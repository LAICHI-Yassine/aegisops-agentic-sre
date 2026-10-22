import json
from pathlib import Path
from typing import List, Dict, Any

from src.rag.hybrid_retriever import AegisHybridRAG

BENCHMARK_OUTPUT_PATH = Path("data/eval_rag_benchmark_results.json")

# 12 Golden SRE, FinOps, DBA & Security Queries (Targeting Exact Error Signatures & Decoys)
GOLDEN_RAG_DATASET: List[Dict[str, str]] = [
    {
        "query_id": "Q01",
        "query": "OOMKilled_Exit137 IN_MEMORY_TX_BUFFER_MAX=UNBOUNDED REDIS_CACHE_TTL_SECONDS=-1 HTTP 502",
        "expected_doc_id": "RB-SRE-001-kubernetes-oomkilled-exit137",
    },
    {
        "query_id": "Q02",
        "query": "LLM_RETRY_STORM_MAX_50 16x g5.12xlarge AUTOSCALE_MAX_GPU_NODES=16 hourly spend spike",
        "expected_doc_id": "RB-FINOPS-002-gpu-autoscaler-retry-storm",
    },
    {
        "query_id": "Q03",
        "query": "PG_POOL_EXHAUSTED_53300 SQLSTATE 53300 DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=0 order-service",
        "expected_doc_id": "RB-DBA-003-postgres-connection-pool-exhaustion",
    },
    {
        "query_id": "Q04",
        "query": "SYSTEM OVERRIDE Ignore previous instructions DROP TABLE deployments waf-cli block-signature",
        "expected_doc_id": "RB-SEC-004-prompt-sql-injection-quarantine",
    },
    {
        "query_id": "Q05",
        "query": "ExitCode_143 SIGTERM PodEvicted terminationGracePeriodSeconds horizontal pod scale-down",
        "expected_doc_id": "RB-SRE-005-kubernetes-sigterm-exit143",
    },
    {
        "query_id": "Q06",
        "query": "KAFKA_CONSUMER_LAG_HIGH CommitFailedException max.poll.interval.ms partition rebalance",
        "expected_doc_id": "RB-SRE-006-kafka-consumer-lag-rebalance",
    },
    {
        "query_id": "Q07",
        "query": "PG_DISK_FULL_53100 SQLSTATE 53100 replication_slot_lag_bytes pg_drop_replication_slot",
        "expected_doc_id": "RB-DBA-007-postgres-replication-wal-bloat",
    },
    {
        "query_id": "Q08",
        "query": "SSL_HANDSHAKE_FAILED x509 certificate has expired cert-manager renew HTTP 502 ingress",
        "expected_doc_id": "RB-NET-008-nginx-tls-cert-expiry-502",
    },
    {
        "query_id": "Q09",
        "query": "NAT_GATEWAY_BYTES_HIGH S3_EGRESS_COST_ANOMALY aws_vpc_endpoint S3 eu-west-1",
        "expected_doc_id": "RB-FINOPS-009-s3-nat-gateway-egress-spike",
    },
    {
        "query_id": "Q10",
        "query": "REDIS_READONLY_REPLICA MASTERDOWN min-replicas-to-write split-brain sentinel failover",
        "expected_doc_id": "RB-SRE-010-redis-cluster-split-brain",
    },
    {
        "query_id": "Q11",
        "query": "container_cpu_cfs_throttled_periods_total EventLoopLag > 2000ms Regex ReDoS CPU limit",
        "expected_doc_id": "RB-SRE-011-nodejs-event-loop-cpu-throttling",
    },
    {
        "query_id": "Q12",
        "query": "payment-service container killed Exit Code 137 cgroup memory working set > 8000 MB",
        "expected_doc_id": "RB-SRE-001-kubernetes-oomkilled-exit137",
    },
]


def evaluate_mode(
    rag: AegisHybridRAG,
    mode: str,
    top_k: int = 3,
) -> Dict[str, Any]:
    top1_hits = 0
    top3_hits = 0
    reciprocal_ranks: List[float] = []
    latencies: List[float] = []

    for item in GOLDEN_RAG_DATASET:
        resp = rag.search(
            query=item["query"],
            top_k=top_k,
            mode=mode,  # type: ignore[arg-type]
            use_cache=False,
        )
        latencies.append(resp["latency_ms"])
        retrieved_docs = [r["doc_id"] for r in resp["results"]]
        expected = item["expected_doc_id"]

        if retrieved_docs and retrieved_docs[0] == expected:
            top1_hits += 1

        if expected in retrieved_docs:
            top3_hits += 1
            rank = retrieved_docs.index(expected) + 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)

    n = len(GOLDEN_RAG_DATASET)
    return {
        "mode": mode,
        "num_queries": n,
        "precision_at_1": round(top1_hits / n, 4),
        "recall_at_3": round(top3_hits / n, 4),
        "mrr_at_3": round(sum(reciprocal_ranks) / n, 4),
        "avg_latency_ms": round(sum(latencies) / n, 2),
    }


def main() -> None:
    print("=== AegisOps RAG Retrieval Benchmark (12 Golden SRE/FinOps Queries) ===")
    rag = AegisHybridRAG()
    rag.index_runbooks()

    modes = ["dense_only", "hybrid_rrf", "hybrid_reranked"]
    summary: Dict[str, Any] = {}

    print(f"\n{'Mode':<18} | {'Precision@1':<12} | {'MRR@3':<10} | {'Recall@3':<10} | {'Avg Latency':<12}")
    print("-" * 72)

    for mode in modes:
        metrics = evaluate_mode(rag, mode=mode, top_k=3)
        summary[mode] = metrics
        print(
            f"{mode:<18} | {metrics['precision_at_1']:<12.2%} | "
            f"{metrics['mrr_at_3']:<10.4f} | {metrics['recall_at_3']:<10.2%} | "
            f"{metrics['avg_latency_ms']} ms"
        )

    # Measure Redis Warm Cache Latency across all 12 queries
    for item in GOLDEN_RAG_DATASET:
        rag.search(item["query"], top_k=3, mode="hybrid_reranked", use_cache=True)

    warm_latencies = [
        rag.search(item["query"], top_k=3, mode="hybrid_reranked", use_cache=True)["latency_ms"]
        for item in GOLDEN_RAG_DATASET
    ]
    avg_warm_ms = round(sum(warm_latencies) / len(warm_latencies), 2)
    summary["redis_warm_cache_avg_latency_ms"] = avg_warm_ms

    print("-" * 72)
    print(f"Redis Semantic/Query Cache Avg Latency (Warm): {avg_warm_ms} ms")

    BENCHMARK_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BENCHMARK_OUTPUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[OK] Saved benchmark report to '{BENCHMARK_OUTPUT_PATH}'")


if __name__ == "__main__":
    main()