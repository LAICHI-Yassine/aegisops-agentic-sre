import uuid
import pytest
from pydantic import ValidationError

from src.agents.schemas import IncidentRCAReport
from src.agents.graph import build_aegis_graph
from scripts.seed_chaos_data import seed_postgresql


@pytest.fixture(scope="module", autouse=True)
def reset_postgres_incidents():
    seed_postgresql()
    yield
    seed_postgresql()


def test_pydantic_rca_schema_blocks_unsafe_commands() -> None:
    with pytest.raises(ValidationError):
        IncidentRCAReport(
            incident_id="INC-2026-004",
            service_name="auth-service",
            severity="P2-HIGH",
            domain="SECURITY_TEST",
            root_cause_summary="Attempted SQL injection and replica overflow.",
            offending_commit_hash="NONE",
            key_telemetry_evidence=["Injected command"],
            cited_runbook_doc_id="RB-SEC-004",
            recommended_action_type="quarantine_malicious_payload",
            proposed_cli_command="DROP TABLE deployments; kubectl scale --replicas=100",
            target_replica_count=100,
            confidence_score=0.95,
        )


def test_langgraph_hitl_pause_and_approval_execution() -> None:
    graph, pg_conn = build_aegis_graph(enable_hitl_interrupt=True)
    try:
        cfg = {"configurable": {"thread_id": f"pytest-oom-{uuid.uuid4().hex[:6]}"}}
        paused_state = graph.invoke({"incident_id": "INC-2026-001"}, config=cfg)

        # 1. Verify graph paused before execute_remediation
        snapshot = graph.get_state(cfg)
        assert snapshot.next == ("execute_remediation",)
        assert paused_state["rca_report"]["offending_commit_hash"] == "a8f921c"
        assert paused_state["rca_report"]["cited_runbook_doc_id"] == "RB-SRE-001-kubernetes-oomkilled-exit137"
        assert paused_state["human_approval_status"] == "PENDING_HUMAN_APPROVAL"

        # 2. Approve and resume
        graph.update_state(
            cfg,
            {"human_approval_status": "APPROVED", "approver_id": "pytest_sre"},
        )
        final_state = graph.invoke(None, config=cfg)
        assert final_state["execution_result"]["status"] == "REMEDIATION_EXECUTED"
        assert final_state["execution_result"]["incident_new_status"] == "RESOLVED"
    finally:
        pg_conn.close()


def test_langgraph_hitl_rejection_blocks_execution() -> None:
    graph, pg_conn = build_aegis_graph(enable_hitl_interrupt=True)
    try:
        cfg = {"configurable": {"thread_id": f"pytest-db-{uuid.uuid4().hex[:6]}"}}
        paused_state = graph.invoke({"incident_id": "INC-2026-003"}, config=cfg)
        assert paused_state["rca_report"]["offending_commit_hash"] == "d9104ea"

        # Reject the staged proposal at HITL gate
        graph.update_state(
            cfg,
            {"human_approval_status": "REJECTED", "approver_id": "pytest_reviewer"},
        )
        final_state = graph.invoke(None, config=cfg)
        assert final_state["execution_result"]["status"] == "REMEDIATION_REJECTED_BY_HUMAN"
    finally:
        pg_conn.close()


def test_langgraph_adversarial_incident_quarantine() -> None:
    graph, pg_conn = build_aegis_graph(enable_hitl_interrupt=True)
    try:
        cfg = {"configurable": {"thread_id": f"pytest-sec-{uuid.uuid4().hex[:6]}"}}
        sec_state = graph.invoke({"incident_id": "INC-2026-004"}, config=cfg)

        rca = sec_state["rca_report"]
        assert rca["security_guardrail_triggered"] is True
        assert rca["recommended_action_type"] == "quarantine_malicious_payload"
        assert sec_state["sql_guardrail_events"][0]["status"] == "BLOCKED_BY_AST_SECURITY_GUARD"
    finally:
        pg_conn.close()