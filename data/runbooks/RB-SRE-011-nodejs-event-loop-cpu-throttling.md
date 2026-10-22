# Runbook RB-SRE-011: CPU CFS Quota Throttling & Synchronous Regex ReDoS
**Alert Signatures:** `container_cpu_cfs_throttled_periods_total`, `EventLoopLag > 2000ms`

## 1. Overview & Remediation
High CPU throttling with flat memory usage (<1GB) indicates CPU limit starvation or catastrophic backtracking in a regular expression. Remove hard CPU CFS limits on latency-sensitive pods.
