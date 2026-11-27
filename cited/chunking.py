"""Split documents into passages that keep attribution intact.

Podcast passages are built from whole speaker turns, so each passage knows who
spoke and when it starts. Newsletter passages are built from whole paragraphs
under one heading. A single turn or paragraph longer than the max is split on
sentence boundaries; it keeps its speaker and start time.
"""
from __future__ import annotations

import re

from . import config

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _words(text: str) -> int:
    return len(text.split())


def _split_long(text: str, max_words: int) -> list[str]:
    if _words(text) <= max_words:
        return [text]
    out, cur, n = [], [], 0
    for sent in _SENT_SPLIT.split(text):
        w = _words(sent)
        if cur and n + w > max_words:
            out.append(" ".join(cur))
            cur, n = [], 0
        cur.append(sent)
        n += w
    if cur:
        out.append(" ".join(cur))
    return out


def _expand_units(units: list[dict], max_words: int) -> list[dict]:
    expanded = []
    for u in units:
        for piece in _split_long(u["text"], max_words):
            expanded.append({**u, "text": piece})
    return expanded


def chunk_document(doc: dict, target: int | None = None, max_words: int | None = None) -> list[dict]:
    target = target or config.CHUNK_TARGET_WORDS
    max_words = max_words or config.CHUNK_MAX_WORDS
    units = _expand_units(doc["units"], max_words)
    podcast = doc["kind"] == "podcast"

    groups: list[list[dict]] = []
    cur: list[dict] = []
    n = 0
    for u in units:
        w = _words(u["text"])
        boundary = (not podcast) and cur and u.get("heading") != cur[-1].get("heading")
        if cur and (n + w > max_words or n >= target or boundary):
            groups.append(cur)
            cur, n = [], 0
        cur.append(u)
        n += w
    if cur:
        # Fold a tiny tail into the previous passage so we don't index fragments.
        if groups and n < target // 4 and sum(_words(x["text"]) for x in groups[-1]) + n <= max_words:
            groups[-1].extend(cur)
        else:
            groups.append(cur)

    chunks = []
    for i, g in enumerate(groups):
        if podcast:
            lines, last = [], None
            for u in g:
                if u["speaker"] != last:
                    lines.append(f"{u['speaker']}: {u['text']}")
                    last = u["speaker"]
                else:
                    lines[-1] += " " + u["text"]
            text = "\n".join(lines)
            speakers = list(dict.fromkeys(u["speaker"] for u in g))
            chunk = {
                "timestamp": g[0]["timestamp"],
                "seconds": g[0]["seconds"],
                "speakers": speakers,
                "heading": "",
            }
        else:
            text = "\n".join(u["text"] for u in g)
            chunk = {"timestamp": "", "seconds": None, "speakers": [], "heading": g[0].get("heading", "")}
        chunk.update({
            "id": f"{doc['id']}#{i}",
            "doc_id": doc["id"],
            "kind": doc["kind"],
            "text": text,
            # First words of the passage's own text, used for #:~:text= deep links.
            "anchor": " ".join(g[0]["text"].split()[:8]),
            "words": sum(_words(u["text"]) for u in g),
        })
        chunks.append(chunk)
    return chunks
