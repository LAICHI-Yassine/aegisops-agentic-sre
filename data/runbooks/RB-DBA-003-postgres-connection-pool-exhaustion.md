# Runbook RB-DBA-003: PostgreSQL Connection Pool Exhaustion (SQLSTATE 53300)
**Service Tier:** Transactional Core (`order-service`, `inventory-service`)
**Alert Signatures:** `PG_POOL_EXHAUSTED_53300`, `FATAL: remaining connection slots are reserved`, `HTTP 503 Service Unavailable`

## 1. Root Cause & Diagnostic Signatures
PostgreSQL error `53300` (`too_many_connections`) happens when application pods open more connections than `max_connections` (150) or fail to close idle transactions.
Primary trigger:
- Setting `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=0` (disabling timeout) while raising `DB_POOL_MAX_SIZE=150`, leaving zombie transactions holding row locks and exhausting all 150 slots.

## 2. Approved DBA Remediation Procedure (HITL Required)
1. Identify the commit that modified `DB_POOL_MAX_SIZE` or `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`.
2. Stage a database pool configuration reset and rolling restart (`action_type="reset_db_pool_config"`):
```bash
git revert <offending_commit_hash> && psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction';" && kubectl rollout restart deployment/order-service -n prod
```
