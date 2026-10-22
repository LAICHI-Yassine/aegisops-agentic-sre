from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Dict, List

TRACE_LOG_PATH = Path("data/llmops_traces.jsonl")
# Reference token pricing ($0.15 per 1M input / $0.60 per 1M output blended ~$0.0003 per 1K tokens)
BLENDED_COST_PER_1K_TOKENS_USD = 0.0003


def record_agent_trace(
    thread_id: str,
    final_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Persists a structured Langfuse-compatible LLMOps trace containing node latencies,
    token consumption, cost estimation, RAG cache status, and guardrail events.
    """
    steps: List[Dict[str, Any]] = final_state.get("trace_steps", [])
    total_tokens = sum(int(s.get("tokens_used", 0)) for s in steps)
    total_latency_ms = round(sum(float(s.get("latency_ms", 0.0)) for s in steps), 2)
    estimated_cost_usd = round((total_tokens / 1000.0) * BLENDED_COST_PER_1K_TOKENS_USD, 6)

    rca = final_state.get("rca_report", {})
    triage = final_state.get("triage", {})

    trace_entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "incident_id": final_state.get("incident_id", "UNKNOWN"),
        "service_name": triage.get("service_name", "UNKNOWN"),
        "domain": triage.get("domain", "UNKNOWN"),
        "severity": triage.get("severity", "UNKNOWN"),
        "total_nodes_executed": len(steps),
        "total_latency_ms": total_latency_ms,
        "total_tokens": total_tokens,
        "estimated_llm_cost_usd": estimated_cost_usd,
        "rag_cache_hit": final_state.get("rag_cache_hit", False),
        "rag_latency_ms": final_state.get("rag_latency_ms", 0.0),
        "cited_runbook": rca.get("cited_runbook_doc_id", "NONE"),
        "offending_commit": rca.get("offending_commit_hash", "NONE"),
        "recommended_action": rca.get("recommended_action_type", "NONE"),
        "confidence_score": rca.get("confidence_score", 0.0),
        "security_guardrail_triggered": rca.get("security_guardrail_triggered", False),
        "human_approval_status": final_state.get("human_approval_status", "PENDING_HUMAN_APPROVAL"),
        "steps": steps,
    }

    TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(trace_entry) + "\n")

    return trace_entry


def load_recent_traces(limit: int = 25) -> List[Dict[str, Any]]:
    if not TRACE_LOG_PATH.exists():
        return []
    lines = TRACE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    parsed = [json.loads(line) for line in lines if line.strip()]
    return list(reversed(parsed[-limit:]))