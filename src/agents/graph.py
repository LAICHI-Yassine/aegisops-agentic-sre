import os
from typing import Literal, Tuple
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver

from src.agents.state import IncidentGraphState
from src.agents.nodes import (
    triage_node,
    mcp_investigator_node,
    hybrid_rag_runbook_node,
    critic_reflection_node,
    stage_hitl_remediation_node,
    execute_remediation_node,
)

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)


def route_after_critic(
    state: IncidentGraphState,
) -> Literal["stage_hitl_remediation", "hybrid_rag_runbook"]:
    critic = state.get("critic_assessment", {})
    iterations = state.get("iteration_count", 1)
    if critic.get("is_evidence_sufficient", False) or iterations >= 2:
        return "stage_hitl_remediation"
    return "hybrid_rag_runbook"


def build_aegis_graph(
    enable_hitl_interrupt: bool = True,
) -> Tuple[Any, psycopg.Connection]:
    """
    Builds and compiles the stateful AegisOps Multi-Agent Graph backed by
    PostgreSQL (`PostgresSaver`) with a Human-in-the-Loop breakpoint before
    `execute_remediation`.
    """
    pg_conn = psycopg.connect(
        POSTGRES_CONN_STR,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    checkpointer = PostgresSaver(pg_conn)
    checkpointer.setup()

    builder = StateGraph(IncidentGraphState)

    builder.add_node("triage", triage_node)
    builder.add_node("mcp_investigator", mcp_investigator_node)
    builder.add_node("hybrid_rag_runbook", hybrid_rag_runbook_node)
    builder.add_node("critic_reflection", critic_reflection_node)
    builder.add_node("stage_hitl_remediation", stage_hitl_remediation_node)
    builder.add_node("execute_remediation", execute_remediation_node)

    builder.add_edge(START, "triage")
    builder.add_edge("triage", "mcp_investigator")
    builder.add_edge("mcp_investigator", "hybrid_rag_runbook")
    builder.add_edge("hybrid_rag_runbook", "critic_reflection")
    builder.add_conditional_edges(
        "critic_reflection",
        route_after_critic,
        {
            "stage_hitl_remediation": "stage_hitl_remediation",
            "hybrid_rag_runbook": "hybrid_rag_runbook",
        },
    )
    builder.add_edge("stage_hitl_remediation", "execute_remediation")
    builder.add_edge("execute_remediation", END)

    interrupt_nodes = ["execute_remediation"] if enable_hitl_interrupt else []
    compiled_graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes,
    )
    return compiled_graph, pg_conn