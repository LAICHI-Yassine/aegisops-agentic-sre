from typing import Tuple, Set
import sqlglot
from sqlglot import exp

# Strictly permitted tables across PostgreSQL & DuckDB
ALLOWED_TABLES: Set[str] = {
    "active_incidents",
    "cloud_billing_hourly",
    "deployments",
    "telemetry_logs",
}

# Forbidden AST expression types (DML, DDL, Privilege/Admin operations)
FORBIDDEN_EXPRESSIONS = (
    exp.Drop,
    exp.Delete,
    exp.Update,
    exp.Insert,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Command,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
)


class SQLSecurityViolationError(PermissionError):
    """Raised when an agent or user attempts an unsafe or unauthorized SQL query."""
    pass


def validate_readonly_sql(sql_query: str, dialect: str = "postgres") -> Tuple[bool, str]:
    """
    Parses a SQL string into an Abstract Syntax Tree (AST) using sqlglot
    and enforces strict Read-Only, Single-Statement, Table-Whitelisted execution.
    """
    if not sql_query or not sql_query.strip():
        raise SQLSecurityViolationError("Empty SQL query is not permitted.")

    try:
        statements = sqlglot.parse(sql_query, read=dialect)
    except Exception as e:
        raise SQLSecurityViolationError(f"SQL syntax/parsing error blocked by AST guard: {e}")

    # 1. Block stacked queries (e.g., "SELECT 1; DROP TABLE deployments;")
    valid_stmts = [s for s in statements if s is not None]
    if len(valid_stmts) != 1:
        raise SQLSecurityViolationError(
            f"Stacked or multiple SQL statements blocked (found {len(valid_stmts)} statements). Only 1 SELECT allowed."
        )

    stmt = valid_stmts[0]

    # 2. Root statement must be a SELECT or UNION/CTE query
    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise SQLSecurityViolationError(
            f"Root SQL operation '{type(stmt).__name__.upper()}' is forbidden. Only read-only SELECT queries are permitted."
        )

    # 3. Walk the entire AST to ensure no forbidden nodes exist inside subqueries/CTEs
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_EXPRESSIONS):
            raise SQLSecurityViolationError(
                f"Forbidden SQL AST operation detected: {type(node).__name__.upper()}."
            )
        if isinstance(node, exp.Func) and node.name.lower() in {
            "pg_sleep", "pg_terminate_backend", "pg_cancel_backend", "pg_read_file", "read_csv", "read_parquet"
        }:
            raise SQLSecurityViolationError(
                f"Forbidden system/file function call blocked: {node.name}()"
            )

    # 4. Collect CTE alias names so we don't mistake CTEs for unauthorized tables
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE) if cte.alias_or_name}

    # 5. Verify all referenced tables belong to ALLOWED_TABLES
    referenced_tables = {
        table.name.lower()
        for table in stmt.find_all(exp.Table)
        if table.name and table.name.lower() not in cte_names
    }

    unauthorized = referenced_tables - ALLOWED_TABLES
    if unauthorized:
        raise SQLSecurityViolationError(
            f"Access to unauthorized table(s) blocked: {sorted(unauthorized)}. Allowed: {sorted(ALLOWED_TABLES)}"
        )

    return True, "AST SQL validation passed."