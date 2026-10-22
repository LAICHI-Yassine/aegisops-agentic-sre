import pytest
from src.rag.chunker import load_and_chunk_runbooks
from src.rag.hybrid_retriever import AegisHybridRAG


@pytest.fixture(scope="module")
def rag_engine() -> AegisHybridRAG:
    engine = AegisHybridRAG()
    engine.index_runbooks()
    return engine


def test_parent_child_chunker_and_rag_guardrail() -> None:
    chunks = load_and_chunk_runbooks()
    assert len(chunks) >= 24

    poison_chunks = [c for c in chunks if "RB-POISON-012" in c.doc_id]
    assert len(poison_chunks) >= 1
    for pc in poison_chunks:
        assert pc.is_sanitized is True
        assert "[REDACTED_BY_AEGIS_RAG_GUARDRAIL]" in pc.parent_text
        assert "DROP TABLE deployments" not in pc.parent_text


def test_hybrid_reranker_beats_decoys_and_poison(rag_engine: AegisHybridRAG) -> None:
    # 1. Must rank RB-SRE-001 (Exit 137) above RB-SRE-005 (Exit 143) and RB-POISON-012
    oom_res = rag_engine.search(
        "payment-service OOMKilled_Exit137 memory working set > 8000MB HTTP 502",
        top_k=3,
        mode="hybrid_reranked",
        use_cache=False,
    )
    assert oom_res["results"][0]["doc_id"] == "RB-SRE-001-kubernetes-oomkilled-exit137"

    # 2. Must rank RB-DBA-003 (SQLSTATE 53300) above RB-DBA-007 (SQLSTATE 53100)
    db_res = rag_engine.search(
        "order-service PG_POOL_EXHAUSTED_53300 SQLSTATE 53300 idle in transaction timeout",
        top_k=3,
        mode="hybrid_reranked",
        use_cache=False,
    )
    assert db_res["results"][0]["doc_id"] == "RB-DBA-003-postgres-connection-pool-exhaustion"


def test_redis_rag_cache_hit_and_latency(rag_engine: AegisHybridRAG) -> None:
    query = "rag-inference-worker 16x g5.12xlarge LLM_RETRY_STORM_MAX_50 cost anomaly"
    cold = rag_engine.search(query, top_k=2, mode="hybrid_reranked", use_cache=True)
    warm = rag_engine.search(query, top_k=2, mode="hybrid_reranked", use_cache=True)

    assert warm["cache_hit"] is True
    assert warm["latency_ms"] < 15.0
    assert warm["results"][0]["doc_id"] == cold["results"][0]["doc_id"]