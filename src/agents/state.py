import operator
from typing import Annotated, Any, Dict, List, TypedDict


class AgentStepTrace(TypedDict):
    node_name: str
    latency_ms: float
    summary: str
    tokens_used: int


class IncidentGraphState(TypedDict, total=False):
    # Input
    incident_id: str
    incident_record: Dict[str, Any]

    # Node 1: Triage Output
    triage: Dict[str, Any]

    # Node 2: MCP Telemetry, FinOps & Git Deployment Evidence
    telemetry_evidence: Dict[str, Any]
    finops_evidence: Dict[str, Any]
    deployment_evidence: Dict[str, Any]
    sql_guardrail_events: List[Dict[str, Any]]

    # Node 3: Hybrid RAG + Cross-Encoder Reranked Runbooks
    rag_query: str
    retrieved_runbooks: List[Dict[str, Any]]
    rag_latency_ms: float
    rag_cache_hit: bool

    # Node 4: Critic & Self-Reflection Loop
    iteration_count: int
    critic_assessment: Dict[str, Any]

    # Node 5 & 6: Structured RCA Report & HITL Staging/Execution
    rca_report: Dict[str, Any]
    staged_proposal: Dict[str, Any]
    human_approval_status: str   # "PENDING_HUMAN_APPROVAL" | "APPROVED" | "REJECTED"
    approver_id: str
    execution_result: Dict[str, Any]

    # Observability & LLMOps Trace Trail (appends across nodes)
    trace_steps: Annotated[List[AgentStepTrace], operator.add]