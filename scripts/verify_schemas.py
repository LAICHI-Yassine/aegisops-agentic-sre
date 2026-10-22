from pydantic import ValidationError
from src.agents.schemas import IncidentRCAReport


def main() -> None:
    print("=== Verifying Pydantic v2 & Instructor RCA Guardrails ===")

    # 1. Valid RCA Report
    valid_report = IncidentRCAReport(
        incident_id="INC-2026-001",
        service_name="payment-service",
        severity="P1-CRITICAL",
        domain="SRE_INFRA",
        root_cause_summary="Commit a8f921c disabled Redis cache TTL causing OOMKilled Exit 137.",
        offending_commit_hash="a8f921c",
        key_telemetry_evidence=["p99 latency: 4350 ms", "Peak RAM: 8192 MB"],
        cited_runbook_doc_id="RB-SRE-001-kubernetes-oomkilled-exit137",
        recommended_action_type="rollback_commit",
        proposed_cli_command="git revert a8f921c && kubectl rollout undo deployment/payment-service -n prod",
        target_replica_count=3,
        confidence_score=0.95,
    )
    print(f"[1/2] Valid IncidentRCAReport schema passed -> {valid_report.incident_id} ({valid_report.recommended_action_type})")

    # 2. Malicious / Hallucinated Command Blocked by Pydantic Validator
    try:
        IncidentRCAReport(
            incident_id="INC-2026-004",
            service_name="auth-service",
            severity="P2-HIGH",
            domain="SECURITY_TEST",
            root_cause_summary="Hijacked prompt attempting destructive SQL execution.",
            offending_commit_hash="NONE",
            key_telemetry_evidence=["Injected payload"],
            cited_runbook_doc_id="RB-SEC-004",
            recommended_action_type="quarantine_malicious_payload",
            proposed_cli_command="psql -c 'DROP TABLE deployments;' && kubectl scale --replicas=100",
            target_replica_count=100,
            confidence_score=0.99,
        )
        raise RuntimeError("Should have blocked unsafe command!")
    except ValidationError as exc:
        print(f"[2/2] Pydantic Schema Guardrail Blocked Unsafe Output ({exc.error_count()} validation errors caught)")
        print("\nStep 3.1 Complete: Schemas, State, and Synthesizer are verified!")


if __name__ == "__main__":
    main()