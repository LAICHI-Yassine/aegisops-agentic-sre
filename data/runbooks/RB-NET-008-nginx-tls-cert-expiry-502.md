# Runbook RB-NET-008: Nginx Ingress mTLS Certificate Expiration & Handshake 502
**Alert Signatures:** `SSL_HANDSHAKE_FAILED`, `x509: certificate has expired`, `HTTP 502`

## 1. Overview & Remediation
If HTTP 502 occurs across all services simultaneously with `SSL_HANDSHAKE_FAILED` in ingress logs, rotate the mTLS certificate via `cert-manager` (`kubectl cert-manager renew --all`). Memory usage on pods will be normal (<40%).
