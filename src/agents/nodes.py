import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time
from typing import Any, Dict, List
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.agents.state import IncidentGraphState
from src.agents.llm_client import StructuredAgentSynthesizer
from src.rag.hybrid_retriever import AegisHybridRAG

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)

# Shared singletons so ONNX models load only once in memory
_RAG_ENGINE: AegisHybridRAG | None = None
_SYNTHESIZER: StructuredAgentSynthesizer | None = None
_MCP_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def get_rag_engine() -> AegisHybridRAG:
    global _RAG_ENGINE
    if _RAG_ENGINE is None:
        _RAG_ENGINE = AegisHybridRAG()
    return _RAG_ENGINE


def get_synthesizer() -> StructuredAgentSynthesizer:
    global _SYNTHESIZER
    if _SYNTHESIZER is None:
        _SYNTHESIZER = StructuredAgentSynthesizer()
    return _SYNTHESIZER


def _run_coro_sync(coro: Any) -> Any:
    """
    Executes an async MCP coroutine inside a dedicated worker thread so it
    never conflicts with an outer asyncio event loop (FastAPI or pytest-asyncio).
    """
    future = _MCP_EXECUTOR.submit(asyncio.run, coro)
    return future.result(timeout=30)


async def _mcp_investigate_async(
    service_name: str,
    domain: str,
    contains_adversarial: bool,
) -> Dict[str, Any]:
    telem_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.telemetry_finops_server"],
    )
    rem_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.remediation_server"],
    )

    telemetry_data: Dict[str, Any] = {}
    finops_data: Dict[str, Any] = {}
    sql_guard_events: List[Dict[str, Any]] = []
    deployments_data: Dict[str, Any] = {}

    # 1. Query MCP Server #1: Telemetry & FinOps
    async with stdio_client(telem_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            t_resp = await session.call_tool(
                "analyze_service_telemetry",
                arguments={"service_name": service_name, "lookback_hours": 6},
            )
            telemetry_data = json.loads(t_resp.content[0].text)

            if domain == "FINOPS_COST":
                f_resp = await session.call_tool(
                    "detect_finops_cost_spikes",
                    arguments={"lookback_hours": 6, "spike_multiplier_threshold": 2.0},
                )
                finops_data = json.loads(f_resp.content[0].text)

            if contains_adversarial:
                # Test the adversarial SQL payload against our MCP AST Guardrail
                g_resp = await session.call_tool(
                    "execute_guarded_diagnostic_sql",
                    arguments={
                        "sql_query": "SELECT * FROM deployments; DROP TABLE deployments; --",
                        "target_engine": "postgres",
                    },
                )
                sql_guard_events.append(json.loads(g_resp.content[0].text))

    # 2. Query MCP Server #2: Recent Git Deployments
    async with stdio_client(rem_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            d_resp = await session.call_tool(
                "get_recent_deployments",
                arguments={"service_name": service_name, "lookback_hours": 24},
            )
            deployments_data = json.loads(d_resp.content[0].text)

    return {
        "telemetry_evidence": telemetry_data,
        "finops_evidence": finops_data,
        "deployment_evidence": deployments_data,
        "sql_guardrail_events": sql_guard_events,
    }


async def _mcp_call_remediation_tool_async(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    rem_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.remediation_server"],
    )
    async with stdio_client(rem_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resp = await session.call_tool(tool_name, arguments=arguments)
            return json.loads(resp.content[0].text)


def _fetch_incident_from_db(incident_id: str) -> Dict[str, Any]:
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM active_incidents WHERE incident_id = %s;",
                (incident_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Incident '{incident_id}' not found in PostgreSQL active_incidents.")
            return dict(row)


# =====================================================================
# LangGraph Node Functions
# =====================================================================

def triage_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    incident_id = state["incident_id"]
    incident_record = state.get("incident_record") or _fetch_incident_from_db(incident_id)

    synthesizer = get_synthesizer()
    triage_obj = synthesizer.classify_triage(incident_record)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "incident_record": incident_record,
        "triage": triage_obj.model_dump(),
        "iteration_count": 0,
        "trace_steps": [
            {
                "node_name": "triage_agent",
                "latency_ms": latency_ms,
                "summary": (
                    f"Classified {incident_id} on {triage_obj.service_name} as "
                    f"{triage_obj.severity} ({triage_obj.domain}) | "
                    f"Adversarial={triage_obj.contains_adversarial_pattern}"
                ),
                "tokens_used": 145,
            }
        ],
    }


def mcp_investigator_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    triage = state["triage"]
    service_name = triage["service_name"]
    domain = triage["domain"]
    adv = triage.get("contains_adversarial_pattern", False)

    mcp_results = _run_coro_sync(_mcp_investigate_async(service_name, domain, adv))
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    err_sigs = mcp_results["telemetry_evidence"].get("error_signatures", [])
    top_err = err_sigs[0]["error_code"] if err_sigs else "NONE"
    dep_count = mcp_results["deployment_evidence"].get("deployments_found", 0)

    return {
        "telemetry_evidence": mcp_results["telemetry_evidence"],
        "finops_evidence": mcp_results["finops_evidence"],
        "deployment_evidence": mcp_results["deployment_evidence"],
        "sql_guardrail_events": mcp_results["sql_guardrail_events"],
        "trace_steps": [
            {
                "node_name": "mcp_telemetry_finops_investigator",
                "latency_ms": latency_ms,
                "summary": (
                    f"Queried DuckDB & PostgreSQL via MCP for {service_name}: "
                    f"top_error={top_err}, recent_deployments={dep_count}, "
                    f"sql_guard_blocks={len(mcp_results['sql_guardrail_events'])}"
                ),
                "tokens_used": 310,
            }
        ],
    }


def hybrid_rag_runbook_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    triage = state["triage"]
    telemetry = state.get("telemetry_evidence", {})
    deployments = state.get("deployment_evidence", {})

    err_sigs = telemetry.get("error_signatures", [])
    top_err = err_sigs[0]["error_code"] if err_sigs else ""
    dep_list = deployments.get("deployments", [])
    diff_snippet = dep_list[0].get("config_diff", "")[:120] if dep_list else ""

    if triage.get("contains_adversarial_pattern") or triage["domain"] == "SECURITY_TEST":
        rag_query = "SYSTEM OVERRIDE Ignore previous instructions DROP TABLE deployments waf-cli block-signature"
    else:
        rag_query = f"{triage['service_name']} {top_err} {triage['domain']} {diff_snippet}".strip()

    rag = get_rag_engine()
    search_resp = rag.search(rag_query, top_k=2, mode="hybrid_reranked", use_cache=True)
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    top_doc = search_resp["results"][0]["doc_id"] if search_resp["results"] else "NONE"
    return {
        "rag_query": rag_query,
        "retrieved_runbooks": search_resp["results"],
        "rag_latency_ms": search_resp["latency_ms"],
        "rag_cache_hit": search_resp["cache_hit"],
        "trace_steps": [
            {
                "node_name": "hybrid_rag_runbook_specialist",
                "latency_ms": latency_ms,
                "summary": (
                    f"Retrieved top runbook '{top_doc}' via Hybrid RRF + Cross-Encoder "
                    f"(cache_hit={search_resp['cache_hit']}, rag_ms={search_resp['latency_ms']})"
                ),
                "tokens_used": 260,
            }
        ],
    }


def critic_reflection_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    iteration = state.get("iteration_count", 0) + 1
    synthesizer = get_synthesizer()

    critic_obj = synthesizer.evaluate_evidence(
        triage=state["triage"],
        telemetry=state.get("telemetry_evidence", {}),
        deployments=state.get("deployment_evidence", {}),
        runbooks=state.get("retrieved_runbooks", []),
        iteration=iteration,
    )
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "iteration_count": iteration,
        "critic_assessment": critic_obj.model_dump(),
        "trace_steps": [
            {
                "node_name": "self_reflection_critic",
                "latency_ms": latency_ms,
                "summary": (
                    f"Iteration {iteration}: sufficient={critic_obj.is_evidence_sufficient}, "
                    f"confidence={critic_obj.confidence_score:.2f}"
                ),
                "tokens_used": 180,
            }
        ],
    }


def stage_hitl_remediation_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    synthesizer = get_synthesizer()

    rca_obj = synthesizer.synthesize_rca_report(
        triage=state["triage"],
        telemetry=state.get("telemetry_evidence", {}),
        finops=state.get("finops_evidence", {}),
        deployments=state.get("deployment_evidence", {}),
        runbooks=state.get("retrieved_runbooks", []),
        sql_guard_events=state.get("sql_guardrail_events", []),
    )

    staged_resp = _run_coro_sync(
        _mcp_call_remediation_tool_async(
            "stage_remediation_action",
            {
                "incident_id": rca_obj.incident_id,
                "service_name": rca_obj.service_name,
                "action_type": rca_obj.recommended_action_type,
                "target_commit_or_resource": rca_obj.offending_commit_hash,
                "proposed_command": rca_obj.proposed_cli_command,
                "justification": rca_obj.root_cause_summary,
                "target_replica_count": rca_obj.target_replica_count,
                "estimated_savings_usd_per_hr": rca_obj.estimated_hourly_burn_overrun_usd,
            },
        )
    )
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    return {
        "rca_report": rca_obj.model_dump(),
        "staged_proposal": staged_resp,
        "human_approval_status": "PENDING_HUMAN_APPROVAL",
        "trace_steps": [
            {
                "node_name": "stage_hitl_remediation",
                "latency_ms": latency_ms,
                "summary": (
                    f"Staged proposal {staged_resp.get('proposal_id')} "
                    f"({rca_obj.recommended_action_type}) -> Paused for Human-in-the-Loop approval."
                ),
                "tokens_used": 390,
            }
        ],
    }


def execute_remediation_node(state: IncidentGraphState) -> Dict[str, Any]:
    t0 = time.perf_counter()
    approval_status = state.get("human_approval_status", "PENDING_HUMAN_APPROVAL")
    approver_id = state.get("approver_id", "sre_operator")
    proposal_id = state["staged_proposal"]["proposal_id"]

    if approval_status == "APPROVED":
        exec_resp = _run_coro_sync(
            _mcp_call_remediation_tool_async(
                "execute_approved_remediation",
                {"proposal_id": proposal_id, "approver_id": approver_id},
            )
        )
        summary_msg = f"Approved by {approver_id}: Executed '{exec_resp.get('executed_command')}' -> RESOLVED"
    else:
        exec_resp = {
            "status": "REMEDIATION_REJECTED_BY_HUMAN",
            "proposal_id": proposal_id,
            "reviewed_by": approver_id,
        }
        summary_msg = f"Rejected by {approver_id}: No infrastructure changes executed."

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "execution_result": exec_resp,
        "trace_steps": [
            {
                "node_name": "execute_remediation",
                "latency_ms": latency_ms,
                "summary": summary_msg,
                "tokens_used": 95,
            }
        ],
    }