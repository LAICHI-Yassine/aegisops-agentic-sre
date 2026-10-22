# Runbook RB-SRE-010: Redis Sentinel Cluster Split-Brain & Master Failover
**Alert Signatures:** `REDIS_READONLY_REPLICA`, `MASTERDOWN Link with MASTER is down`

## 1. Overview & Remediation
Network partition between Redis Sentinel nodes can cause split-brain writes. Set `min-replicas-to-write 1` and `min-replicas-max-lag 10` in `redis.conf`.
