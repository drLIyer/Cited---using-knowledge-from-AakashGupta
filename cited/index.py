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
TOPIC_MIN_Z = 1.3
TOPIC_Z_MARGIN = 0.3


# Ad-like passages (course plugs, sponsor reads the transcripts didn't mark,
# bundle announcements). Ranked lower in search; never auto-added to notebooks.
_PROMO_STRONG = re.compile(
    r"(brought to you by|use (my|the) code|promo code|discount code|\b\d{1,2}% off\b|\$\d+ off\b|"
    r"i(?:'m| am) accepting (a group|applications)|enroll (now|today|at)|sign up (now|today) at|"
    r"link in the (description|show notes)|annual subscriber|become an annual|subscribe (now|today)|"
    r"my bundle|aakash'?s bundle|free year of)", re.I)
_PROMO_WEAK = re.compile(r"(cohort|maven\.com|sponsor|limited spots|apply (now|today)|giveaway|discount|coupon|waitlist)", re.I)


def is_promo(text: str) -> bool:
    strong = len(_PROMO_STRONG.findall(text))
    return strong >= 2 or (strong >= 1 and strong + len(_PROMO_WEAK.findall(text)) >= 2)


def tokenize(text: str) -> list[str]:
    return [t.strip(".'-") for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1]


def _doc_header(doc: dict) -> str:
    guests = ", ".join(g for g in doc.get("guests") or [] if g and g.lower() != "guest")
    return f"{doc['title']}" + (f" (with {guests})" if guests else "")


def _doc_meta(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "units"}


def tag_topics(emb: np.ndarray) -> list[list[str]]:
    """Provisional tags: score each topic relative to its own average.

    Raw similarity favors topics with broad descriptions (one topic took half
    the archive), so each topic's scores are standardized across all passages
    and a passage is tagged only where it stands out (z >= TOPIC_MIN_Z).
    """
    topic_vecs = models.embed_passages([f"{t['name']}. {t['description']}" for t in TOPICS])
    sims = emb @ topic_vecs.T
    z = (sims - sims.mean(axis=0)) / (sims.std(axis=0) + 1e-6)
    tags = []
    for row in z:
        order = np.argsort(-row)
        if row[order[0]] < TOPIC_MIN_Z:
            tags.append([])
            continue
        picked = [TOPICS[order[0]]["id"]]
        if row[order[1]] >= max(TOPIC_MIN_Z, row[order[0]] - TOPIC_Z_MARGIN):
            picked.append(TOPICS[order[1]]["id"])
        tags.append(picked)
    return tags


def retag(index_dir: Path | None = None) -> dict:
    """Recompute provisional topic tags on an existing index (no re-embedding)."""
    index_dir = index_dir or config.INDEX_DIR
    chunks = [json.loads(line) for line in (index_dir / "chunks.jsonl").open()]
    emb = np.load(index_dir / "embeddings.npy")
    for c, tags in zip(chunks, tag_topics(emb)):
        c["topics"] = tags
        c["topics_source"] = "similarity"
    with (index_dir / "chunks.jsonl").open("w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")
    from collections import Counter
    return dict(Counter(t for c in chunks for t in c["topics"]))


def write_seed(index_dir: Path | None = None) -> int:
    """Save the current index's vectors as the reusable embedding seed."""
    index_dir = index_dir or config.INDEX_DIR
    docs = {d["id"]: d for d in json.loads((index_dir / "docs.json").read_text())}
    chunks = [json.loads(line) for line in (index_dir / "chunks.jsonl").open()]
    emb = np.load(index_dir / "embeddings.npy")
    keys = [_text_key(f"{docs[c['doc_id']]['title']}\n{c['text']}") for c in chunks]
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(SEED_PATH, keys=np.array(keys), vecs=emb.astype(np.float16))
    return len(keys)


SEED_PATH = config.ROOT / "index_seed" / "embeddings.npz"


def _text_key(text: str) -> str:
    return hashlib.sha256(f"{config.EMBED_MODEL}\n{text}".encode()).hexdigest()[:20]


def _embed_with_seed(texts: list[str]) -> np.ndarray:
    """Embed passages, reusing vectors saved in index_seed/ for unchanged text.

    The seed holds only text fingerprints and vectors (no transcript text), so it
    can live in the repo and spare every deploy a long embedding run.
    """
    keys = [_text_key(t) for t in texts]
    known: dict[str, np.ndarray] = {}
    if SEED_PATH.exists():
        seed = np.load(SEED_PATH)
        known = dict(zip(seed["keys"].tolist(), seed["vecs"].astype(np.float32)))
    missing = [i for i, k in enumerate(keys) if k not in known]
    log.info("Embedding %d new passages (%d reused from seed)…", len(missing), len(texts) - len(missing))
    if missing:
        fresh = models.embed_passages([texts[i] for i in missing])
        for i, v in zip(missing, fresh):
            known[keys[i]] = v
    emb = np.stack([known[k] for k in keys]).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    if missing:
        SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(SEED_PATH, keys=np.array(keys), vecs=emb.astype(np.float16))
    return emb


def build(docs: list[dict], out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.INDEX_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    chunks = [c for d in docs for c in chunking.chunk_document(d)]
    by_id = {d["id"]: d for d in docs}
    # The title goes into the embedded text so a passage keeps its context.
    texts = [f"{by_id[c['doc_id']]['title']}\n{c['text']}" for c in chunks]
    emb = _embed_with_seed(texts)
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
    from . import updates
    updates.record_first_seen([d["id"] for d in docs], out_dir)
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
    topic_vecs: np.ndarray | None = field(default=None, repr=False)
    topic_mu: np.ndarray | None = field(default=None, repr=False)
    topic_sd: np.ndarray | None = field(default=None, repr=False)
    first_seen: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, index_dir: Path | None = None, generated_dir: Path | None = None) -> "Index":
        index_dir = index_dir or config.INDEX_DIR
        docs = {d["id"]: d for d in json.loads((index_dir / "docs.json").read_text())}
        chunks = [json.loads(line) for line in (index_dir / "chunks.jsonl").open()]
        emb = np.load(index_dir / "embeddings.npy")
        meta = json.loads((index_dir / "meta.json").read_text())
        for c in chunks:
            c["promo"] = is_promo(c["text"])
        bm25 = BM25Okapi([tokenize(f"{_doc_header(docs[c['doc_id']])} {c['text']}") for c in chunks])
        idx = cls(docs=docs, chunks=chunks, emb=emb, meta=meta, bm25=bm25,
                  pos={c["id"]: i for i, c in enumerate(chunks)})
        from . import generated
        idx.generated = generated.load(generated_dir or config.GENERATED_DIR, idx)
        from . import updates
        idx.first_seen = updates.load_first_seen(index_dir)
        idx.topic_vecs = models.embed_passages([f"{t['name']}. {t['description']}" for t in TOPICS])
        sims = emb @ idx.topic_vecs.T
        idx.topic_mu, idx.topic_sd = sims.mean(axis=0), sims.std(axis=0) + 1e-6
        return idx

    def question_topics(self, q_vec: np.ndarray, result_tags: list[list[str]] | None = None,
                        limit: int = 3, min_z: float = 1.0) -> list[str]:
        """Topics a question is about.

        Combines the question's own topic score (relative z, like passages) with
        how many of the top passages carry each tag: the question alone can be
        ambiguous ("price an AI feature" reads as AI evals to a small model).
        """
        z = (q_vec @ self.topic_vecs.T - self.topic_mu) / self.topic_sd
        votes = np.zeros(len(TOPICS))
        pos = {t["id"]: i for i, t in enumerate(TOPICS)}
        for tags in result_tags or []:
            for t in tags:
                if t in pos:
                    votes[pos[t]] += 1
        if votes.sum():
            votes = votes / votes.sum() * 2.0  # up to +2 for a topic all passages share
        score = z + votes
        order = np.argsort(-score)
        return [TOPICS[i]["id"] for i in order[:limit] if z[i] >= min_z or votes[i] >= 0.5]

    def context_text(self, chunk: dict) -> str:
        """Passage text with its source title and guests, for keyword search and reranking.

        Guests matter: some transcripts have no speaker names, so a question that
        names a guest would otherwise never match their words."""
        return f"{_doc_header(self.docs[chunk['doc_id']])}\n{chunk['text']}"

    def chunk(self, chunk_id: str) -> dict | None:
        i = self.pos.get(chunk_id)
        return None if i is None else self.chunks[i]

    def doc_chunks(self, doc_id: str) -> list[dict]:
        return [c for c in self.chunks if c["doc_id"] == doc_id]
