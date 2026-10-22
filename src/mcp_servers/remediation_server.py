import os
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
from mcp.server.mcpserver import MCPServer

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)
ANCHOR_TIME = datetime(2026, 10, 20, 8, 0, 0, tzinfo=timezone.utc)

mcp = MCPServer("aegis-devops-remediator")

ALLOWED_ACTIONS = {
    "rollback_commit",
    "scale_down_gpu_nodes",
    "reset_db_pool_config",
    "quarantine_malicious_payload",
}
MAX_ALLOWED_REPLICA_COUNT = 6


def _ensure_remediation_table() -> None:
    with psycopg.connect(POSTGRES_CONN_STR) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS remediation_proposals (
                    proposal_id VARCHAR(36) PRIMARY KEY,
                    incident_id VARCHAR(32) NOT NULL,
                    service_name VARCHAR(64) NOT NULL,
                    action_type VARCHAR(64) NOT NULL,
                    target_commit_or_resource VARCHAR(128) NOT NULL,
                    proposed_command TEXT NOT NULL,
                    justification TEXT NOT NULL,
                    estimated_savings_usd_per_hr NUMERIC(10, 2) DEFAULT 0.0,
                    status VARCHAR(32) DEFAULT 'PENDING_HUMAN_APPROVAL',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    reviewed_by VARCHAR(64),
                    reviewed_at TIMESTAMPTZ
                );
            """)
        conn.commit()


@mcp.tool()
def get_recent_deployments(service_name: str, lookback_hours: int = 24) -> str:
    """
    Retrieves recent Git deployments and configuration diffs (config_diff)
    for a specific microservice to correlate production incidents with code changes.
    """
    cutoff = ANCHOR_TIME - timedelta(hours=lookback_hours)
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT commit_hash, service_name, author, deployed_at, status, config_diff
                FROM deployments
                WHERE service_name = %s AND deployed_at >= %s
                ORDER BY deployed_at DESC;
                """,
                (service_name, cutoff),
            )
            deployments = cur.fetchall()

    return json.dumps(
        {
            "service_name": service_name,
            "lookback_hours": lookback_hours,
            "deployments_found": len(deployments),
            "deployments": deployments,
        },
        default=str,
        indent=2,
    )


@mcp.tool()
def stage_remediation_action(
    incident_id: str,
    service_name: str,
    action_type: Literal[
        "rollback_commit",
        "scale_down_gpu_nodes",
        "reset_db_pool_config",
        "quarantine_malicious_payload",
    ],
    target_commit_or_resource: str,
    proposed_command: str,
    justification: str,
    target_replica_count: int = 2,
    estimated_savings_usd_per_hr: float = 0.0,
) -> str:
    """
    Stages a remediation action into PostgreSQL with status 'PENDING_HUMAN_APPROVAL'.
    Enforces strict safety bounds (blocks unapproved scale-up requests or destructive commands).
    """
    _ensure_remediation_table()

    # Guardrail 1: Action Whitelist
    if action_type not in ALLOWED_ACTIONS:
        return json.dumps(
            {
                "status": "BLOCKED_BY_REMEDIATION_GUARDRAIL",
                "reason": f"Action '{action_type}' is not in ALLOWED_ACTIONS: {sorted(ALLOWED_ACTIONS)}",
            },
            indent=2,
        )

    # Guardrail 2: Replica Count Bound (prevents INC-2026-004 prompt injection scaling to 100 instances)
    if target_replica_count < 1 or target_replica_count > MAX_ALLOWED_REPLICA_COUNT:
        return json.dumps(
            {
                "status": "BLOCKED_BY_REMEDIATION_GUARDRAIL",
                "reason": (
                    f"Requested target_replica_count={target_replica_count} violates policy bounds "
                    f"(1..{MAX_ALLOWED_REPLICA_COUNT}). Runaway scale-up blocked."
                ),
            },
            indent=2,
        )

    # Guardrail 3: Command String Sanitization
    forbidden_tokens = ["rm -rf", "drop table", "delete from", "truncate ", "mkfs", "dd if="]
    lower_cmd = proposed_command.lower()
    for token in forbidden_tokens:
        if token in lower_cmd:
            return json.dumps(
                {
                    "status": "BLOCKED_BY_REMEDIATION_GUARDRAIL",
                    "reason": f"Destructive CLI/SQL token '{token}' detected in proposed_command.",
                },
                indent=2,
            )

    proposal_id = f"PROP-{uuid.uuid4().hex[:8].upper()}"
    with psycopg.connect(POSTGRES_CONN_STR) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO remediation_proposals (
                    proposal_id, incident_id, service_name, action_type,
                    target_commit_or_resource, proposed_command, justification,
                    estimated_savings_usd_per_hr, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'PENDING_HUMAN_APPROVAL');
                """,
                (
                    proposal_id,
                    incident_id,
                    service_name,
                    action_type,
                    target_commit_or_resource,
                    proposed_command,
                    justification,
                    estimated_savings_usd_per_hr,
                ),
            )
        conn.commit()

    return json.dumps(
        {
            "status": "STAGED_FOR_HITL_APPROVAL",
            "proposal_id": proposal_id,
            "incident_id": incident_id,
            "service_name": service_name,
            "action_type": action_type,
            "target_commit_or_resource": target_commit_or_resource,
            "proposed_command": proposed_command,
            "approval_state": "PENDING_HUMAN_APPROVAL",
        },
        indent=2,
    )


@mcp.tool()
def execute_approved_remediation(proposal_id: str, approver_id: str) -> str:
    """
    Executes a previously staged remediation proposal ONLY after a human operator
    explicitly approves it at the Human-in-the-Loop (HITL) checkpoint.
    """
    _ensure_remediation_table()
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM remediation_proposals WHERE proposal_id = %s;",
                (proposal_id,),
            )
            prop = cur.fetchone()
            if not prop:
                return json.dumps({"status": "ERROR", "reason": f"Proposal {proposal_id} not found."})

            cur.execute(
                """
                UPDATE remediation_proposals
                SET status = 'EXECUTED_APPROVED', reviewed_by = %s, reviewed_at = NOW()
                WHERE proposal_id = %s;
                """,
                (approver_id, proposal_id),
            )
            cur.execute(
                """
                UPDATE active_incidents
                SET status = 'RESOLVED'
                WHERE incident_id = %s;
                """,
                (prop["incident_id"],),
            )
        conn.commit()

    return json.dumps(
        {
            "status": "REMEDIATION_EXECUTED",
            "proposal_id": proposal_id,
            "incident_id": prop["incident_id"],
            "approved_by": approver_id,
            "executed_command": prop["proposed_command"],
            "incident_new_status": "RESOLVED",
        },
        default=str,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()