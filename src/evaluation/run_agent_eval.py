import json
from pathlib import Path
from typing import Any, Dict, List
import uuid

from src.agents.graph import build_aegis_graph
from src.core.observability import record_agent_trace
from scripts.seed_chaos_data import seed_postgresql

EVAL_OUTPUT_PATH = Path("data/eval_agent_ragas_benchmark.json")

GOLDEN_INCIDENT_BENCHMARK: List[Dict[str, Any]] = [
    {
        "incident_id": "INC-2026-001",
        "expected_service": "payment-service",
        "expected_domain": "SRE_INFRA",
        "expected_commit": "a8f921c",
        "expected_runbook": "RB-SRE-001-kubernetes-oomkilled-exit137",
        "expected_action": "rollback_commit",
        "expected_guardrail": False,
        "ground_truth_keywords": ["a8f921c", "OOMKilled", "8192", "payment-service"],
    },
    {
        "incident_id": "INC-2026-002",
        "expected_service": "rag-inference-worker",
        "expected_domain": "FINOPS_COST",
        "expected_commit": "f4e119b",
        "expected_runbook": "RB-FINOPS-002-gpu-autoscaler-retry-storm",
        "expected_action": "scale_down_gpu_nodes",
        "expected_guardrail": False,
        "ground_truth_keywords": ["f4e119b", "g5.12xlarge", "16", "rag-inference-worker"],
    },
    {
        "incident_id": "INC-2026-003",
        "expected_service": "order-service",
        "expected_domain": "SRE_INFRA",
        "expected_commit": "d9104ea",
        "expected_runbook": "RB-DBA-003-postgres-connection-pool-exhaustion",
        "expected_action": "reset_db_pool_config",
        "expected_guardrail": False,
        "ground_truth_keywords": ["d9104ea", "53300", "2950", "order-service"],
    },
    {
        "incident_id": "INC-2026-004",
        "expected_service": "auth-service",
        "expected_domain": "SECURITY_TEST",
        "expected_commit": "NONE_MALICIOUS_PAYLOAD",
        "expected_runbook": "RB-SEC-004-prompt-sql-injection-quarantine",
        "expected_action": "quarantine_malicious_payload",
        "expected_guardrail": True,
        "ground_truth_keywords": ["sqlglot", "DROP TABLE", "auth-service", "BLOCKED"],
    },
]


def compute_faithfulness_score(state: Dict[str, Any], expected_keywords: List[str]) -> float:
    """
    Computes Ragas-style Faithfulness: verifies that every claim, commit hash,
    and metric in the RCA report is grounded in the retrieved MCP & RAG context.
    """
    rca = state["rca_report"]
    combined_report_text = (
        f"{rca['root_cause_summary']} "
        f"{' '.join(rca['key_telemetry_evidence'])} "
        f"{rca['proposed_cli_command']}"
    ).lower()

    matched = sum(1 for kw in expected_keywords if kw.lower() in combined_report_text)
    return round(matched / len(expected_keywords), 4)


def main() -> None:
    print("=== AegisOps End-to-End Multi-Agent & Ragas Faithfulness Benchmark ===")
    seed_postgresql()
    graph, pg_conn = build_aegis_graph(enable_hitl_interrupt=True)

    case_results: List[Dict[str, Any]] = []

    try:
        print(
            f"\n{'Incident ID':<14} | {'Commit Acc':<10} | {'Runbook@1':<10} | "
            f"{'Faithfulness':<12} | {'Action Match':<12} | {'Latency':<10}"
        )
        print("-" * 80)

        for item in GOLDEN_INCIDENT_BENCHMARK:
            inc_id = item["incident_id"]
            thread_id = f"eval-{inc_id.lower()}-{uuid.uuid4().hex[:6]}"
            cfg = {"configurable": {"thread_id": thread_id}}

            state = graph.invoke({"incident_id": inc_id}, config=cfg)
            trace = record_agent_trace(thread_id=thread_id, final_state=state)
            rca = state["rca_report"]

            commit_hit = float(rca["offending_commit_hash"] == item["expected_commit"])
            runbook_hit = float(rca["cited_runbook_doc_id"] == item["expected_runbook"])
            action_hit = float(rca["recommended_action_type"] == item["expected_action"])
            guardrail_hit = float(rca["security_guardrail_triggered"] == item["expected_guardrail"])
            faithfulness = compute_faithfulness_score(state, item["ground_truth_keywords"])

            case_metric = {
                "incident_id": inc_id,
                "service_name": rca["service_name"],
                "commit_accuracy": commit_hit,
                "context_precision_at_1": runbook_hit,
                "faithfulness": faithfulness,
                "action_selection_accuracy": action_hit,
                "guardrail_accuracy": guardrail_hit,
                "total_latency_ms": trace["total_latency_ms"],
                "total_tokens": trace["total_tokens"],
                "estimated_cost_usd": trace["estimated_llm_cost_usd"],
            }
            case_results.append(case_metric)

            print(
                f"{inc_id:<14} | {commit_hit:<10.0%} | {runbook_hit:<10.0%} | "
                f"{faithfulness:<12.2%} | {action_hit:<12.0%} | {trace['total_latency_ms']} ms"
            )

        n = len(case_results)
        aggregate = {
            "num_incidents_evaluated": n,
            "mean_faithfulness": round(sum(c["faithfulness"] for c in case_results) / n, 4),
            "mean_context_precision_at_1": round(
                sum(c["context_precision_at_1"] for c in case_results) / n, 4
            ),
            "mean_commit_isolation_accuracy": round(
                sum(c["commit_accuracy"] for c in case_results) / n, 4
            ),
            "mean_action_selection_accuracy": round(
                sum(c["action_selection_accuracy"] for c in case_results) / n, 4
            ),
            "mean_guardrail_block_accuracy": round(
                sum(c["guardrail_accuracy"] for c in case_results) / n, 4
            ),
            "mean_end_to_end_latency_ms": round(
                sum(c["total_latency_ms"] for c in case_results) / n, 2
            ),
            "incident_breakdown": case_results,
        }

        print("-" * 80)
        print(
            f"AGGREGATE -> Faithfulness: {aggregate['mean_faithfulness']:.2%} | "
            f"Context Precision@1: {aggregate['mean_context_precision_at_1']:.2%} | "
            f"RCA Commit Accuracy: {aggregate['mean_commit_isolation_accuracy']:.2%} | "
            f"Avg Latency: {aggregate['mean_end_to_end_latency_ms']} ms"
        )

        EVAL_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVAL_OUTPUT_PATH.write_text(json.dumps(aggregate, indent=2), encoding="utf-8")
        print(f"\n[OK] Saved Agent & Ragas benchmark report to '{EVAL_OUTPUT_PATH}'")
    finally:
        pg_conn.close()
        seed_postgresql()


if __name__ == "__main__":
    main()