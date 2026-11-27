"""Build every outbound link from stored metadata. The model never writes URLs."""
from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse


def youtube_at(url: str, seconds: int | None) -> str:
    """Link to a YouTube video at a moment. Transcript timestamps can be offset
    from the video by up to a few minutes, so the UI labels these "around"."""
    if not url:
        return ""
    if seconds is None or seconds <= 0:
        return url
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "t"]
    query.append(("t", f"{int(seconds)}s"))
    return urlunparse(parts._replace(query=urlencode(query)))


def text_fragment(url: str, text: str) -> str:
    """Deep link to a passage with a text fragment (#:~:text=...).

    Browsers that support text fragments scroll to and highlight the words;
    others just open the page.
    """
    if not url or not text:
        return url
    words = text.split()[:6]
    if len(words) < 3:
        return url
    # Characters with special meaning inside a text directive must be escaped.
    snippet = quote(" ".join(words), safe="")
    snippet = snippet.replace("-", "%2D").replace(",", "%2C").replace("&", "%26")
    base = url.split("#", 1)[0]
    return f"{base}#:~:text={snippet}"


def fmt_time(seconds: int | None) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def source_link(chunk: dict, doc: dict) -> str:
    if chunk["kind"] == "podcast":
        return youtube_at(doc.get("youtube_url") or "", chunk.get("seconds")) or doc.get("url", "")
    return text_fragment(doc.get("url", ""), chunk.get("anchor", ""))
