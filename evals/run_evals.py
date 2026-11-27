#!/usr/bin/env python3
"""Evaluate retrieval, confidence and links. No API key needed.

    python evals/run_evals.py                 # full report
    python evals/run_evals.py --save          # also write evals/report.json

Metrics:
  hit@k          expected document appears in the top k passages (specific,
                 newsletter and broad questions that list expectations)
  mrr            mean reciprocal rank of the first expected document
  topic_match    for broad questions with a topic: top passages tagged with it
  confidence     how confidence levels split by category; out-of-scope
                 questions should be "low", in-scope should not
  diversity      distinct sources among the top 8
  links          every result has a link; podcast links carry a timestamp
  latency        search time per question (p50 / p95)
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cited import search  # noqa: E402
from cited.index import Index  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--k", type=int, default=8)
    args = ap.parse_args()

    questions = json.loads((ROOT / "evals" / "questions.json").read_text())["questions"]
    idx = Index.load()
    search.search(idx, "warm up")  # load models before timing

    rows, latencies = [], []
    for item in questions:
        t0 = time.time()
        out = search.search(idx, item["q"], top_k=args.k)
        latencies.append(time.time() - t0)
        res = out["results"]
        docs = [r["doc_id"] for r in res]
        row = {"q": item["q"], "cat": item["cat"], "confidence": out["confidence"]["level"],
               "top_score": out["confidence"]["score"], "top_doc": docs[0] if docs else None,
               "distinct_sources": len(set(docs))}
        if item.get("expect"):
            ranks = [i for i, d in enumerate(docs) if d in item["expect"]]
            row["hit"] = bool(ranks)
            row["rr"] = 1 / (ranks[0] + 1) if ranks else 0.0
        if item.get("topic"):
            row["topic_match"] = sum(item["topic"] in r["topics"] for r in res) / max(1, len(res))
        row["links_ok"] = all(r["url"] for r in res) and all(
            "t=" in r["url"] for r in res if r["kind"] == "podcast" and r["has_video"] and r["timestamp"] not in ("", "0:00"))
        rows.append(row)

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)

    def mean(xs):
        xs = list(xs)
        return round(sum(xs) / len(xs), 3) if xs else None

    report = {"k": args.k, "questions": len(rows), "categories": {}}
    for cat, rs in by_cat.items():
        exp = [r for r in rs if "hit" in r]
        report["categories"][cat] = {
            "n": len(rs),
            "hit_at_k": mean(r["hit"] for r in exp),
            "mrr": mean(r["rr"] for r in exp),
            "topic_match": mean(r["topic_match"] for r in rs if "topic_match" in r),
            "confidence": dict(Counter(r["confidence"] for r in rs)),
            "mean_top_score": mean(r["top_score"] for r in rs),
            "distinct_sources": mean(r["distinct_sources"] for r in rs),
        }
    in_scope = [r for r in rows if r["cat"] != "out_of_scope"]
    oos = [r for r in rows if r["cat"] == "out_of_scope"]
    report["summary"] = {
        "hit_at_k": mean(r["hit"] for r in rows if "hit" in r),
        "mrr": mean(r["rr"] for r in rows if "rr" in r),
        "in_scope_marked_low": mean(r["confidence"] == "low" for r in in_scope),
        "out_of_scope_marked_low": mean(r["confidence"] == "low" for r in oos),
        "links_ok": mean(r["links_ok"] for r in rows),
        "latency_p50_s": round(statistics.median(latencies), 2),
        "latency_p95_s": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2),
    }
    misses = [r for r in rows if r.get("hit") is False]
    report["misses"] = [{"q": r["q"], "got": r["top_doc"]} for r in misses]
    report["oos_not_low"] = [{"q": r["q"], "confidence": r["confidence"], "score": r["top_score"]}
                             for r in oos if r["confidence"] != "low"]
    report["in_scope_low"] = [{"q": r["q"], "score": r["top_score"]} for r in in_scope if r["confidence"] == "low"]

    print(json.dumps(report, indent=2))
    if args.save:
        (ROOT / "evals" / "report.json").write_text(json.dumps({"report": report, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
