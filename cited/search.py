"""Hybrid retrieval: keyword (BM25) + meaning (embeddings), fused by rank,
re-scored by a cross-encoder, capped per source for diversity.

Confidence comes from the reranker score of the best passage and how many
distinct sources clear the bar. The thresholds below are provisional: they
should be calibrated against evals/questions.json, not taken on faith.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from . import config, links, models
from .index import Index, tokenize
from .topics import TOPIC_BY_ID

# Provisional confidence thresholds on sigmoid(reranker score).
HIGH = 0.80
MEDIUM = 0.35
SUPPORT = 0.50   # a passage "supports" an answer above this
MORE_MIN = 0.20  # minimum for "More on this topic" suggestions


def rrf(ranked_lists: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, i in enumerate(ranked):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + rank + 1)
    return [i for i, _ in sorted(scores.items(), key=lambda x: -x[1])]


def _candidates(idx: Index, query: str, q_vec: np.ndarray, allowed: np.ndarray | None) -> list[int]:
    n = config.CANDIDATES
    dense = idx.emb @ q_vec
    bm = idx.bm25.get_scores(tokenize(query))
    if allowed is not None:
        dense = np.where(allowed, dense, -np.inf)
        bm = np.where(allowed, bm, -np.inf)
    dense_top = [int(i) for i in np.argsort(-dense)[:n] if np.isfinite(dense[i])]
    bm_top = [int(i) for i in np.argsort(-bm)[:n] if np.isfinite(bm[i]) and bm[i] > 0]
    return rrf([dense_top, bm_top])[: config.RERANK_POOL]


def confidence(results: list[dict]) -> dict:
    if not results:
        return {"level": "low", "score": 0.0, "label": "Not covered in the free archive", "sources": 0}
    top = results[0]["relevance"]
    supporting = len({r["doc_id"] for r in results if r["relevance"] >= SUPPORT})
    if top >= HIGH and supporting >= 2:
        level, label = "high", f"Well covered: {supporting} sources"
    elif top >= MEDIUM:
        level, label = "medium", "Partly covered" if supporting < 2 else f"Covered by {supporting} sources"
    else:
        level, label = "low", "Not well covered in the free archive"
    return {"level": level, "score": round(top, 3), "label": label, "sources": supporting}


def present(idx: Index, chunk: dict, relevance: float | None = None) -> dict:
    """The public shape of a passage: everything the UI and exports need."""
    d = idx.docs[chunk["doc_id"]]
    out = {
        "chunk_id": chunk["id"],
        "doc_id": d["id"],
        "kind": d["kind"],
        "title": d["title"],
        "date": d["date"],
        "speakers": [s for s in chunk.get("speakers", []) if s],
        "timestamp": links.fmt_time(chunk.get("seconds")),
        "heading": chunk.get("heading", ""),
        "text": chunk["text"],
        "topics": chunk.get("topics", []),
        "url": links.source_link(chunk, d),
        "page_url": d.get("url", ""),
    }
    if relevance is not None:
        out["relevance"] = round(relevance, 4)
    return out


def search(idx: Index, query: str, top_k: int | None = None, kind: str | None = None,
           topic: str | None = None) -> dict:
    top_k = top_k or config.TOP_K
    allowed = None
    if kind or topic:
        allowed = np.array([(not kind or c["kind"] == kind) and (not topic or topic in c.get("topics", []))
                            for c in idx.chunks])
    q_vec = models.embed_queries([query])[0]
    pool = _candidates(idx, query, q_vec, allowed)
    texts = [f"{idx.docs[idx.chunks[i]['doc_id']]['title']}. {idx.chunks[i]['text']}" for i in pool]
    scores = models.rerank(query, texts)
    ranked = sorted(zip(pool, scores), key=lambda x: -x[1])

    results, more, per_doc = [], [], Counter()
    for i, s in ranked:
        c = idx.chunks[i]
        rel = models.sigmoid(s)
        if len(results) < top_k and per_doc[c["doc_id"]] < config.MAX_PER_DOC:
            per_doc[c["doc_id"]] += 1
            results.append(present(idx, c, rel))
        elif c["doc_id"] not in per_doc and rel >= MORE_MIN and all(m["doc_id"] != c["doc_id"] for m in more):
            more.append(present(idx, c, rel))
    return {"results": results, "more": more[:6], "confidence": confidence(results)}


def related(idx: Index, results: list[dict], asked: str = "", limit: int = 4) -> dict:
    """Suggested next steps: related topics and questions.

    Prefers pre-generated related questions; falls back to questions each
    source is known to answer; then to simple topic prompts.
    """
    weights: Counter = Counter()
    for r in results:
        for t in r.get("topics", []):
            weights[t] += r.get("relevance", 0.5)
    topic_ids = [t for t, _ in weights.most_common(3)]
    topics = [{"id": t, "name": TOPIC_BY_ID[t]["name"]} for t in topic_ids if t in TOPIC_BY_ID]

    questions: list[str] = []
    gen = idx.generated
    for t in topic_ids:
        questions += gen.get("topics", {}).get(t, {}).get("related_questions", [])[:3]
    for r in results:
        questions += gen.get("docs", {}).get(r["doc_id"], {}).get("questions_answered", [])[:1]
    if not questions:
        questions = [f"What do guests disagree about in {TOPIC_BY_ID[t]['name'].lower()}?" for t in topic_ids]
        seen_speakers = []
        for r in results:
            for s in r["speakers"]:
                if s not in ("Aakash Gupta", "Unidentified speaker") and s not in seen_speakers:
                    seen_speakers.append(s)
        questions += [f"What else does {s} recommend?" for s in seen_speakers[:2]]
    asked_l = asked.strip().lower()
    deduped = list(dict.fromkeys(q for q in questions if q.strip().lower() != asked_l))
    return {"topics": topics, "questions": deduped[:limit]}
