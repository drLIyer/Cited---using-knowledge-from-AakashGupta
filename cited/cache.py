"""Saved answers: reuse only when it is safe.

A saved answer (pre-generated Q&A, or an earlier live answer) is served only
when BOTH hold:
  1. the new question means nearly the same thing (embedding similarity >=
     CACHE_QUESTION_SIM), and
  2. the passages the saved answer cites still rank in the new question's top
     results (overlap >= CACHE_SOURCE_OVERLAP).
Condition 2 catches near-identical wording with a different meaning
("pricing AI for enterprise" vs "...for consumers"): those pull up different
passages, so the saved answer is not reused.

Entries are tied to the index version; rebuilding the index discards them.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

from . import config, models


class AnswerCache:
    def __init__(self, path: Path, index_version: str):
        self.path = path
        self.version = index_version
        self.entries: list[dict] = []
        self.vecs = np.zeros((0, 384), dtype=np.float32)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        if data.get("version") != self.version:
            return  # index changed: saved answers may cite passages that moved
        self.entries = data.get("entries", [])
        if self.entries:
            self.vecs = np.array([e["vec"] for e in self.entries], dtype=np.float32)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": self.version, "entries": self.entries}))
        tmp.replace(self.path)

    def add(self, question: str, answer: str, chunk_ids: list[str], origin: str,
            vec: np.ndarray | None = None, persist: bool = True) -> None:
        if vec is None:
            vec = models.embed_queries([question])[0]
        with self._lock:
            self.entries.append({"question": question, "answer": answer, "chunk_ids": chunk_ids,
                                 "origin": origin, "created": time.time(), "vec": vec.tolist()})
            self.vecs = np.vstack([self.vecs, vec[None, :].astype(np.float32)])
            if persist:
                self._save()

    def lookup(self, q_vec: np.ndarray, top_chunk_ids: list[str]) -> dict | None:
        if not self.entries:
            return None
        sims = self.vecs @ q_vec
        top = set(top_chunk_ids)
        for i in np.argsort(-sims)[:5]:
            if sims[i] < config.CACHE_QUESTION_SIM:
                break
            e = self.entries[int(i)]
            cited = e["chunk_ids"]
            overlap = len(top & set(cited)) / max(1, len(cited))
            if overlap >= config.CACHE_SOURCE_OVERLAP:
                return {**{k: v for k, v in e.items() if k != "vec"},
                        "similarity": round(float(sims[i]), 3), "overlap": round(overlap, 2)}
        return None


def seed_from_generated(cache: AnswerCache, generated: dict) -> int:
    """Load the pre-generated Q&A bank into the cache (in memory only)."""
    items = [(qa, t) for t, page in generated.get("topics", {}).items() for qa in page.get("qa", [])]
    if not items:
        return 0
    known = {e["question"] for e in cache.entries}
    items = [(qa, t) for qa, t in items if qa["question"] not in known]
    if not items:
        return 0
    vecs = models.embed_queries([qa["question"] for qa, _ in items])
    for (qa, t), v in zip(items, vecs):
        cache.add(qa["question"], qa["answer"], qa["chunk_ids"], origin=f"pregenerated:{t}",
                  vec=v, persist=False)
    return len(items)
