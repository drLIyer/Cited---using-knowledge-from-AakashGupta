#!/usr/bin/env python3
"""Pre-generate Cited's notes, topic pages and Q&A with Claude.

Pass 1 is "documents" (one request per episode/post). Pass 2 is "topics";
run it after pass 1 so topic bundles use the better topic tags.

Estimate cost (no key needed):
    python scripts/pregenerate.py estimate documents
    python scripts/pregenerate.py estimate topics

Option A, Claude Code on your own machine (uses your Claude plan, no API key):
    python scripts/pregenerate.py export documents
    bash pregen/run_with_claude_code.sh
    python scripts/pregenerate.py import

Option B, Batch API (needs ANTHROPIC_API_KEY; 50% off standard prices):
    python scripts/pregenerate.py submit documents
    python scripts/pregenerate.py collect <batch_id>
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import config, pregen  # noqa: E402
from cited.index import Index  # noqa: E402


def summarize(reports: list[dict]) -> None:
    ok = [r for r in reports if "error" not in r]
    errors = [r for r in reports if "error" in r]
    quotes = sum(r.get("quotes", 0) for r in ok)
    dropped = sum(r.get("dropped_quotes", 0) for r in ok)
    print(f"Stored {len(ok)} items; {len(errors)} failed.")
    if quotes:
        print(f"Quotes: {quotes} checked, {dropped} dropped as not exact ({dropped / quotes:.1%}).")
    qa_bad = sum(r.get("qa_quotes_unverified", 0) + r.get("qa_dropped", 0) for r in ok)
    if qa_bad:
        print(f"Q&A answers rejected (bad citation or quote): {qa_bad}")
    for r in errors[:20]:
        print("  failed:", r)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("estimate", "export", "submit"):
        p = sub.add_parser(name)
        p.add_argument("pass_", metavar="pass", choices=["documents", "topics"])
        p.add_argument("--only", nargs="*", help="limit to these doc or topic ids")
        p.add_argument("--limit", type=int, help="only the first N items (for a trial run)")
        p.add_argument("--model", default=config.PREGEN_MODEL)
    imp = sub.add_parser("import")
    imp.add_argument("--inputs", default=str(pregen.INPUTS_DIR))
    imp.add_argument("--outputs", default=str(config.DATA_DIR / "pregen_outputs"))
    col = sub.add_parser("collect")
    col.add_argument("batch_id")
    col.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    idx = Index.load()

    if args.cmd in ("estimate", "export", "submit"):
        items = pregen.work_items(idx, args.pass_, args.only)
        if args.limit:
            items = items[: args.limit]
        est = pregen.estimate(items, args.model, batch=True)
        if args.cmd == "estimate":
            print(json.dumps(est, indent=2))
            print("Claude Code (your plan) instead of the Batch API: no API charge; plan usage limits apply.")
            return
        if args.cmd == "export":
            out = pregen.export_inputs(items)
            print(f"Wrote {len(items)} inputs to {out}. Next: bash pregen/run_with_claude_code.sh")
            return
        print(f"Submitting {est['requests']} requests to {args.model}; estimated ${est['est_cost_usd']}.")
        print("Batch id:", pregen.submit_batch(items, model=args.model))
        return

    if args.cmd == "import":
        reports = pregen.import_outputs(idx, Path(args.outputs), Path(args.inputs) / "manifest.json")
        summarize(reports)
        return

    if args.cmd == "collect":
        summarize(pregen.collect_batch(idx, args.batch_id, wait=not args.no_wait))


if __name__ == "__main__":
    main()
