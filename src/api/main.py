from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Literal
import uuid
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import psycopg
from psycopg.rows import dict_row
import duckdb

from src.agents.graph import build_aegis_graph
from src.core.observability import record_agent_trace, load_recent_traces

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "data/telemetry_parquet/aegis_telemetry.duckdb")

# Global compiled LangGraph & PostgreSQL connection managed by FastAPI lifespan
_GRAPH_INSTANCE: Any = None
_PG_CHECKPOINT_CONN: psycopg.Connection | None = None


def get_graph() -> Any:
    global _GRAPH_INSTANCE, _PG_CHECKPOINT_CONN
    if _GRAPH_INSTANCE is None:
        _GRAPH_INSTANCE, _PG_CHECKPOINT_CONN = build_aegis_graph(enable_hitl_interrupt=True)
    return _GRAPH_INSTANCE


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_graph()
    yield
    global _PG_CHECKPOINT_CONN, _GRAPH_INSTANCE
    if _PG_CHECKPOINT_CONN is not None:
        _PG_CHECKPOINT_CONN.close()
        _PG_CHECKPOINT_CONN = None
        _GRAPH_INSTANCE = None


app = FastAPI(
    title="AegisOps Autonomous SRE & FinOps Multi-Agent API",
    version="1.0.0",
    description="Stateful LangGraph Multi-Agent Orchestrator with Custom MCP v2 Servers, Hybrid Reranked Qdrant RAG & PostgreSQL HITL Checkpointing.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HITLDecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"] = Field(
        ..., description="Human operator decision at the HITL checkpoint"
    )
    approver_id: str = Field(
        default="y.laichi", description="Identifier of the SRE/FinOps engineer approving or rejecting"
    )


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "aegisops-agentic-api",
        "mcp_servers": ["aegis-telemetry-finops", "aegis-devops-remediator"],
        "hitl_checkpointer": "PostgresSaver",
    }


@app.get("/incidents")
async def list_incidents() -> Dict[str, Any]:
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM active_incidents ORDER BY incident_id ASC;")
            incidents = cur.fetchall()
            cur.execute(
                """
                SELECT timestamp_hour, service_name, hourly_cost_usd, active_instances, llm_tokens_consumed
                FROM cloud_billing_hourly
                ORDER BY timestamp_hour DESC
                LIMIT 96;
                """
            )
            billing_series = cur.fetchall()

    return {
        "count": len(incidents),
        "incidents": incidents,
        "recent_billing_series": billing_series,
    }


@app.post("/incidents/{incident_id}/investigate")
async def investigate_incident(incident_id: str) -> Dict[str, Any]:
    graph = get_graph()
    thread_id = f"warroom-{incident_id.lower()}-{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        paused_state = graph.invoke({"incident_id": incident_id}, config=config)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    snapshot = graph.get_state(config)
    trace_entry = record_agent_trace(thread_id=thread_id, final_state=paused_state)

    return {
        "thread_id": thread_id,
        "incident_id": incident_id,
        "checkpoint_status": "PAUSED_FOR_HITL" if snapshot.next else "COMPLETED",
        "next_scheduled_node": list(snapshot.next),
        "triage": paused_state.get("triage"),
        "rca_report": paused_state.get("rca_report"),
        "staged_proposal": paused_state.get("staged_proposal"),
        "retrieved_runbooks": paused_state.get("retrieved_runbooks", []),
        "telemetry_evidence": paused_state.get("telemetry_evidence", {}),
        "finops_evidence": paused_state.get("finops_evidence", {}),
        "deployment_evidence": paused_state.get("deployment_evidence", {}),
        "sql_guardrail_events": paused_state.get("sql_guardrail_events", []),
        "llmops_trace": trace_entry,
    }


@app.post("/incidents/threads/{thread_id}/decision")
async def submit_hitl_decision(
    thread_id: str,
    payload: HITLDecisionRequest,
) -> Dict[str, Any]:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"Checkpoint thread '{thread_id}' not found.")

    graph.update_state(
        config,
        {
            "human_approval_status": payload.decision,
            "approver_id": payload.approver_id,
        },
    )
    final_state = graph.invoke(None, config=config)
    trace_entry = record_agent_trace(thread_id=thread_id, final_state=final_state)

    return {
        "thread_id": thread_id,
        "incident_id": final_state.get("incident_id"),
        "human_decision": payload.decision,
        "approver_id": payload.approver_id,
        "execution_result": final_state.get("execution_result"),
        "llmops_trace": trace_entry,
    }


@app.get("/telemetry/{service_name}")
async def get_service_telemetry_overview(service_name: str) -> Dict[str, Any]:
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        hourly_rows = con.execute(
            """
            SELECT
                 date_trunc('hour', timestamp) AS hour_bucket,
                ROUND(QUANTILE_CONT(latency_ms, 0.99), 2) AS p99_latency_ms,
                ROUND(MAX(memory_usage_mb), 1) AS peak_memory_mb,
                ROUND(MAX(cpu_usage_pct), 1) AS peak_cpu_pct,
                SUM(CASE WHEN error_code != 'NONE' THEN 1 ELSE 0 END) AS error_count
            FROM telemetry_logs
            WHERE service_name = ?
            GROUP BY 1
            ORDER BY 1 ASC;
            """,
            [service_name],
        ).fetchall()
    finally:
        con.close()

    return {
        "service_name": service_name,
        "hourly_buckets": [
            {
                "hour_bucket": str(r[0]),
                "p99_latency_ms": r[1],
                "peak_memory_mb": r[2],
                "peak_cpu_pct": r[3],
                "error_count": r[4],
            }
            for r in hourly_rows
        ],
    }


@app.get("/observability/benchmarks")
async def get_observability_and_benchmarks() -> Dict[str, Any]:
    rag_bench_path = Path("data/eval_rag_benchmark_results.json")
    agent_bench_path = Path("data/eval_agent_ragas_benchmark.json")

    rag_bench = (
        json.loads(rag_bench_path.read_text(encoding="utf-8"))
        if rag_bench_path.exists()
        else {}
    )
    agent_bench = (
        json.loads(agent_bench_path.read_text(encoding="utf-8"))
        if agent_bench_path.exists()
        else {}
    )

    return {
        "rag_benchmark": rag_bench,
        "agent_ragas_benchmark": agent_bench,
        "recent_traces": load_recent_traces(limit=20),
    }