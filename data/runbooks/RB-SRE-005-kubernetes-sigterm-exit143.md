# Runbook RB-SRE-005: Kubernetes Pod Graceful Termination (Exit Code 143 - SIGTERM)
**Alert Signatures:** `ExitCode_143`, `SIGTERM`, `PodEvicted`

## 1. Overview & Remediation
Exit code 143 indicates a normal `SIGTERM` signal during horizontal pod autoscaler scale-down or node maintenance. Unlike Exit Code 137 (`OOMKilled`), Exit 143 is NOT a memory leak. Ensure `terminationGracePeriodSeconds` is set to 30s.
