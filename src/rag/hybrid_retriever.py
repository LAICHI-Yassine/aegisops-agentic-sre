import hashlib
import json
import os
import re
import time
import zlib
from typing import List, Dict, Any, Literal
from dotenv import load_dotenv
import redis
from qdrant_client import QdrantClient, models
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

from src.rag.chunker import RunbookChunk, load_and_chunk_runbooks

load_dotenv()

COLLECTION_NAME = "aegis_runbooks_hybrid"
DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"


class BM25SparseEncoder:
    """
    Deterministic lexical BM25 term-frequency sparse encoder for Qdrant.
    Preserves exact error codes, SQLSTATEs, and compound identifiers while Qdrant
    computes corpus-wide Inverse Document Frequency (Modifier.IDF) server-side.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, avg_doc_len: float = 90.0) -> None:
        self.k1 = k1
        self.b = b
        self.avg_doc_len = avg_doc_len

    @staticmethod
    def tokenize(text: str) -> List[str]:
        raw_tokens = re.findall(r"[a-z0-9_.\-]+", text.lower())
        expanded: List[str] = []
        for tok in raw_tokens:
            expanded.append(tok)
            # Also index sub-parts of snake_case or hyphenated technical codes
            if "_" in tok or "-" in tok:
                for sub in re.split(r"[_\-]+", tok):
                    if len(sub) >= 2:
                        expanded.append(sub)
        return expanded

    @staticmethod
    def _token_to_id(token: str) -> int:
        return zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF

    def encode_passage(self, text: str) -> models.SparseVector:
        tokens = self.tokenize(text)
        doc_len = max(len(tokens), 1)
        tf_ById: Dict[int, int] = {}
        for tok in tokens:
            tid = self._token_to_id(tok)
            tf_ById[tid] = tf_ById.get(tid, 0) + 1

        indices = sorted(tf_ById.keys())
        values = [
            round(
                (tf_ById[idx] * (self.k1 + 1.0))
                / (tf_ById[idx] + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))),
                4,
            )
            for idx in indices
        ]
        return models.SparseVector(indices=indices, values=values)

    def encode_query(self, text: str) -> models.SparseVector:
        tokens = self.tokenize(text)
        unique_ids = sorted({self._token_to_id(tok) for tok in tokens})
        values = [1.0 for _ in unique_ids]
        return models.SparseVector(indices=unique_ids, values=values)


class AegisHybridRAG:
    """
    Production Hybrid RAG Engine combining:
    - Qdrant Dense Vectors (BAAI/bge-small-en-v1.5)
    - Qdrant Sparse BM25 Vectors with Server-Side IDF & Reciprocal Rank Fusion (RRF)
    - ONNX Cross-Encoder Reranker (Xenova/ms-marco-MiniLM-L-6-v2)
    - Redis Query Cache
    """

    def __init__(self) -> None:
        self.qdrant = QdrantClient(
            host=os.getenv("QDRANT_HOST", "localhost"),
            port=int(os.getenv("QDRANT_PORT", 6333)),
        )
        self.redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True,
        )
        self.dense_encoder = TextEmbedding(model_name=DENSE_MODEL_NAME, cache_dir=".model_cache")
        self.sparse_encoder = BM25SparseEncoder()
        self.reranker = TextCrossEncoder(model_name=RERANKER_MODEL_NAME, cache_dir=".model_cache")

    def index_runbooks(self, chunks: List[RunbookChunk] | None = None) -> int:
        if chunks is None:
            chunks = load_and_chunk_runbooks()

        if self.qdrant.collection_exists(COLLECTION_NAME):
            self.qdrant.delete_collection(COLLECTION_NAME)

        self.qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "dense": models.VectorParams(
                    size=384,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse_bm25": models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                )
            },
        )

        child_texts = [c.child_text for c in chunks]
        dense_Embeddings = list(self.dense_encoder.embed(child_texts))

        points: List[models.PointStruct] = []
        for idx, (chunk, dense_vec) in enumerate(zip(chunks, dense_Embeddings)):
            sparse_vec = self.sparse_encoder.encode_passage(chunk.child_text)
            points.append(
                models.PointStruct(
                    id=idx + 1,
                    vector={
                        "dense": dense_vec.tolist(),
                        "sparse_bm25": sparse_vec,
                    },
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "doc_id": chunk.doc_id,
                        "title": chunk.title,
                        "section_header": chunk.section_header,
                        "alert_signatures": chunk.alert_signatures,
                        "child_text": chunk.child_text,
                        "parent_text": chunk.parent_text,
                        "is_sanitized": chunk.is_sanitized,
                    },
                )
            )

        self.qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
        # Clear any stale Redis RAG cache entries on re-index
        for key in self.redis.scan_iter("aegis:rag:cache:*"):
            self.redis.delete(key)

        return len(points)

    @staticmethod
    def _cache_key(query: str, top_k: int, mode: str) -> str:
        norm = f"{mode}::{top_k}::{query.strip().lower()}"
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
        return f"aegis:rag:cache:{digest}"

    def search(
        self,
        query: str,
        top_k: int = 3,
        prefetch_limit: int = 12,
        mode: Literal["dense_only", "hybrid_rrf", "hybrid_reranked"] = "hybrid_reranked",
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        cache_key = self._cache_key(query, top_k, mode)

        if use_cache:
            cached = self.redis.get(cache_key)
            if cached is not None:
                data = json.loads(cached)
                data["cache_hit"] = True
                data["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
                return data

        dense_query = next(self.dense_encoder.query_embed([query])).tolist()

        if mode == "dense_only":
            resp = self.qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=dense_query,
                using="dense",
                limit=top_k,
                with_payload=True,
            )
            candidates = resp.points
        else:
            sparse_query = self.sparse_encoder.encode_query(query)
            limit_to_fetch = prefetch_limit if mode == "hybrid_reranked" else top_k
            resp = self.qdrant.query_points(
                collection_name=COLLECTION_NAME,
                prefetch=[
                    models.Prefetch(
                        query=dense_query,
                        using="dense",
                        limit=prefetch_limit,
                    ),
                    models.Prefetch(
                        query=sparse_query,
                        using="sparse_bm25",
                        limit=prefetch_limit,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit_to_fetch,
                with_payload=True,
            )
            candidates = resp.points

        results: List[Dict[str, Any]] = []
        if mode == "hybrid_reranked" and candidates:
            candidate_texts = [pt.payload["child_text"] for pt in candidates]
            rerank_scores = [float(s) for s in self.reranker.rerank(query, candidate_texts)]

            scored_pairs = sorted(
                zip(candidates, rerank_scores),
                key=lambda item: item[1],
                reverse=True,
            )

            # Deduplicate by parent doc_id so the LLM receives distinct runbooks
            seen_docs = set()
            for pt, ce_score in scored_pairs:
                doc_id = pt.payload["doc_id"]
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                results.append(
                    {
                        "chunk_id": pt.payload["chunk_id"],
                        "doc_id": doc_id,
                        "title": pt.payload["title"],
                        "section_header": pt.payload["section_header"],
                        "rrf_score": round(float(pt.score), 4),
                        "reranker_score": round(ce_score, 4),
                        "is_sanitized": pt.payload["is_sanitized"],
                        "child_text": pt.payload["child_text"],
                        "parent_text": pt.payload["parent_text"],
                    }
                )
                if len(results) >= top_k:
                    break
        else:
            seen_docs = set()
            for pt in candidates:
                doc_id = pt.payload["doc_id"]
                if doc_id in seen_docs:
                    continue
                seen_docs.add(doc_id)
                results.append(
                    {
                        "chunk_id": pt.payload["chunk_id"],
                        "doc_id": doc_id,
                        "title": pt.payload["title"],
                        "section_header": pt.payload["section_header"],
                        "score": round(float(pt.score), 4),
                        "is_sanitized": pt.payload["is_sanitized"],
                        "child_text": pt.payload["child_text"],
                        "parent_text": pt.payload["parent_text"],
                    }
                )
                if len(results) >= top_k:
                    break

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        output = {
            "query": query,
            "mode": mode,
            "cache_hit": False,
            "latency_ms": latency_ms,
            "results": results,
        }

        if use_cache:
            self.redis.set(cache_key, json.dumps(output), ex=3600)

        return output