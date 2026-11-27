"""Build, save and load the search index.

Files in INDEX_DIR:
    docs.json        document metadata (no text)
    chunks.jsonl     passages with attribution and provisional topic tags
    embeddings.npy   one normalized vector per passage
    meta.json        build info; "version" changes whenever the content changes
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from . import chunking, config, models
from .topics import TOPICS

log = logging.getLogger(__name__)

_STOP = set("""a an and are as at be but by for from has have he her his i if in into is it its
me my of on or our she so that the their them then there these they this to was we were what
when which who will with you your yeah um uh like just kind sort really know think going""".split())
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'+#.-]*")

# Provisional topic tagging by meaning similarity (replaced by the pre-generation pass).
TOPIC_MIN_SIM = 0.52
TOPIC_MARGIN = 0.025


def tokenize(text: str) -> list[str]:
    return [t.strip(".'-") for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _doc_meta(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "units"}


def tag_topics(emb: np.ndarray) -> list[list[str]]:
    topic_vecs = models.embed_passages([f"{t['name']}. {t['description']}" for t in TOPICS])
    sims = emb @ topic_vecs.T
    tags = []
    for row in sims:
        order = np.argsort(-row)
        best = row[order[0]]
        if best < TOPIC_MIN_SIM:
            tags.append([])
            continue
        picked = [TOPICS[order[0]]["id"]]
        if row[order[1]] >= max(TOPIC_MIN_SIM, best - TOPIC_MARGIN):
            picked.append(TOPICS[order[1]]["id"])
        tags.append(picked)
    return tags


def build(docs: list[dict], out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.INDEX_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    chunks = [c for d in docs for c in chunking.chunk_document(d)]
    by_id = {d["id"]: d for d in docs}
    # The title goes into the embedded text so a passage keeps its context.
    texts = [f"{by_id[c['doc_id']]['title']}\n{c['text']}" for c in chunks]
    log.info("Embedding %d passages from %d documents…", len(chunks), len(docs))
    emb = models.embed_passages(texts)
    for c, tags in zip(chunks, tag_topics(emb)):
        c["topics"] = tags
        c["topics_source"] = "similarity"

    digest = hashlib.sha256()
    for c in chunks:
        digest.update(c["id"].encode())
        digest.update(c["text"].encode())
    meta = {
        "version": digest.hexdigest()[:16],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embed_model": config.EMBED_MODEL,
        "chunk_target_words": config.CHUNK_TARGET_WORDS,
        "chunk_max_words": config.CHUNK_MAX_WORDS,
        "documents": len(docs),
        "podcasts": sum(1 for d in docs if d["kind"] == "podcast"),
        "newsletters": sum(1 for d in docs if d["kind"] == "newsletter"),
        "passages": len(chunks),
        "words": sum(c["words"] for c in chunks),
    }

    (out_dir / "docs.json").write_text(json.dumps([_doc_meta(d) for d in docs]))
    with (out_dir / "chunks.jsonl").open("w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")
    np.save(out_dir / "embeddings.npy", emb)
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("Index built in %.0fs: %s", time.time() - t0, meta)
    return meta


@dataclass
class Index:
    docs: dict[str, dict]
    chunks: list[dict]
    emb: np.ndarray
    meta: dict
    bm25: BM25Okapi = field(repr=False)
    pos: dict[str, int] = field(repr=False)
    generated: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, index_dir: Path | None = None, generated_dir: Path | None = None) -> "Index":
        index_dir = index_dir or config.INDEX_DIR
        docs = {d["id"]: d for d in json.loads((index_dir / "docs.json").read_text())}
        chunks = [json.loads(line) for line in (index_dir / "chunks.jsonl").open()]
        emb = np.load(index_dir / "embeddings.npy")
        meta = json.loads((index_dir / "meta.json").read_text())
        bm25 = BM25Okapi([tokenize(f"{docs[c['doc_id']]['title']} {c['text']}") for c in chunks])
        idx = cls(docs=docs, chunks=chunks, emb=emb, meta=meta, bm25=bm25,
                  pos={c["id"]: i for i, c in enumerate(chunks)})
        from . import generated
        idx.generated = generated.load(generated_dir or config.GENERATED_DIR, idx)
        return idx

    def chunk(self, chunk_id: str) -> dict | None:
        i = self.pos.get(chunk_id)
        return None if i is None else self.chunks[i]

    def doc_chunks(self, doc_id: str) -> list[dict]:
        return [c for c in self.chunks if c["doc_id"] == doc_id]
