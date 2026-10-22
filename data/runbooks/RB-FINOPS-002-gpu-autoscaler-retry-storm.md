# Runbook RB-FINOPS-002: Runaway GPU Cluster Autoscaling & LLM Token Retry Storm
**Service Tier:** GenAI Inference Workers (`rag-inference-worker`, `llm-batch-processor`)
**Alert Signatures:** `LLM_RETRY_STORM_MAX_50`, `AWS Cost Anomaly > 300%`, `g5.12xlarge`, `HTTP 429 Too Many Requests`

## 1. Root Cause & FinOps Impact Analysis
Runaway cloud spend on GPU inference clusters (`g5.12xlarge` at ~$40/hr per node) typically occurs when:
- A deployment increases `MAX_AGENT_RETRIES` (from `2` to `50`) without exponential backoff or circuit breaking.
- `AUTOSCALE_MAX_GPU_NODES` is raised to `16` instances with spot-fallback disabled, driving hourly spend from ~$14.50/hr to >$500/hr and LLM token consumption above 15M tokens/hour.

| Cost / Telemetry Signal | Baseline | Runaway Anomaly |
| :--- | :--- | :--- |
| Instance Type & Count | 2x `g5.2xlarge` | 16x `g5.12xlarge` |
| Hourly Cloud Spend | ~$14.50 / hr | > $500.00 / hr (>30x spike) |
| Error Signature | `NONE` | `LLM_RETRY_STORM_MAX_50` (HTTP 429) |

## 2. Approved FinOps Containment Procedure (HITL Required)
1. Correlate the cost spike timestamp against `deployments` for `rag-inference-worker`.
2. Stage an immediate GPU scale-down and config revert via `stage_remediation_action` (`action_type="scale_down_gpu_nodes"`, `target_replica_count=2`):
```bash
git revert <offending_commit_hash> && kubectl scale deployment/rag-inference-worker --replicas=2 -n prod
```
