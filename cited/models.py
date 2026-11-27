"""Lazy-loaded local models: the embedding model and the reranker.

Both run on CPU via ONNX (fastembed), so there is no PyTorch dependency and
no per-query cost.
"""
from __future__ import annotations

import math
import threading

import numpy as np

from . import config

_lock = threading.Lock()
_embedder = None
_reranker = None


def embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            _embedder = TextEmbedding(config.EMBED_MODEL)
    return _embedder


def reranker():
    global _reranker
    with _lock:
        if _reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            _reranker = TextCrossEncoder(config.RERANK_MODEL)
    return _reranker


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


def embed_passages(texts: list[str], batch_size: int = 64) -> np.ndarray:
    vecs = list(embedder().passage_embed(texts, batch_size=batch_size))
    return _normalize(np.array(vecs, dtype=np.float32))


def embed_queries(texts: list[str]) -> np.ndarray:
    vecs = list(embedder().query_embed(texts))
    return _normalize(np.array(vecs, dtype=np.float32))


def rerank(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    return [float(s) for s in reranker().rerank(query, passages)]


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
