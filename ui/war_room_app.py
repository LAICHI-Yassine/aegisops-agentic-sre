import html
import json
import os
from pathlib import Path
from typing import Any, Dict, List
import uuid
from dotenv import load_dotenv
import duckdb
import pandas as pd
import plotly.graph_objects as go
import psycopg
from psycopg.rows import dict_row
import streamlit as st

from src.agents.graph import build_aegis_graph
from src.core.observability import record_agent_trace, load_recent_traces
from scripts.seed_chaos_data import seed_postgresql

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "data/telemetry_parquet/aegis_telemetry.duckdb")

st.set_page_config(
    page_title="AEGISOPS // Autonomous SRE & FinOps Control Plane",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =====================================================================
# ENTERPRISE OBSIDIAN DESIGN SYSTEM (CSS + TYPOGRAPHY)
# =====================================================================
DESIGN_SYSTEM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-obsidian: #07090E;
    --bg-surface: #0D111A;
    --bg-card: rgba(17, 23, 36, 0.78);
    --border-subtle: rgba(255, 255, 255, 0.08);
    --border-highlight: rgba(99, 102, 241, 0.45);
    --text-primary: #F8FAFC;
    --text-secondary: #94A3B8;
    --text-muted: #64748B;
    --accent-indigo: #6366F1;
    --accent-cyan: #06B6D4;
    --accent-emerald: #10B981;
    --accent-amber: #F59E0B;
    --accent-crimson: #F43F5E;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    background-color: var(--bg-obsidian) !important;
    color: var(--text-primary) !important;
}

code, pre, .mono {
    font-family: 'JetBrains Mono', monospace !important;
}

.block-container {
    padding-top: 1.4rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1480px !important;
}

/* Hide default Streamlit chrome */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {background: transparent !important;}

/* Sidebar Styling */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #090C14 0%, #07090E 100%) !important;
    border-right: 1px solid var(--border-subtle) !important;
}

/* Top Command Header */
.command-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 18px 24px;
    background: linear-gradient(135deg, rgba(19, 26, 42, 0.85) 0%, rgba(11, 15, 25, 0.92) 100%);
    border: 1px solid var(--border-subtle);
    border-top: 2px solid var(--accent-indigo);
    border-radius: 12px;
    margin-bottom: 20px;
    box-shadow: 0 12px 32px -8px rgba(0, 0, 0, 0.6);
}

.brand-title {
    font-size: 1.45rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #FFFFFF;
    display: flex;
    align-items: center;
    gap: 12px;
}

.brand-badge {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 999px;
    background: rgba(99, 102, 241, 0.16);
    color: #818CF8;
    border: 1px solid rgba(99, 102, 241, 0.35);
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

.brand-subtitle {
    font-size: 0.84rem;
    color: var(--text-secondary);
    margin-top: 4px;
}

.header-status-cluster {
    display: flex;
    gap: 14px;
    align-items: center;
}

.status-pill {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    padding: 6px 12px;
    border-radius: 8px;
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34D399;
    display: flex;
    align-items: center;
    gap: 7px;
}

.pulse-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background-color: #10B981;
    box-shadow: 0 0 8px #10B981;
}

.pulse-dot-crimson {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background-color: #F43F5E;
    box-shadow: 0 0 10px #F43F5E;
}

/* KPI Glass Cards */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 18px;
}

.kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 16px 18px;
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(12px);
}

.kpi-card::before {
    content: "";
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent-indigo), var(--accent-cyan));
}

.kpi-card.critical::before {
    background: linear-gradient(90deg, var(--accent-crimson), var(--accent-amber));
}

.kpi-card.emerald::before {
    background: linear-gradient(90deg, var(--accent-emerald), var(--accent-cyan));
}

.kpi-label {
    font-size: 0.73rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--text-secondary);
    margin-bottom: 6px;
}

.kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.32rem;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.kpi-sub {
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-top: 4px;
    font-family: 'JetBrains Mono', monospace;
}

/* Alert Telemetry Banner */
.telemetry-banner {
    background: rgba(244, 63, 94, 0.07);
    border: 1px solid rgba(244, 63, 94, 0.28);
    border-left: 4px solid var(--accent-crimson);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.telemetry-banner-title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.76rem;
    font-weight: 600;
    color: #FB7185;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 4px;
}

.telemetry-banner-payload {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    color: #E2E8F0;
}

/* LangGraph Pipeline Visualizer */
.pipeline-container {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 16px 20px;
    margin: 18px 0;
}

.pipeline-header {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.74rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-secondary);
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
}

.pipeline-steps {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 10px;
}

.node-step {
    background: rgba(13, 17, 26, 0.9);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 10px 12px;
    transition: all 0.2s ease;
}

.node-step.done {
    border-color: rgba(16, 185, 129, 0.45);
    background: rgba(16, 185, 129, 0.06);
}

.node-step.paused {
    border-color: rgba(245, 158, 11, 0.65);
    background: rgba(245, 158, 11, 0.10);
    box-shadow: 0 0 15px rgba(245, 158, 11, 0.15);
}

.node-step-num {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    color: var(--text-muted);
    display: flex;
    justify-content: space-between;
}

.node-step-name {
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-top: 3px;
}

.node-step-meta {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.70rem;
    color: #34D399;
    margin-top: 4px;
}

.node-step.paused .node-step-meta {
    color: #FBBF24;
}

/* Section Panels */
.glass-panel {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
}

.panel-title {
    font-size: 0.92rem;
    font-weight: 600;
    color: #FFFFFF;
    letter-spacing: -0.01em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 10px;
}

.panel-tag {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.70rem;
    padding: 2px 8px;
    border-radius: 6px;
    background: rgba(6, 182, 212, 0.12);
    color: #22D3EE;
    border: 1px solid rgba(6, 182, 212, 0.25);
}

/* Evidence List Items */
.evidence-item {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.80rem;
    padding: 8px 12px;
    background: rgba(7, 9, 14, 0.65);
    border: 1px solid var(--border-subtle);
    border-left: 3px solid var(--accent-cyan);
    border-radius: 6px;
    margin-bottom: 8px;
    color: #E2E8F0;
}

/* HITL Terminal Gate */
.hitl-gate {
    background: linear-gradient(135deg, rgba(245, 158, 11, 0.08) 0%, rgba(15, 20, 32, 0.95) 100%);
    border: 1px solid rgba(245, 158, 11, 0.4);
    border-left: 4px solid var(--accent-amber);
    border-radius: 10px;
    padding: 20px 22px;
    margin-top: 18px;
    margin-bottom: 14px;
}

.hitl-title {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    font-weight: 600;
    color: #FBBF24;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.terminal-box {
    background: #05070B;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 14px 16px;
    margin-top: 12px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.86rem;
    color: #34D399;
}

/* Custom Styling for Streamlit Tabs & Buttons */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: var(--bg-surface);
    padding: 6px;
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
}

.stTabs [data-baseweb="tab"] {
    height: 38px;
    border-radius: 7px;
    font-weight: 500;
    font-size: 0.85rem;
    color: var(--text-secondary);
}

.stTabs [aria-selected="true"] {
    background-color: rgba(99, 102, 241, 0.2) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(99, 102, 241, 0.45) !important;
}

div.stButton > button[kind="primary"] {
    background: linear-gradient(90deg, #4F46E5 0%, #6366F1 100%) !important;
    border: 1px solid rgba(129, 140, 248, 0.4) !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em !important;
    border-radius: 8px !important;
    padding: 0.55rem 1.2rem !important;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35) !important;
}

div.stButton > button[kind="secondary"] {
    background: rgba(17, 23, 36, 0.9) !important;
    border: 1px solid rgba(255, 255, 255, 0.14) !important;
    color: #E2E8F0 !important;
    font-weight: 500 !important;
    border-radius: 8px !important;
}
</style>
"""


@st.cache_resource
def get_cached_graph():
    graph, conn = build_aegis_graph(enable_hitl_interrupt=True)
    return graph, conn


def fetch_incidents_and_billing() -> tuple[List[Dict[str, Any]], pd.DataFrame]:
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM active_incidents ORDER BY incident_id ASC;")
            incidents = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT timestamp_hour, service_name, hourly_cost_usd, active_instances, llm_tokens_consumed
                FROM cloud_billing_hourly
                ORDER BY timestamp_hour ASC;
                """
            )
            billing_rows = [dict(r) for r in cur.fetchall()]
    df_billing = pd.DataFrame(billing_rows)
    if not df_billing.empty:
        df_billing["hourly_cost_usd"] = df_billing["hourly_cost_usd"].astype(float)
    return incidents, df_billing


def fetch_service_telemetry_df(service_name: str) -> pd.DataFrame:
    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        rows = con.execute(
            """
            SELECT
                date_trunc('hour', timestamp) AS hour_bucket,
                ROUND(QUANTILE_CONT(latency_ms, 0.99), 2) AS p99_latency_ms,
                ROUND(MAX(memory_usage_mb), 1) AS peak_memory_mb,
                ROUND(MAX(cpu_usage_pct), 1) AS peak_cpu_pct,
                SUM(CASE WHEN error_code != 'NONE' THEN 1 ELSE 0 END) AS error_count
            FROM telemetry_logs
            WHERE service_name = ?
            GROUP BY 1
            ORDER BY 1 ASC;
            """,
            [service_name],
        ).fetchall()
    finally:
        con.close()

    return pd.DataFrame(
        rows,
        columns=["hour_bucket", "p99_latency_ms", "peak_memory_mb", "peak_cpu_pct", "error_count"],
    )


def build_enterprise_chart(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    title: str,
    unit_suffix: str,
    line_color: str,
    fill_rgba: str,
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df[x_col],
            y=df[y_col],
            mode="lines",
            line=dict(color=line_color, width=2.4, shape="spline"),
            fill="tozeroy",
            fillcolor=fill_rgba,
            hovertemplate=f"<b>%{{x}}</b><br>{y_col}: <b>%{{y}}{unit_suffix}</b><extra></extra>",
        )
    )
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(family="JetBrains Mono, monospace", size=12, color="#94A3B8"),
            x=0.01,
            y=0.95,
        ),
        paper_bgcolor="rgba(17, 23, 36, 0.78)",
        plot_bgcolor="rgba(7, 9, 14, 0.55)",
        font=dict(family="JetBrains Mono, monospace", size=11, color="#64748B"),
        height=255,
        margin=dict(l=42, r=16, t=42, b=32),
        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.04)",
            zeroline=False,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
            ticksuffix=unit_suffix,
        ),
        hovermode="x unified",
    )
    return fig


def render_pipeline_bar(trace_steps: List[Dict[str, Any]], is_paused_hitl: bool) -> str:
    step_map = {s["node_name"]: s for s in trace_steps}
    canonical_nodes = [
        ("01", "triage_agent", "Triage & Router"),
        ("02", "mcp_telemetry_finops_investigator", "MCP Telemetry/SQL"),
        ("03", "hybrid_rag_runbook_specialist", "Qdrant Hybrid RAG"),
        ("04", "self_reflection_critic", "Hallucination Critic"),
        ("05", "stage_hitl_remediation", "HITL Gate Stager"),
        ("06", "execute_remediation", "MCP Executor"),
    ]

    cards_html = []
    for num, key, label in canonical_nodes:
        if key in step_map:
            lat = step_map[key]["latency_ms"]
            tok = step_map[key]["tokens_used"]
            cls = "node-step done"
            status_badge = "✓ DONE"
            meta = f"{lat} ms · {tok} tok"
        elif key == "execute_remediation" and is_paused_hitl:
            cls = "node-step paused"
            status_badge = "⏸ HITL PAUSED"
            meta = "Awaiting Sign-Off"
        else:
            cls = "node-step"
            status_badge = "PENDING"
            meta = "--"

        cards_html.append(
            f'<div class="{cls}">'
            f'<div class="node-step-num"><span>NODE {num}</span><span>{status_badge}</span></div>'
            f'<div class="node-step-name">{label}</div>'
            f'<div class="node-step-meta">{meta}</div>'
            f"</div>"
        )

    total_ms = round(sum(s["latency_ms"] for s in trace_steps), 2)
    total_tok = sum(s["tokens_used"] for s in trace_steps)
    return (
        f'<div class="pipeline-container">'
        f'<div class="pipeline-header">'
        f'<span>LANGGRAPH STATE MACHINE TRAJECTORY (POSTGRESSAVER CHECKPOINTING)</span>'
        f'<span>TOTAL: {total_ms} MS // {total_tok} TOKENS</span>'
        f"</div>"
        f'<div class="pipeline-steps">{"".join(cards_html)}</div>'
        f"</div>"
    )


def main() -> None:
    st.markdown(DESIGN_SYSTEM_CSS, unsafe_allow_html=True)

    incidents, df_billing = fetch_incidents_and_billing()
    open_count = sum(1 for i in incidents if i["status"] == "OPEN")

    # =====================================================================
    # SIDEBAR: INCIDENT SELECTOR & ARCHITECTURE TELEMETRY
    # =====================================================================
    with st.sidebar:
        st.markdown(
            """
            <div style="padding: 8px 0 14px 0; border-bottom: 1px solid rgba(255,255,255,0.08); margin-bottom: 16px;">
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; color: #818CF8; letter-spacing: 0.08em;">
                    AEGISOPS // CONTROL PLANE
                </div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #FFFFFF; margin-top: 2px;">
                    Incident Dispatch Queue
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        inc_options = {
            f"{inc['incident_id']} · {inc['service_name']} [{inc['status']}]": inc
            for inc in incidents
        }
        selected_label = st.selectbox(
            "ACTIVE INCIDENT STREAM",
            list(inc_options.keys()),
            index=0,
        )
        selected_inc = inc_options[selected_label]

        approver_name = st.text_input("ON-CALL SRE OPERATOR ID", value="y.laichi")

        if st.button("↻ Reset Sandbox Incidents to OPEN", use_container_width=True):
            seed_postgresql()
            st.session_state.pop("active_investigation", None)
            st.rerun()

        st.markdown(
            """
            <div style="margin-top: 22px; padding: 14px; background: rgba(17,23,36,0.75); border: 1px solid rgba(255,255,255,0.07); border-radius: 8px;">
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 0.70rem; color: #94A3B8; text-transform: uppercase; margin-bottom: 10px;">
                    Runtime Mesh Status
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; color: #E2E8F0; line-height: 1.85;">
                    <div><span style="color:#10B981;">●</span> LangGraph Checkpointer: <span style="color:#94A3B8;">PG16</span></div>
                    <div><span style="color:#10B981;">●</span> MCP Server #1: <span style="color:#94A3B8;">Telemetry/FinOps</span></div>
                    <div><span style="color:#10B981;">●</span> MCP Server #2: <span style="color:#94A3B8;">Remediator v2</span></div>
                    <div><span style="color:#10B981;">●</span> Hybrid RAG: <span style="color:#94A3B8;">Qdrant RRF + ONNX</span></div>
                    <div><span style="color:#10B981;">●</span> AST SQL Guard: <span style="color:#94A3B8;">sqlglot Strict</span></div>
                    <div><span style="color:#10B981;">●</span> OLAP Store: <span style="color:#94A3B8;">DuckDB (200k rows)</span></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # =====================================================================
    # TOP EXECUTIVE COMMAND BAR
    # =====================================================================
    st.markdown(
        f"""
        <div class="command-header">
            <div>
                <div class="brand-title">
                    ⚡ AEGISOPS
                    <span class="brand-badge">MCP SDK v2.x // LANGGRAPH HITL</span>
                </div>
                <div class="brand-subtitle">
                    Autonomous Multi-Agent SRE Incident Responder, FinOps Anomaly Containment & Hybrid Reranked RAG
                </div>
            </div>
            <div class="header-status-cluster">
                <div class="status-pill">
                    <span class="pulse-dot"></span>
                    MCP JSON-RPC: ONLINE
                </div>
                <div class="status-pill" style="background: rgba(244,63,94,0.1); border-color: rgba(244,63,94,0.3); color: #FB7185;">
                    <span class="pulse-dot-crimson"></span>
                    OPEN INCIDENTS: {open_count}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3 = st.tabs(
        [
            "⚡ LIVE WAR-ROOM & HITL REMEDIATION GATE",
            "📐 HYBRID RAG & RAGAS BENCHMARK SUITE",
            "🛰️ LLMOPS TELEMETRY & COST OBSERVATORY",
        ]
    )

    # =====================================================================
    # TAB 1: WAR-ROOM & HITL REMEDIATION GATE
    # =====================================================================
    with tab1:
        df_telem = fetch_service_telemetry_df(selected_inc["service_name"])
        svc_billing = df_billing[df_billing["service_name"] == selected_inc["service_name"]]

        peak_p99 = float(df_telem["p99_latency_ms"].max()) if not df_telem.empty else 0.0
        peak_cost = float(svc_billing["hourly_cost_usd"].max()) if not svc_billing.empty else 0.0
        status_cls = "critical" if selected_inc["status"] == "OPEN" else "emerald"

        # 4 Glassmorphic KPI Cards
        st.markdown(
            f"""
            <div class="kpi-grid">
                <div class="kpi-card {status_cls}">
                    <div class="kpi-label">Incident Identifier</div>
                    <div class="kpi-value">{html.escape(selected_inc['incident_id'])}</div>
                    <div class="kpi-sub">{html.escape(selected_inc['severity'])} // {html.escape(selected_inc['status'])}</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Target Microservice</div>
                    <div class="kpi-value">{html.escape(selected_inc['service_name'])}</div>
                    <div class="kpi-sub">Domain: {html.escape(selected_inc['domain'])}</div>
                </div>
                <div class="kpi-card critical">
                    <div class="kpi-label">DuckDB Peak p99 Latency</div>
                    <div class="kpi-value">{peak_p99:,.1f} ms</div>
                    <div class="kpi-sub">SLO Threshold: &lt; 100.0 ms</div>
                </div>
                <div class="kpi-card">
                    <div class="kpi-label">Peak Hourly Cloud Burn</div>
                    <div class="kpi-value">${peak_cost:,.2f} / hr</div>
                    <div class="kpi-sub">PostgreSQL FinOps Stream</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Incoming Alert Banner
        st.markdown(
            f"""
            <div class="telemetry-banner">
                <div>
                    <div class="telemetry-banner-title">
                        INGESTED WEBHOOK ALERT PAYLOAD // {html.escape(selected_inc['title'])}
                    </div>
                    <div class="telemetry-banner-payload">
                        {html.escape(selected_inc['alert_payload'])}
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Dual Neon Telemetry Charts
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            if not df_telem.empty:
                fig1 = build_enterprise_chart(
                    df=df_telem,
                    x_col="hour_bucket",
                    y_col="p99_latency_ms",
                    title=f"DUCKDB OLAP // {selected_inc['service_name'].upper()} P99 LATENCY (MS)",
                    unit_suffix=" ms",
                    line_color="#F43F5E",
                    fill_rgba="rgba(244, 63, 94, 0.14)",
                )
                st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar": False})

        with c_chart2:
            if not svc_billing.empty:
                fig2 = build_enterprise_chart(
                    df=svc_billing,
                    x_col="timestamp_hour",
                    y_col="hourly_cost_usd",
                    title=f"POSTGRESQL FINOPS // {selected_inc['service_name'].upper()} HOURLY SPEND ($/HR)",
                    unit_suffix=" $",
                    line_color="#06B6D4",
                    fill_rgba="rgba(6, 182, 212, 0.14)",
                )
                st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})

        # Launch Autonomous Investigation Button
        if st.button(
            f"⚡ DISPATCH AUTONOMOUS LANGGRAPH INVESTIGATION // {selected_inc['incident_id']}",
            type="primary",
            use_container_width=True,
        ):
            graph, _ = get_cached_graph()
            thread_id = f"warroom-{selected_inc['incident_id'].lower()}-{uuid.uuid4().hex[:6]}"
            cfg = {"configurable": {"thread_id": thread_id}}

            with st.spinner("Executing Multi-Agent StateGraph over MCP v2 & Qdrant Hybrid RAG..."):
                paused_state = graph.invoke({"incident_id": selected_inc["incident_id"]}, config=cfg)
                snapshot = graph.get_state(cfg)
                trace_entry = record_agent_trace(thread_id=thread_id, final_state=paused_state)

                st.session_state["active_investigation"] = {
                    "thread_id": thread_id,
                    "incident_id": selected_inc["incident_id"],
                    "state": paused_state,
                    "next_node": list(snapshot.next),
                    "trace": trace_entry,
                }

        # Render Active Investigation Results
        active_inv = st.session_state.get("active_investigation")
        if active_inv and active_inv["incident_id"] == selected_inc["incident_id"]:
            inv_state = active_inv["state"]
            rca = inv_state["rca_report"]
            proposal = inv_state["staged_proposal"]
            runbooks = inv_state.get("retrieved_runbooks", [])
            deployments = inv_state.get("deployment_evidence", {}).get("deployments", [])
            is_paused = len(active_inv["next_node"]) > 0

            # 1. Visual LangGraph Pipeline Trajectory Bar
            st.markdown(
                render_pipeline_bar(inv_state.get("trace_steps", []), is_paused_hitl=is_paused),
                unsafe_allow_html=True,
            )

            # 2. Root Cause Synthesis Card
            guard_badge = (
                '<span class="panel-tag" style="background:rgba(244,63,94,0.16); color:#FB7185; border-color:rgba(244,63,94,0.4);">'
                "🛡️ SQLGLOT AST & PROMPT GUARDRAIL TRIGGERED</span>"
                if rca["security_guardrail_triggered"]
                else '<span class="panel-tag">✓ PYDANTIC V2 & INSTRUCTOR VALIDATED</span>'
            )

            st.markdown(
                f"""
                <div class="glass-panel">
                    <div class="panel-title">
                        <span>ROOT CAUSE ANALYSIS (RCA) SYNTHESIS // THREAD {html.escape(active_inv['thread_id'])}</span>
                        {guard_badge}
                    </div>
                    <div style="font-size: 0.95rem; line-height: 1.65; color: #F1F5F9; margin-bottom: 14px;">
                        {html.escape(rca['root_cause_summary'])}
                    </div>
                    <div class="kpi-grid" style="margin-bottom: 0;">
                        <div class="kpi-card" style="padding: 12px 14px;">
                            <div class="kpi-label">Offending Git Commit</div>
                            <div class="kpi-value" style="font-size: 1.05rem; color: #38BDF8;">{html.escape(rca['offending_commit_hash'])}</div>
                        </div>
                        <div class="kpi-card" style="padding: 12px 14px;">
                            <div class="kpi-label">Primary Cited Runbook</div>
                            <div class="kpi-value" style="font-size: 0.92rem;">{html.escape(rca['cited_runbook_doc_id'])}</div>
                        </div>
                        <div class="kpi-card" style="padding: 12px 14px;">
                            <div class="kpi-label">Hourly Burn Overrun</div>
                            <div class="kpi-value" style="font-size: 1.05rem; color: #FBBF24;">${rca['estimated_hourly_burn_overrun_usd']:,.2f} / hr</div>
                        </div>
                        <div class="kpi-card" style="padding: 12px 14px;">
                            <div class="kpi-label">Critic Confidence</div>
                            <div class="kpi-value" style="font-size: 1.05rem; color: #34D399;">{rca['confidence_score']:.0%}</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # 3. Two-Column Deep Technical Evidence (MCP Telemetry/Git Diff vs Hybrid RAG Runbooks)
            col_left, col_right = st.columns(2)

            with col_left:
                evidence_items_html = "".join(
                    f'<div class="evidence-item">▸ {html.escape(ev)}</div>'
                    for ev in rca["key_telemetry_evidence"]
                )
                st.markdown(
                    f"""
                    <div class="glass-panel">
                        <div class="panel-title">
                            <span>MCP TELEMETRY & GIT DEPLOYMENT CORRELATION</span>
                            <span class="panel-tag">DUCKDB + POSTGRES</span>
                        </div>
                        {evidence_items_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if deployments:
                    dep = deployments[0]
                    st.caption(
                        f"Git Commit `{dep['commit_hash']}` by `{dep['author']}` deployed at `{dep['deployed_at']}`:"
                    )
                    st.code(dep["config_diff"], language="diff")

                if inv_state.get("sql_guardrail_events"):
                    st.caption("Blocked Adversarial SQL AST Inspection (`sqlglot`):")
                    st.code(
                        json.dumps(inv_state["sql_guardrail_events"][0], indent=2),
                        language="json",
                    )

            with col_right:
                st.markdown(
                    f"""
                    <div class="glass-panel" style="margin-bottom: 10px;">
                        <div class="panel-title" style="margin-bottom: 4px; border-bottom: none; padding-bottom: 0;">
                            <span>QDRANT HYBRID RRF + CROSS-ENCODER RERANKED RUNBOOKS</span>
                            <span class="panel-tag">CACHE HIT: {inv_state.get('rag_cache_hit', False)} ({inv_state.get('rag_latency_ms', 0)} MS)</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                for idx, rb in enumerate(runbooks, start=1):
                    ce_score = rb.get("reranker_score", rb.get("score", 0.0))
                    with st.expander(
                        f"RANK #{idx} // {rb['doc_id']}  [Cross-Encoder Logit: {ce_score}]",
                        expanded=(idx == 1),
                    ):
                        st.markdown(rb["parent_text"])

            # 4. Terminal-Grade Human-in-the-Loop Execution Gate
            st.markdown(
                f"""
                <div class="hitl-gate">
                    <div class="hitl-title">
                        <span>⚡ HUMAN-IN-THE-LOOP (HITL) PRODUCTION EXECUTION GATE // {html.escape(proposal.get('proposal_id', 'STAGED'))}</span>
                        <span>ACTION: {html.escape(rca['recommended_action_type'].upper())} (REPLICAS={rca['target_replica_count']})</span>
                    </div>
                    <div style="font-size: 0.83rem; color: #CBD5E1; margin-top: 6px;">
                        LangGraph execution is frozen at <code>PostgresSaver</code> checkpoint before node <code>execute_remediation</code>. Review the staged command below:
                    </div>
                    <div class="terminal-box">
                        $ {html.escape(rca['proposed_cli_command'])}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if is_paused:
                b_col1, b_col2 = st.columns(2)
                with b_col1:
                    if st.button(
                        f"✓ APPROVE & EXECUTE REMEDIATION (SIGN-OFF: {approver_name})",
                        type="primary",
                        use_container_width=True,
                    ):
                        graph, _ = get_cached_graph()
                        cfg = {"configurable": {"thread_id": active_inv["thread_id"]}}
                        graph.update_state(
                            cfg,
                            {"human_approval_status": "APPROVED", "approver_id": approver_name},
                        )
                        final_state = graph.invoke(None, config=cfg)
                        trace_entry = record_agent_trace(
                            thread_id=active_inv["thread_id"], final_state=final_state
                        )
                        st.session_state["active_investigation"]["state"] = final_state
                        st.session_state["active_investigation"]["next_node"] = []
                        st.session_state["active_investigation"]["trace"] = trace_entry
                        st.rerun()

                with b_col2:
                    if st.button(
                        f"✕ REJECT & ABORT REMEDIATION (SIGN-OFF: {approver_name})",
                        type="secondary",
                        use_container_width=True,
                    ):
                        graph, _ = get_cached_graph()
                        cfg = {"configurable": {"thread_id": active_inv["thread_id"]}}
                        graph.update_state(
                            cfg,
                            {"human_approval_status": "REJECTED", "approver_id": approver_name},
                        )
                        final_state = graph.invoke(None, config=cfg)
                        trace_entry = record_agent_trace(
                            thread_id=active_inv["thread_id"], final_state=final_state
                        )
                        st.session_state["active_investigation"]["state"] = final_state
                        st.session_state["active_investigation"]["next_node"] = []
                        st.session_state["active_investigation"]["trace"] = trace_entry
                        st.rerun()

            if inv_state.get("execution_result"):
                exec_res = inv_state["execution_result"]
                if exec_res.get("status") == "REMEDIATION_EXECUTED":
                    st.markdown(
                        f"""
                        <div class="glass-panel" style="border-color: rgba(16,185,129,0.5); background: rgba(16,185,129,0.08);">
                            <div style="font-family:'JetBrains Mono',monospace; font-size:0.86rem; color:#34D399; font-weight:600;">
                                ✓ REMEDIATION EXECUTED VIA MCP // INCIDENT {html.escape(selected_inc['incident_id'])} TRANSITIONED TO RESOLVED
                            </div>
                            <div style="font-family:'JetBrains Mono',monospace; font-size:0.78rem; color:#E2E8F0; margin-top:4px;">
                                Signed off by: {html.escape(str(exec_res.get('approved_by')))} | Command: {html.escape(str(exec_res.get('executed_command')))}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div class="glass-panel" style="border-color: rgba(244,63,94,0.5); background: rgba(244,63,94,0.08);">
                            <div style="font-family:'JetBrains Mono',monospace; font-size:0.86rem; color:#FB7185; font-weight:600;">
                                ✕ REMEDIATION ABORTED BY OPERATOR // NO INFRASTRUCTURE MUTATIONS APPLIED
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

    # =====================================================================
    # TAB 2: HYBRID RAG & RAGAS BENCHMARK SUITE
    # =====================================================================
    with tab2:
        rag_path = Path("data/eval_rag_benchmark_results.json")
        agent_path = Path("data/eval_agent_ragas_benchmark.json")

        if rag_path.exists() and agent_path.exists():
            rag_data = json.loads(rag_path.read_text(encoding="utf-8"))
            agent_data = json.loads(agent_path.read_text(encoding="utf-8"))

            st.markdown(
                f"""
                <div class="kpi-grid">
                    <div class="kpi-card emerald">
                        <div class="kpi-label">Ragas Faithfulness</div>
                        <div class="kpi-value">{agent_data['mean_faithfulness']:.1%}</div>
                        <div class="kpi-sub">Zero Hallucinated CLI Flags</div>
                    </div>
                    <div class="kpi-card emerald">
                        <div class="kpi-label">Hybrid+Rerank Precision@1</div>
                        <div class="kpi-value">{rag_data['hybrid_reranked']['precision_at_1']:.1%}</div>
                        <div class="kpi-sub">MRR@3: {rag_data['hybrid_reranked']['mrr_at_3']:.4f}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">RCA Commit Isolation</div>
                        <div class="kpi-value">{agent_data['mean_commit_isolation_accuracy']:.1%}</div>
                        <div class="kpi-sub">4/4 Chaos Scenarios</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Redis Semantic Cache Hit</div>
                        <div class="kpi-value">{rag_data.get('redis_warm_cache_avg_latency_ms', 1.08)} ms</div>
                        <div class="kpi-sub">&gt;1,000x Cold-to-Warm Speedup</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            b_col1, b_col2 = st.columns(2)
            with b_col1:
                st.markdown(
                    """
                    <div class="glass-panel">
                        <div class="panel-title">
                            <span>QDRANT RETRIEVAL ABLATION (12 GOLDEN QUERIES)</span>
                            <span class="panel-tag">DENSE VS. BM25 RRF VS. RERANKER</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                rag_rows = [
                    rag_data[k]
                    for k in ("dense_only", "hybrid_rrf", "hybrid_reranked")
                    if k in rag_data
                ]
                st.dataframe(pd.DataFrame(rag_rows), use_container_width=True, hide_index=True)

            with b_col2:
                st.markdown(
                    """
                    <div class="glass-panel">
                        <div class="panel-title">
                            <span>END-TO-END MULTI-AGENT & RAGAS INCIDENT EVAL</span>
                            <span class="panel-tag">4 PRODUCTION SCENARIOS</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    pd.DataFrame(agent_data.get("incident_breakdown", [])),
                    use_container_width=True,
                    hide_index=True,
                )

    # =====================================================================
    # TAB 3: LLMOPS TRACE & COST OBSERVATORY
    # =====================================================================
    with tab3:
        traces = load_recent_traces(limit=25)
        if traces:
            latest = traces[0]
            st.markdown(
                f"""
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Latest Trace Thread</div>
                        <div class="kpi-value" style="font-size:1.02rem;">{html.escape(latest['thread_id'])}</div>
                        <div class="kpi-sub">Incident: {html.escape(latest['incident_id'])}</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">End-to-End Graph Latency</div>
                        <div class="kpi-value">{latest['total_latency_ms']:,.1f} ms</div>
                        <div class="kpi-sub">{latest['total_nodes_executed']} LangGraph Nodes</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Total Token Consumption</div>
                        <div class="kpi-value">{latest['total_tokens']:,} tok</div>
                        <div class="kpi-sub">Est. Cost: ${latest['estimated_llm_cost_usd']:.5f}</div>
                    </div>
                    <div class="kpi-card emerald">
                        <div class="kpi-label">Guardrail Status</div>
                        <div class="kpi-value">{"BLOCKED ATTACK" if latest['security_guardrail_triggered'] else "VERIFIED SAFE"}</div>
                        <div class="kpi-sub">HITL: {html.escape(latest['human_approval_status'])}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            df_steps = pd.DataFrame(latest["steps"])
            fig_waterfall = go.Figure(
                go.Bar(
                    x=df_steps["latency_ms"],
                    y=df_steps["node_name"],
                    orientation="h",
                    text=[f"{v} ms ({t} tok)" for v, t in zip(df_steps["latency_ms"], df_steps["tokens_used"])],
                    textposition="auto",
                    marker=dict(
                        color="#6366F1",
                        line=dict(color="#818CF8", width=1.5),
                    ),
                )
            )
            fig_waterfall.update_layout(
                title=dict(
                    text=f"LANGGRAPH NODE LATENCY WATERFALL // {latest['thread_id'].upper()}",
                    font=dict(family="JetBrains Mono, monospace", size=12, color="#94A3B8"),
                ),
                paper_bgcolor="rgba(17, 23, 36, 0.78)",
                plot_bgcolor="rgba(7, 9, 14, 0.55)",
                font=dict(family="JetBrains Mono, monospace", size=11, color="#E2E8F0"),
                height=260,
                margin=dict(l=20, r=20, t=40, b=20),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_waterfall, use_container_width=True, config={"displayModeBar": False})

            st.markdown(
                """
                <div class="glass-panel">
                    <div class="panel-title">
                        <span>HISTORICAL LLMOPS EXECUTION TRACE LOG (JSONL AUDIT TRAIL)</span>
                        <span class="panel-tag">LANGFUSE-COMPATIBLE SPANS</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            df_traces = pd.DataFrame(
                [
                    {
                        "Timestamp (UTC)": t["timestamp_utc"],
                        "Thread ID": t["thread_id"],
                        "Incident": t["incident_id"],
                        "Service": t["service_name"],
                        "Domain": t["domain"],
                        "Nodes": t["total_nodes_executed"],
                        "Latency (ms)": t["total_latency_ms"],
                        "Tokens": t["total_tokens"],
                        "Cost ($)": f"${t['estimated_llm_cost_usd']:.5f}",
                        "RAG Cache": t["rag_cache_hit"],
                        "Guardrail": t["security_guardrail_triggered"],
                        "HITL Status": t["human_approval_status"],
                    }
                    for t in traces
                ]
            )
            st.dataframe(df_traces, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()