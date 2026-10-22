# Runbook RB-DBA-007: PostgreSQL Read-Replica WAL Lag & Disk Bloat (SQLSTATE 53100)
**Alert Signatures:** `PG_DISK_FULL_53100`, `replication_slot_lag_bytes > 50GB`

## 1. Overview & Remediation
Inactive logical replication slots prevent PostgreSQL from pruning WAL segments (`pg_wal`), leading to disk exhaustion (`SQLSTATE 53100`). Drop orphaned replication slots via `pg_drop_replication_slot()`.
