#!/usr/bin/env python3
"""Fetch the free sources and build the search index.

    python scripts/ingest.py              # use cached sources if present
    python scripts/ingest.py --refresh    # pull new episodes and posts first

Needs no API key. Downloads two small open models on first run (~100 MB).
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import config, index, sources  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="fetch new episodes and posts")
    ap.add_argument("--no-newsletter", action="store_true")
    ap.add_argument("--no-podcast", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    docs = []
    if not args.no_podcast:
        repo = config.DATA_DIR / "transcripts"
        if args.refresh or not (repo / "episodes").exists():
            repo = sources.ensure_transcripts(repo)
        docs += sources.load_episodes(repo)
    if not args.no_newsletter:
        posts = sources.fetch_newsletter_posts(refresh=args.refresh)
        docs += sources.newsletter_docs(posts)
    if not docs:
        sys.exit("No documents loaded.")
    meta = index.build(docs)
    print(f"Indexed {meta['documents']} documents ({meta['podcasts']} episodes, "
          f"{meta['newsletters']} posts) as {meta['passages']} passages.")


if __name__ == "__main__":
    main()
