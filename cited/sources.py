"""Load the two free sources into one document shape.

Podcast: Aakash Gupta's public transcript repo (one markdown file per episode,
speaker turns with timestamps). Newsletter: the free posts on his Substack,
fetched from Substack's public posts API. Paid posts are skipped: the public
API returns only a teaser for them.

Every document is a dict:
    id, kind ("podcast" | "newsletter"), title, subtitle, date, url,
    youtube_url (podcast), guests, tags, units
A unit is the smallest piece we keep attribution for:
    podcast    -> {"speaker", "timestamp", "seconds", "text"}
    newsletter -> {"heading", "text"}
"""
from __future__ import annotations

import html
import json
import logging
import re
import subprocess
import time
from html.parser import HTMLParser
from pathlib import Path

import yaml

from . import config

log = logging.getLogger(__name__)

TURN_RE = re.compile(r"^\*\*(?P<speaker>[^*]+?)\*\*\s*\((?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\)\s*$")
SPONSOR_MARK = "<!-- sponsor -->"


# ── Podcast transcripts ────────────────────────────────────────────────────────

def ensure_transcripts(dest: Path | None = None) -> Path:
    """Clone (or update) the public transcript repo into the data directory."""
    dest = dest or config.DATA_DIR / "transcripts"
    if (dest / ".git").exists():
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=False)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "-q", config.TRANSCRIPTS_REPO, str(dest)], check=True
        )
    return dest


def ts_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta = yaml.safe_load(raw[3:end]) or {}
    body = raw[end + 4:]
    return meta, body


def parse_episode(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(raw)

    units: list[dict] = []
    current: dict | None = None
    sponsor_next = False
    skip_current = False
    buf: list[str] = []

    def flush():
        if current is not None and not skip_current:
            text = " ".join(" ".join(buf).split())
            if text:
                units.append({**current, "text": text})

    for line in body.splitlines():
        stripped = line.strip()
        if stripped == SPONSOR_MARK:
            sponsor_next = True
            continue
        m = TURN_RE.match(stripped)
        if m:
            flush()
            ts = m.group("ts")
            current = {"speaker": m.group("speaker").strip(), "timestamp": ts, "seconds": ts_to_seconds(ts)}
            skip_current = sponsor_next
            sponsor_next = False
            buf = []
            continue
        if current is not None and stripped:
            buf.append(stripped)
    flush()

    guests = meta.get("guests") or []
    if isinstance(guests, str):
        guests = [g.strip() for g in guests.split(",") if g.strip()]
    speaker_names = str(meta.get("speaker_names", ""))
    if speaker_names.startswith("none"):
        # Speakers were not separated in this transcript: never guess who spoke.
        for u in units:
            u["speaker"] = "Unidentified speaker"

    slug = path.stem
    return {
        "id": f"pod:{slug}",
        "kind": "podcast",
        "title": str(meta.get("title", slug)).strip(),
        "subtitle": str(meta.get("subtitle") or "").strip(),
        "date": str(meta.get("date", ""))[:10],
        "url": meta.get("substack_url") or "",
        "youtube_url": meta.get("youtube_url") or "",
        "guests": [str(g) for g in guests],
        "tags": [],
        "speaker_names": speaker_names,
        "units": units,
    }


def load_episodes(repo_dir: Path) -> list[dict]:
    files = sorted((repo_dir / "episodes").glob("*.md"))
    docs = [parse_episode(p) for p in files]
    log.info("Parsed %d podcast episodes", len(docs))
    return [d for d in docs if d["units"]]


# ── Newsletter (free posts only) ──────────────────────────────────────────────

class _BlockParser(HTMLParser):
    """Turn Substack post HTML into (heading, paragraph) units.

    Skips subscribe widgets, buttons, image captions and embedded media.
    """
    BLOCK = {"p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP_CLASSES = ("subscription-widget", "button-wrapper", "captioned-image", "image-caption",
                    "footnote-anchor", "embedded-post", "youtube-wrap", "tweet", "pencraft",
                    "digest-post-embed", "paywall")
    SKIP_TAGS = {"script", "style", "figure", "figcaption", "button", "form", "svg", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.units: list[dict] = []
        self.heading = ""
        self._tag_stack: list[tuple[str, bool]] = []
        self._buf: list[str] = []
        self._block: str | None = None

    def _skipping(self) -> bool:
        return any(skip for _, skip in self._tag_stack)

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        skip = tag in self.SKIP_TAGS or any(c in cls for c in self.SKIP_CLASSES)
        if tag in ("br", "img", "hr", "source", "input", "meta", "link"):
            return
        self._tag_stack.append((tag, skip))
        if tag in self.BLOCK and not self._skipping() and self._block is None:
            self._block = tag
            self._buf = []

    def handle_endtag(self, tag):
        # Pop up to and including the matching tag (HTML can be sloppy).
        for i in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[i][0] == tag:
                del self._tag_stack[i:]
                break
        if tag == self._block:
            text = " ".join(" ".join(self._buf).split())
            self._block = None
            if not text:
                return
            if tag in self.HEADINGS:
                self.heading = text
            elif not _is_boilerplate(text):
                self.units.append({"heading": self.heading, "text": text})

    def handle_data(self, data):
        if self._block is not None and not self._skipping():
            self._buf.append(data)


_BOILERPLATE = re.compile(
    r"^(subscribe|share|leave a comment|thanks for reading|upgrade to paid|"
    r"get \d+% off|this post is for paid subscribers|listen now|watch now)\b",
    re.I,
)


def _is_boilerplate(text: str) -> bool:
    return len(text) < 400 and bool(_BOILERPLATE.match(text))


def html_to_units(body_html: str) -> list[dict]:
    p = _BlockParser()
    p.feed(body_html or "")
    p.close()
    return p.units


def fetch_newsletter_posts(cache_path: Path | None = None, refresh: bool = False,
                           client=None, sleep: float = 0.6) -> list[dict]:
    """Fetch every post listing from the Substack API and keep free newsletters.

    Results are cached to disk; pass refresh=True to fetch again.
    """
    import httpx

    cache_path = cache_path or config.DATA_DIR / "newsletter" / "posts.json"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text())

    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=30,
                                    headers={"User-Agent": "Cited/0.1 (+research reader)"})
    seen: dict[int, dict] = {}
    cursor: str | None = None
    try:
        for _ in range(500):
            url = f"{config.NEWSLETTER_BASE}/api/v1/posts?limit=50"
            if cursor:
                url += f"&cursor={cursor}"
            for attempt in range(6):
                r = client.get(url)
                if r.status_code == 429:
                    wait = int(r.headers.get("retry-after", "0") or 0) or 10 * (attempt + 1)
                    log.info("Substack rate limit, waiting %ss", wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                break
            else:
                raise RuntimeError("Substack kept rate-limiting; try again later")
            data = r.json()
            batch = data.get("posts", []) if isinstance(data, dict) else data
            new = [p for p in batch if p.get("id") not in seen]
            for p in batch:
                seen[p["id"]] = p
            cursor = data.get("nextCursor") if isinstance(data, dict) else None
            if not batch or not cursor or not new:
                break
            time.sleep(sleep)
    finally:
        if own_client:
            client.close()

    free = [p for p in seen.values()
            if p.get("audience") == "everyone" and p.get("type") == "newsletter"
            and (p.get("body_html") or "")]
    keep = ["id", "title", "subtitle", "slug", "post_date", "canonical_url", "body_html",
            "audience", "type", "postTags", "wordcount"]
    slim = [{k: p.get(k) for k in keep} for p in free]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(slim))
    log.info("Fetched %d listings, kept %d free newsletter posts", len(seen), len(slim))
    return slim


def newsletter_docs(posts: list[dict]) -> list[dict]:
    docs = []
    for p in posts:
        units = html_to_units(p.get("body_html") or "")
        if not units:
            continue
        tags = [t.get("name") for t in (p.get("postTags") or []) if t.get("name")]
        docs.append({
            "id": f"news:{p.get('slug') or p['id']}",
            "kind": "newsletter",
            "title": html.unescape(p.get("title") or "Untitled"),
            "subtitle": html.unescape(p.get("subtitle") or ""),
            "date": (p.get("post_date") or "")[:10],
            "url": p.get("canonical_url") or f"{config.NEWSLETTER_BASE}/p/{p.get('slug')}",
            "youtube_url": "",
            "guests": [],
            "tags": tags,
            "units": units,
        })
    log.info("Built %d newsletter documents", len(docs))
    return docs
