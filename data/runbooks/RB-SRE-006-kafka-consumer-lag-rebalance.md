# Runbook RB-SRE-006: Kafka Consumer Group Lag & Partition Rebalance Storm
**Alert Signatures:** `KAFKA_CONSUMER_LAG_HIGH`, `CommitFailedException`, `RebalanceInProgress`

## 1. Overview & Remediation
When `max.poll.interval.ms` is exceeded during slow batch processing, Kafka triggers a partition rebalance storm. Increase `max.poll.interval.ms` to 600000 and reduce `max.poll.records` to 100.
