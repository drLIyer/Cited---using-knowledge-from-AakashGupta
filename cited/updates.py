"""New episodes and posts for a reader's notebooks.

Cited records when it first saw each document (first_seen.json). When a reader
comes back, the browser sends a summary of each notebook (its questions, the
passages saved in it, its topics) and when it was last checked. Documents that
arrived since then are scored against the notebook with the same reranker
search uses, and strong matches are returned for the browser to add.

Nothing about the notebook is stored on the server.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import config, models, search
from .index import Index
from .topics import STARTERS, TOPIC_BY_ID

FIRST_SEEN_FILE = "first_seen.json"
SEED_FIRST_SEEN = config.ROOT / "index_seed" / FIRST_SEEN_FILE

MIN_RELEVANCE = 0.5      # sigmoid(reranker) a new passage must reach for a notebook
MAX_PER_NOTEBOOK = 3     # at most this many additions per notebook per check
STALE_DAYS = 30          # a document published long before Cited first saw it is not "new"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_first_seen(index_dir: Path | None = None) -> dict[str, str]:
    for path in ((index_dir or config.INDEX_DIR) / FIRST_SEEN_FILE, SEED_FIRST_SEEN):
        if path.exists():
            return json.loads(path.read_text())
    return {}


def record_first_seen(doc_ids: list[str], index_dir: Path | None = None, when: str | None = None) -> dict[str, str]:
    """Stamp documents Cited hasn't seen before; keep earlier stamps unchanged."""
    seen = load_first_seen(index_dir)
    stamp = when or now_iso()
    for d in doc_ids:
        seen.setdefault(d, stamp)
    path = (index_dir or config.INDEX_DIR) / FIRST_SEEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=0, sort_keys=True))
    return seen


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def new_doc_ids(idx: Index, first_seen: dict[str, str], since: str) -> list[str]:
    """Documents first seen after `since` that were also published recently.

    The publication check stops an old post (e.g. one a feed change suddenly
    exposes) from being treated as new.
    """
    try:
        cutoff = _parse(since)
    except ValueError:
        return []
    out = []
    for doc_id, seen in first_seen.items():
        doc = idx.docs.get(doc_id)
        if doc is None or _parse(seen) <= cutoff:
            continue
        published = doc.get("date") or ""
        try:
            pub = datetime.fromisoformat(published[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if pub >= _parse(seen) - timedelta(days=STALE_DAYS):
            out.append(doc_id)
    return out


def notebook_queries(nb: dict, idx: Index) -> list[str]:
    """What a notebook is about: its questions; else its topics; else its sources' titles.

    Each is scored on its own: the reranker judges one question against one
    passage well, and a long merged query badly.
    """
    qs = [q.strip() for q in nb.get("questions", []) if isinstance(q, str) and q.strip()][:6]
    if not qs:  # a topic notebook: use the topic's own starter questions
        for t in nb.get("topics", [])[:3]:
            qs += STARTERS.get(t, [])
    if not qs:
        for cid in nb.get("chunk_ids", [])[:5]:
            c = idx.chunk(cid)
            if c:
                qs.append(idx.docs[c["doc_id"]]["title"])
    if not qs and nb.get("title"):
        qs.append(str(nb["title"]))
    return [q[:300] for q in qs]


def suggest_for_notebook(idx: Index, nb: dict, candidate_docs: list[str]) -> list[dict]:
    queries = notebook_queries(nb, idx)
    if not queries or not candidate_docs:
        return []
    already = {c.split("#")[0] for c in nb.get("chunk_ids", [])} | set(nb.get("doc_ids", []))
    cand = set(candidate_docs) - already
    pos = [i for i, c in enumerate(idx.chunks) if c["doc_id"] in cand and not c.get("promo")]
    if not pos:
        return []
    qvecs = models.embed_queries(queries)
    best: dict[str, tuple[float, int, str]] = {}
    for q, qv in zip(queries, qvecs):
        sims = idx.emb[pos] @ qv
        shortlist = [pos[i] for i in np.argsort(-sims)[:12]]
        scores = models.rerank(q, [idx.context_text(idx.chunks[i]) for i in shortlist])
        for i, s in zip(shortlist, scores):
            rel = models.sigmoid(s)
            doc = idx.chunks[i]["doc_id"]
            if rel >= MIN_RELEVANCE and rel > best.get(doc, (0.0, -1, ""))[0]:
                best[doc] = (rel, i, q)
    ranked = sorted(best.values(), key=lambda x: -x[0])[:MAX_PER_NOTEBOOK]
    out = []
    for rel, i, q in ranked:
        item = search.present(idx, idx.chunks[i], rel)
        item["matched"] = q
        out.append(item)
    return out


def check(idx: Index, notebooks: list[dict], first_seen: dict[str, str]) -> dict:
    out = {}
    for nb in notebooks[:20]:
        since = nb.get("since") or ""
        candidates = new_doc_ids(idx, first_seen, since) if since else []
        out[str(nb.get("id", ""))[:40]] = suggest_for_notebook(idx, nb, candidates)
    return {"checked_at": now_iso(), "notebooks": out}
