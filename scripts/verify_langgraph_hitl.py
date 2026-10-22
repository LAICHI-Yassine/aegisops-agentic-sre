import uuid
from src.agents.graph import build_aegis_graph


def main() -> None:
    print("=== Building AegisOps Multi-Agent Graph with PostgreSQL Checkpointer ===")
    graph, pg_conn = build_aegis_graph(enable_hitl_interrupt=True)

    try:
        # -----------------------------------------------------------------
        # Test 1: Run Investigation on INC-2026-002 (FinOps Runaway GPU Spike)
        # -----------------------------------------------------------------
        thread_id_1 = f"thread-finops-{uuid.uuid4().hex[:6]}"
        config_1 = {"configurable": {"thread_id": thread_id_1}}

        print(f"\n[1/4] Triggering Multi-Agent Investigation for INC-2026-002 (thread={thread_id_1})...")
        state_at_interrupt = graph.invoke({"incident_id": "INC-2026-002"}, config=config_1)

        snapshot = graph.get_state(config_1)
        rca = state_at_interrupt["rca_report"]
        proposal = state_at_interrupt["staged_proposal"]

        print(f"   -> Graph Paused at HITL Checkpoint! Next scheduled node: {snapshot.next}")
        print(f"   -> Offending Commit Isolated: {rca['offending_commit_hash']}")
        print(f"   -> Cited Runbook: {rca['cited_runbook_doc_id']}")
        print(f"   -> Estimated Hourly Burn Overrun: ${rca['estimated_hourly_burn_overrun_usd']}/hr")
        print(f"   -> Staged Proposal ID: {proposal['proposal_id']} ({proposal['approval_state']})")
        print(f"   -> Proposed Command: {rca['proposed_cli_command']}")

        # -----------------------------------------------------------------
        # Test 2: Simulate Human Operator Approval & Resume from PostgreSQL
        # -----------------------------------------------------------------
        print("\n[2/4] Simulating Human-in-the-Loop Approval by 'y.laichi' & Resuming Graph...")
        graph.update_state(
            config_1,
            {
                "human_approval_status": "APPROVED",
                "approver_id": "y.laichi",
            },
        )
        final_state = graph.invoke(None, config=config_1)
        exec_res = final_state["execution_result"]
        print(
            f"   -> Graph Resumed & Completed! Status: {exec_res['status']} | "
            f"Incident Status: {exec_res['incident_new_status']}"
        )

        # -----------------------------------------------------------------
        # Test 3: Adversarial Prompt & SQL Injection Trap (INC-2026-004)
        # -----------------------------------------------------------------
        thread_id_2 = f"thread-sec-{uuid.uuid4().hex[:6]}"
        config_2 = {"configurable": {"thread_id": thread_id_2}}

        print(f"\n[3/4] Triggering Investigation on Adversarial Trap INC-2026-004 (thread={thread_id_2})...")
        sec_state = graph.invoke({"incident_id": "INC-2026-004"}, config=config_2)
        sec_rca = sec_state["rca_report"]
        guard_block = sec_state["sql_guardrail_events"][0]

        print(f"   -> Security Guardrail Triggered: {sec_rca['security_guardrail_triggered']}")
        print(f"   -> AST SQL Guard Status: {guard_block['status']}")
        print(f"   -> Recommended Action: {sec_rca['recommended_action_type']}")
        print(f"   -> Safe Quarantine Command: {sec_rca['proposed_cli_command']}")

        # -----------------------------------------------------------------
        # Test 4: Verify LLMOps Agent Step Trace Trail
        # -----------------------------------------------------------------
        total_tokens = sum(step["tokens_used"] for step in final_state["trace_steps"])
        total_ms = round(sum(step["latency_ms"] for step in final_state["trace_steps"]), 2)
        print(f"\n[4/4] LLMOps Trace Verified ({len(final_state['trace_steps'])} nodes executed | {total_tokens} tokens | {total_ms} ms):")
        for step in final_state["trace_steps"]:
            print(f"      * [{step['node_name']}] {step['latency_ms']} ms -> {step['summary']}")

        print("\nStep 3.2 Complete: LangGraph Multi-Agent Orchestration & PostgresSaver HITL Verified!")
    finally:
        pg_conn.close()
        # Re-seed active_incidents status back to OPEN so future tests start clean
        from scripts.seed_chaos_data import seed_postgresql
        seed_postgresql()


if __name__ == "__main__":
    main()