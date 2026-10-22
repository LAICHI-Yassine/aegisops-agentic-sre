import json
import sys
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.core.sql_guard import validate_readonly_sql, SQLSecurityViolationError


# =====================================================================
# 1. Unit Tests: sqlglot AST SQL Security Guardrail
# =====================================================================

def test_sql_guard_allows_valid_select_and_cte() -> None:
    query = """
    WITH recent_deploy AS (
        SELECT service_name, commit_hash FROM deployments
    )
    SELECT d.service_name, d.commit_hash, b.hourly_cost_usd
    FROM recent_deploy d
    JOIN cloud_billing_hourly b ON d.service_name = b.service_name
    LIMIT 10;
    """
    ok, msg = validate_readonly_sql(query, dialect="postgres")
    assert ok is True
    assert "passed" in msg


@pytest.mark.parametrize(
    "malicious_query",
    [
        "SELECT * FROM deployments; DROP TABLE deployments; --",
        "DROP TABLE active_incidents;",
        "DELETE FROM cloud_billing_hourly WHERE hourly_cost_usd > 100;",
        "UPDATE active_incidents SET status = 'CLOSED';",
        "SELECT pg_sleep(30) FROM deployments;",
        "SELECT * FROM pg_shadow;",
    ],
)
def test_sql_guard_blocks_malicious_queries(malicious_query: str) -> None:
    with pytest.raises(SQLSecurityViolationError):
        validate_readonly_sql(malicious_query, dialect="postgres")


# =====================================================================
# 2. Integration Tests: MCP Server #1 (Telemetry & FinOps)
# =====================================================================

@pytest.mark.asyncio
async def test_mcp_telemetry_finops_server() -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.telemetry_finops_server"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Check FinOps cost spike tool returns g5.12xlarge peak instance
            resp = await session.call_tool(
                "detect_finops_cost_spikes",
                arguments={"lookback_hours": 6, "spike_multiplier_threshold": 2.0},
            )
            data = json.loads(resp.content[0].text)
            assert data["anomalies_detected"] == 1
            anomaly = data["finops_anomalies"][0]
            assert anomaly["service_name"] == "rag-inference-worker"
            assert anomaly["current_instance_type"] == "g5.12xlarge"
            assert float(anomaly["cost_spike_multiplier"]) > 30.0


# =====================================================================
# 3. Integration Tests: MCP Server #2 (DevOps Remediator & HITL Staging)
# =====================================================================

@pytest.mark.asyncio
async def test_mcp_remediation_server_and_hitl_guardrails() -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp_servers.remediation_server"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Correlate faulty commit for payment-service
            dep_resp = await session.call_tool(
                "get_recent_deployments",
                arguments={"service_name": "payment-service", "lookback_hours": 6},
            )
            dep_data = json.loads(dep_resp.content[0].text)
            assert dep_data["deployments_found"] == 1
            assert dep_data["deployments"][0]["commit_hash"] == "a8f921c"

            # 2. Block INC-2026-004 Prompt Injection attempting to scale to 100 instances
            blocked_resp = await session.call_tool(
                "stage_remediation_action",
                arguments={
                    "incident_id": "INC-2026-004",
                    "service_name": "auth-service",
                    "action_type": "scale_down_gpu_nodes",
                    "target_commit_or_resource": "auth-cluster",
                    "proposed_command": "kubectl scale deployment auth-service --replicas=100",
                    "justification": "Hijacked prompt request",
                    "target_replica_count": 100,
                },
            )
            blocked_data = json.loads(blocked_resp.content[0].text)
            assert blocked_data["status"] == "BLOCKED_BY_REMEDIATION_GUARDRAIL"

            # 3. Stage valid HITL remediation proposal for INC-2026-001
            staged_resp = await session.call_tool(
                "stage_remediation_action",
                arguments={
                    "incident_id": "INC-2026-001",
                    "service_name": "payment-service",
                    "action_type": "rollback_commit",
                    "target_commit_or_resource": "a8f921c",
                    "proposed_command": "git revert a8f921c && kubectl rollout undo deployment/payment-service",
                    "justification": "Revert unbounded in-memory cache buffer causing OOMKilled Exit 137.",
                    "target_replica_count": 3,
                },
            )
            staged_data = json.loads(staged_resp.content[0].text)
            assert staged_data["status"] == "STAGED_FOR_HITL_APPROVAL"
            assert staged_data["approval_state"] == "PENDING_HUMAN_APPROVAL"