# Runbook RB-SRE-001: Kubernetes Pod OOMKilled (Exit Code 137) & Memory Leak Remediation
**Service Tier:** Tier-1 Critical Microservices (`payment-service`, `checkout-service`)
**Alert Signatures:** `OOMKilled_Exit137`, `container_memory_working_set_bytes > 95%`, `HTTP 502 Bad Gateway`

## 1. Root Cause & Diagnostic Signatures
When a container exceeds its cgroup memory limit (8192 MB), the Linux kernel OOM Killer terminates the process with `SIGKILL` (`Exit Code 137`).
Common causes in production:
- Unbounded in-memory transaction buffers (`IN_MEMORY_TX_BUFFER_MAX=UNBOUNDED`).
- Disabling Redis cache TTL eviction (`REDIS_CACHE_TTL_SECONDS=-1`), causing local heap accumulation.

| Metric / Signal | Normal Baseline | Critical Threshold (P1) |
| :--- | :--- | :--- |
| Memory Working Set | 900 MB - 1,400 MB | >= 8,000 MB (Limit Hit) |
| Latency p99 | < 65 ms | > 4,000 ms |
| HTTP Status | 200 OK | 502 Bad Gateway |

## 2. Approved Standard Remediation Procedure (HITL Required)
Do NOT vertically scale pod memory limits if a recent commit introduced an unbounded cache leak.
1. Query recent deployments via MCP `get_recent_deployments` to identify commits modifying `REDIS_CACHE_TTL_SECONDS` or `IN_MEMORY_TX_BUFFER_MAX`.
2. Stage a commit rollback and pod rollout restart using `stage_remediation_action` with `action_type="rollback_commit"`:
```bash
git revert <offending_commit_hash> && kubectl rollout undo deployment/payment-service -n prod
```
3. Verify memory stabilizes below 1,500 MB and p99 latency drops under 100 ms.
