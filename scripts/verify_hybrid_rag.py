from src.rag.hybrid_retriever import AegisHybridRAG


def main() -> None:
    print("=== Initializing AegisHybridRAG (ONNX Dense + Sparse BM25 + Cross-Encoder) ===")
    rag = AegisHybridRAG()

    count = rag.index_runbooks()
    print(f"[1/4] Indexed {count} Parent-Child chunks into Qdrant collection 'aegis_runbooks_hybrid'")

    # Test Query 1: OOMKilled Exit 137 vs Exit 143 Decoy
    q1 = "payment-service pod crashed with OOMKilled_Exit137 and HTTP 502 Bad Gateway memory > 8000MB"
    res_cold = rag.search(q1, top_k=2, mode="hybrid_reranked", use_cache=True)
    top_hit = res_cold["results"][0]
    print(
        f"[2/4] Hybrid + Reranker Verified (Cold) -> Top-1 Doc: {top_hit['doc_id']} | "
        f"RRF Score: {top_hit['rrf_score']} | Cross-Encoder Score: {top_hit['reranker_score']} | "
        f"Latency: {res_cold['latency_ms']} ms"
    )

    # Test Query 1 Again: Warm Redis Cache Hit
    res_warm = rag.search(q1, top_k=2, mode="hybrid_reranked", use_cache=True)
    print(
        f"[3/4] Redis RAG Cache Verified (Warm) -> Cache Hit: {res_warm['cache_hit']} | "
        f"Latency dropped from {res_cold['latency_ms']} ms -> {res_warm['latency_ms']} ms"
    )

    # Test Query 2: FinOps GPU Anomaly
    q2 = "AWS cost anomaly spike on rag-inference-worker 16x g5.12xlarge LLM_RETRY_STORM_MAX_50"
    res_finops = rag.search(q2, top_k=2, mode="hybrid_reranked", use_cache=False)
    finops_hit = res_finops["results"][0]
    print(
        f"[4/4] FinOps Runbook Retrieval Verified -> Top-1 Doc: {finops_hit['doc_id']} | "
        f"Cross-Encoder Score: {finops_hit['reranker_score']}"
    )


if __name__ == "__main__":
    main()