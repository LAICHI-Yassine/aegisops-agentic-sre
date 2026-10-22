# Runbook RB-FINOPS-009: AWS NAT Gateway Data Processing & S3 Cross-Region Egress Spike
**Alert Signatures:** `NAT_GATEWAY_BYTES_HIGH`, `S3_EGRESS_COST_ANOMALY`

## 1. Overview & Remediation
High AWS NAT Gateway hourly charges occur when VPC workloads pull large model weights from S3 without a Gateway VPC Endpoint. Provision an `aws_vpc_endpoint` for S3 in `eu-west-1`.
