"""Central configuration. Every value can be overridden with an environment variable."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where raw source material is cached (git-ignored: the transcript license asks
# us not to re-host the raw files as a dataset).
DATA_DIR = Path(os.getenv("CITED_DATA_DIR", ROOT / "data"))
# Search index built from DATA_DIR (git-ignored, rebuilt at deploy time).
INDEX_DIR = Path(os.getenv("CITED_INDEX_DIR", ROOT / "index"))
# Pre-generated content (summaries, topic pages, Q&A bank). Committed, because
# it costs money to produce and holds only summaries and short quotes.
GENERATED_DIR = Path(os.getenv("CITED_GENERATED_DIR", ROOT / "generated"))

TRANSCRIPTS_REPO = os.getenv(
    "CITED_TRANSCRIPTS_REPO", "https://github.com/aakashg/growth-podcast-transcripts.git"
)
NEWSLETTER_BASE = os.getenv("CITED_NEWSLETTER_BASE", "https://www.news.aakashg.com")

# Credit line the transcript license asks for, shown wherever quotes appear.
CREDIT_NAME = "The Growth Podcast with Aakash Gupta"
CREDIT_URL = "https://www.news.aakashg.com"

# Models. Pre-generation runs once (Batch API) on the most capable model; live
# answers run on a fast, cheap model. Both are overridable.
PREGEN_MODEL = os.getenv("CITED_PREGEN_MODEL", "claude-opus-5-5")
ANSWER_MODEL = os.getenv("CITED_ANSWER_MODEL", "claude-haiku-5-5")

EMBED_MODEL = os.getenv("CITED_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL = os.getenv("CITED_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")

# Chunking
CHUNK_TARGET_WORDS = int(os.getenv("CITED_CHUNK_WORDS", "260"))
CHUNK_MAX_WORDS = int(os.getenv("CITED_CHUNK_MAX_WORDS", "420"))

# Retrieval
CANDIDATES = int(os.getenv("CITED_CANDIDATES", "40"))   # per retriever, before fusion
RERANK_POOL = int(os.getenv("CITED_RERANK_POOL", "30"))  # fused results sent to the reranker
TOP_K = int(os.getenv("CITED_TOP_K", "8"))
MAX_PER_DOC = int(os.getenv("CITED_MAX_PER_DOC", "2"))   # diversity cap

# Saved-answer reuse: BOTH conditions must hold (see cited/cache.py).
CACHE_QUESTION_SIM = float(os.getenv("CITED_CACHE_QUESTION_SIM", "0.92"))
CACHE_SOURCE_OVERLAP = float(os.getenv("CITED_CACHE_SOURCE_OVERLAP", "0.5"))

# Abuse / cost protection for the free tier.
RATE_LIMIT_PER_HOUR = int(os.getenv("CITED_RATE_LIMIT_PER_HOUR", "20"))
MAX_HISTORY_TURNS = int(os.getenv("CITED_MAX_HISTORY_TURNS", "4"))
