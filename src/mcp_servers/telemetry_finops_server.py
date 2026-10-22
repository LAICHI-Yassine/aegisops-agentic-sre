import os
import json
from datetime import datetime, timedelta, timezone
from typing import Literal, Any, Dict, List
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
import duckdb
from mcp.server.mcpserver import MCPServer

from src.core.sql_guard import validate_readonly_sql, SQLSecurityViolationError

load_dotenv()

POSTGRES_CONN_STR = (
    f"dbname={os.getenv('POSTGRES_DB', 'aegisops_db')} "
    f"user={os.getenv('POSTGRES_USER', 'aegis_admin')} "
    f"password={os.getenv('POSTGRES_PASSWORD', 'aegis_secret_pwd')} "
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')}"
)
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "data/telemetry_parquet/aegis_telemetry.duckdb")
ANCHOR_TIME = datetime(2026, 10, 20, 8, 0, 0, tzinfo=timezone.utc)

# Initialize Official MCP Server
mcp = MCPServer("aegis-telemetry-finops")


@mcp.resource("aegis://schema/database-catalog")
def get_database_schema_catalog() -> str:
    """Exposes the PostgreSQL and DuckDB table schemas to MCP clients."""
    schema_info = {
        "postgresql_tables": {
            "active_incidents": ["incident_id", "title", "service_name", "severity", "domain", "triggered_at", "status", "alert_payload"],
            "cloud_billing_hourly": ["billing_id", "timestamp_hour", "service_name", "cloud_provider", "region", "instance_type", "active_instances", "llm_tokens_consumed", "hourly_cost_usd"],
            "deployments": ["deployment_id", "commit_hash", "service_name", "author", "deployed_at", "status", "config_diff"],
        },
        "duckdb_olap_views": {
            "telemetry_logs": ["timestamp", "service_name", "latency_ms", "memory_usage_mb", "cpu_usage_pct", "http_status", "severity", "error_code", "log_message"]
        },
        "reference_anchor_time_utc": ANCHOR_TIME.isoformat(),
    }
    return json.dumps(schema_info, indent=2)


@mcp.tool()
def get_open_incidents(domain_filter: str = "ALL") -> str:
    """
    Fetches all currently OPEN production incidents from PostgreSQL.
    Args:
        domain_filter: Filter by domain ('ALL', 'SRE_INFRA', 'FINOPS_COST', 'SECURITY_TEST')
    """
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if domain_filter.upper() == "ALL":
                cur.execute(
                    "SELECT * FROM active_incidents WHERE status = 'OPEN' ORDER BY triggered_at DESC;"
                )
            else:
                cur.execute(
                    "SELECT * FROM active_incidents WHERE status = 'OPEN' AND domain = %s ORDER BY triggered_at DESC;",
                    (domain_filter.upper(),),
                )
            rows = cur.fetchall()
    return json.dumps(rows, default=str, indent=2)


@mcp.tool()
def detect_finops_cost_spikes(lookback_hours: int = 6, spike_multiplier_threshold: float = 2.0) -> str:
    """
    Compares hourly cloud billing and LLM token usage over the recent lookback window
    against the historical baseline (prior 7 days) to isolate runaway cloud/GPU spend.
    """
    cutoff_recent = ANCHOR_TIME - timedelta(hours=lookback_hours)
    query = """
        WITH baseline AS (
            SELECT
                service_name,
                cloud_provider,
                region,
                ROUND(AVG(hourly_cost_usd), 2) AS baseline_avg_cost_usd,
                ROUND(AVG(active_instances), 1) AS baseline_avg_instances,
                ROUND(AVG(llm_tokens_consumed), 0) AS baseline_avg_tokens
            FROM cloud_billing_hourly
            WHERE timestamp_hour < %s
            GROUP BY service_name, cloud_provider, region
        ),
        recent AS (
            SELECT
                service_name,
                (ARRAY_AGG(instance_type ORDER BY hourly_cost_usd DESC))[1] AS current_instance_type,
                MAX(active_instances) AS peak_instances,
                MAX(llm_tokens_consumed) AS peak_llm_tokens,
                ROUND(AVG(hourly_cost_usd), 2) AS recent_avg_cost_usd,
                ROUND(MAX(hourly_cost_usd), 2) AS recent_peak_cost_usd
            FROM cloud_billing_hourly
            WHERE timestamp_hour >= %s
            GROUP BY service_name
        )
        SELECT
            r.service_name,
            b.cloud_provider,
            b.region,
            r.current_instance_type,
            b.baseline_avg_instances,
            r.peak_instances,
            b.baseline_avg_tokens,
            r.peak_llm_tokens,
            b.baseline_avg_cost_usd,
            r.recent_avg_cost_usd,
            r.recent_peak_cost_usd,
            ROUND((r.recent_avg_cost_usd / NULLIF(b.baseline_avg_cost_usd, 0)), 2) AS cost_spike_multiplier,
            ROUND((r.recent_avg_cost_usd - b.baseline_avg_cost_usd) * 24, 2) AS projected_daily_burn_overrun_usd
        FROM recent r
        JOIN baseline b ON r.service_name = b.service_name
        WHERE (r.recent_avg_cost_usd / NULLIF(b.baseline_avg_cost_usd, 0)) >= %s
        ORDER BY cost_spike_multiplier DESC;
    """
    with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (cutoff_recent, cutoff_recent, spike_multiplier_threshold))
            anomalies = cur.fetchall()

    return json.dumps(
        {
            "lookback_hours": lookback_hours,
            "anomalies_detected": len(anomalies),
            "finops_anomalies": anomalies,
        },
        default=str,
        indent=2,
    )


@mcp.tool()
def analyze_service_telemetry(service_name: str, lookback_hours: int = 6) -> str:
    """
    Executes an OLAP aggregation in DuckDB over the 200,000+ telemetry log store
    for a specific microservice, returning p50/p95/p99 latency, memory/CPU peaks,
    HTTP 5xx error rates, and dominant error signatures.
    """
    cutoff_recent = (ANCHOR_TIME - timedelta(hours=lookback_hours)).isoformat()

    con = duckdb.connect(DUCKDB_PATH, read_only=True)
    try:
        metrics_row = con.execute(
            """
            SELECT
                COUNT(*) AS total_events,
                ROUND(QUANTILE_CONT(latency_ms, 0.50), 2) AS latency_p50_ms,
                ROUND(QUANTILE_CONT(latency_ms, 0.95), 2) AS latency_p95_ms,
                ROUND(QUANTILE_CONT(latency_ms, 0.99), 2) AS latency_p99_ms,
                ROUND(MAX(memory_usage_mb), 1) AS peak_memory_mb,
                ROUND(MAX(cpu_usage_pct), 1) AS peak_cpu_pct,
                SUM(CASE WHEN http_status >= 500 OR http_status = 429 THEN 1 ELSE 0 END) AS failed_requests_count,
                ROUND(
                    100.0 * SUM(CASE WHEN http_status >= 500 OR http_status = 429 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                    2
                ) AS failure_rate_pct
            FROM telemetry_logs
            WHERE service_name = ? AND timestamp >= CAST(? AS TIMESTAMPTZ);
            """,
            [service_name, cutoff_recent],
        ).fetchone()

        error_breakdown = con.execute(
            """
            SELECT
                error_code,
                severity,
                http_status,
                COUNT(*) AS occurrences,
                FIRST(log_message) AS sample_log_message,
                MIN(timestamp) AS first_seen_utc,
                MAX(timestamp) AS last_seen_utc
            FROM telemetry_logs
            WHERE service_name = ?
              AND timestamp >= CAST(? AS TIMESTAMPTZ)
              AND error_code != 'NONE'
            GROUP BY error_code, severity, http_status
            ORDER BY occurrences DESC;
            """,
            [service_name, cutoff_recent],
        ).fetchall()
    finally:
        con.close()

    result = {
        "service_name": service_name,
        "lookback_hours": lookback_hours,
        "telemetry_summary": {
            "total_events": metrics_row[0],
            "latency_p50_ms": metrics_row[1],
            "latency_p95_ms": metrics_row[2],
            "latency_p99_ms": metrics_row[3],
            "peak_memory_mb": metrics_row[4],
            "peak_cpu_pct": metrics_row[5],
            "failed_requests_count": metrics_row[6],
            "failure_rate_pct": metrics_row[7],
        },
        "error_signatures": [
            {
                "error_code": r[0],
                "severity": r[1],
                "http_status": r[2],
                "occurrences": r[3],
                "sample_log_message": r[4],
                "first_seen_utc": str(r[5]),
                "last_seen_utc": str(r[6]),
            }
            for r in error_breakdown
        ],
    }
    return json.dumps(result, default=str, indent=2)


@mcp.tool()
def execute_guarded_diagnostic_sql(
    sql_query: str,
    target_engine: Literal["postgres", "duckdb"] = "postgres",
) -> str:
    """
    Executes a custom read-only SQL query against PostgreSQL or DuckDB after
    passing strict sqlglot AST validation (blocks DROP/DELETE/UPDATE/stacked queries).
    """
    try:
        validate_readonly_sql(sql_query, dialect="postgres" if target_engine == "postgres" else "duckdb")
    except SQLSecurityViolationError as sec_err:
        return json.dumps(
            {
                "status": "BLOCKED_BY_AST_SECURITY_GUARD",
                "violation_reason": str(sec_err),
                "attempted_query": sql_query,
            },
            indent=2,
        )

    try:
        if target_engine == "postgres":
            with psycopg.connect(POSTGRES_CONN_STR, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql_query)
                    rows: List[Dict[str, Any]] = cur.fetchall()[:50]
            return json.dumps({"status": "SUCCESS", "engine": "postgres", "rows": rows}, default=str, indent=2)
        else:
            con = duckdb.connect(DUCKDB_PATH, read_only=True)
            try:
                rel = con.sql(sql_query)
                cols = rel.columns
                fetched = rel.fetchall()[:50]
                rows = [dict(zip(cols, r)) for r in fetched]
            finally:
                con.close()
            return json.dumps({"status": "SUCCESS", "engine": "duckdb", "rows": rows}, default=str, indent=2)
    except Exception as db_err:
        return json.dumps({"status": "EXECUTION_ERROR", "error": str(db_err)}, indent=2)


if __name__ == "__main__":
    mcp.run()