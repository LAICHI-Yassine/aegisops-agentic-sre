from typing import List, Literal
from pydantic import BaseModel, Field, field_validator


class TriageClassification(BaseModel):
    """Structured output produced by Node 1 (Triage & Alert Router Agent)."""

    incident_id: str = Field(..., description="Unique incident identifier (e.g., INC-2026-001)")
    service_name: str = Field(..., description="Target microservice under investigation")
    severity: Literal["P1-CRITICAL", "P2-HIGH", "P3-WARN"] = Field(
        ..., description="Classified incident severity"
    )
    domain: Literal["SRE_INFRA", "FINOPS_COST", "SECURITY_TEST"] = Field(
        ..., description="Primary operational domain for routing"
    )
    contains_adversarial_pattern: bool = Field(
        default=False,
        description="True if alert payload contains prompt injection or SQL injection patterns",
    )
    investigation_plan: List[str] = Field(
        ...,
        min_length=2,
        description="Ordered list of MCP and RAG diagnostic steps to execute",
    )


class CriticAssessment(BaseModel):
    """Structured output produced by Node 4 (Self-Reflection & Hallucination Critic)."""

    is_evidence_sufficient: bool = Field(
        ...,
        description="True only if telemetry metrics, git commit diffs, and runbook citations align",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Calibrated confidence score between 0.0 and 1.0",
    )
    hallucination_or_unsafe_cmd_detected: bool = Field(
        default=False,
        description="True if proposed remediation contains unverified or destructive commands",
    )
    critique_reasoning: str = Field(
        ...,
        description="Explanation of why evidence passed or what requires re-querying",
    )


class IncidentRCAReport(BaseModel):
    """
    Final deterministic Root Cause Analysis (RCA) and HITL Remediation Plan
    validated by Pydantic v2 / Instructor before human sign-off.
    """

    incident_id: str
    service_name: str
    severity: Literal["P1-CRITICAL", "P2-HIGH", "P3-WARN"]
    domain: Literal["SRE_INFRA", "FINOPS_COST", "SECURITY_TEST"]
    root_cause_summary: str = Field(
        ...,
        min_length=20,
        description="Technical Root Cause Analysis grounded in telemetry and deployment evidence",
    )
    offending_commit_hash: str = Field(
        ...,
        description="Git commit hash correlated with the anomaly, or 'NONE_MALICIOUS_PAYLOAD'",
    )
    key_telemetry_evidence: List[str] = Field(
        ...,
        min_length=1,
        description="Exact quantitative metrics from DuckDB / PostgreSQL supporting the RCA",
    )
    cited_runbook_doc_id: str = Field(
        ...,
        description="Document ID of the top reranked engineering runbook (e.g., RB-SRE-001...)",
    )
    estimated_hourly_burn_overrun_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Hourly cloud spend overrun in USD/hr (for FinOps anomalies)",
    )
    recommended_action_type: Literal[
        "rollback_commit",
        "scale_down_gpu_nodes",
        "reset_db_pool_config",
        "quarantine_malicious_payload",
    ]
    proposed_cli_command: str = Field(
        ...,
        min_length=5,
        description="Exact CLI/Git/SQL remediation command to be executed upon HITL approval",
    )
    target_replica_count: int = Field(
        default=2,
        ge=1,
        le=6,
        description="Target replica count bounded between 1 and 6 by safety policy",
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    security_guardrail_triggered: bool = Field(
        default=False,
        description="True if sqlglot AST or prompt-injection guardrails blocked an attack",
    )

    @field_validator("proposed_cli_command")
    @classmethod
    def block_destructive_commands(cls, v: str) -> str:
        lower = v.lower()
        for forbidden in ("drop table", "delete from", "rm -rf", "truncate ", "--replicas=100"):
            if forbidden in lower:
                raise ValueError(f"Unsafe token '{forbidden}' blocked by Pydantic schema guardrail.")
        return v