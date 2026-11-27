"""Exact-quote verification.

Every quote shown to a user, live or pre-generated, must appear in the source
passage it is attributed to. Comparison ignores case, whitespace and
typographic differences (curly vs straight quotes, dash styles); an ellipsis in
the quote may stand for skipped words, as long as the pieces appear in order.
"""
from __future__ import annotations

import re

_TRANS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")
_QUOTED = re.compile(r"[\"“]([^\"“”]{12,600})[\"”]")


def normalize(text: str) -> str:
    text = text.translate(_TRANS).lower()
    text = re.sub(r"[^\w\s'.,;:!?%$-]", " ", text)
    return " ".join(text.split())


def quote_in(quote: str, source: str) -> bool:
    if not quote or not quote.strip():
        return False
    src = normalize(source)
    pos = 0
    for piece in _ELLIPSIS.split(quote):
        piece = normalize(piece).strip(" .,;:")
        if not piece:
            continue
        idx = src.find(piece, pos)
        if idx == -1:
            return False
        pos = idx + len(piece)
    return True


def extract_quotes(text: str, min_words: int = 4) -> list[str]:
    """Quoted spans in an answer that are long enough to count as quotes."""
    return [q.strip() for q in _QUOTED.findall(text) if len(q.split()) >= min_words]


def check_answer(answer: str, sources: list[str]) -> dict:
    """Check every quote in an answer against the passages it could come from."""
    quotes = extract_quotes(answer)
    joined = "\n".join(sources)
    unverified = [q for q in quotes if not quote_in(q, joined)]
    return {"quotes": len(quotes), "unverified": unverified}
