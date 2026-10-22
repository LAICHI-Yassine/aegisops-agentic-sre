import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from scripts.seed_chaos_data import seed_postgresql


@pytest.fixture(scope="module", autouse=True)
def reset_db():
    seed_postgresql()
    yield
    seed_postgresql()


def test_fastapi_health_and_incidents_list() -> None:
    with TestClient(app) as client:
        h_resp = client.get("/health")
        assert h_resp.status_code == 200
        assert h_resp.json()["status"] == "healthy"

        inc_resp = client.get("/incidents")
        assert inc_resp.status_code == 200
        data = inc_resp.json()
        assert data["count"] == 4


def test_fastapi_investigate_and_hitl_approval_flow() -> None:
    with TestClient(app) as client:
        # 1. Trigger investigation on INC-2026-002 (FinOps GPU anomaly)
        inv_resp = client.post("/incidents/INC-2026-002/investigate")
        assert inv_resp.status_code == 200
        inv_data = inv_resp.json()

        assert inv_data["checkpoint_status"] == "PAUSED_FOR_HITL"
        assert inv_data["next_scheduled_node"] == ["execute_remediation"]
        assert inv_data["rca_report"]["offending_commit_hash"] == "f4e119b"
        thread_id = inv_data["thread_id"]

        # 2. Submit HITL Approval Decision
        dec_resp = client.post(
            f"/incidents/threads/{thread_id}/decision",
            json={"decision": "APPROVED", "approver_id": "y.laichi"},
        )
        assert dec_resp.status_code == 200
        dec_data = dec_resp.json()
        assert dec_data["execution_result"]["status"] == "REMEDIATION_EXECUTED"
        assert dec_data["execution_result"]["incident_new_status"] == "RESOLVED"


def test_fastapi_telemetry_and_benchmarks_endpoints() -> None:
    with TestClient(app) as client:
        t_resp = client.get("/telemetry/payment-service")
        assert t_resp.status_code == 200
        assert len(t_resp.json()["hourly_buckets"]) > 0

        b_resp = client.get("/observability/benchmarks")
        assert b_resp.status_code == 200
        b_data = b_resp.json()
        assert "rag_benchmark" in b_data
        assert "agent_ragas_benchmark" in b_data