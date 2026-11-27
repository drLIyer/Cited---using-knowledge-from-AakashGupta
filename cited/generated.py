"""Validate, store and load pre-generated content.

Raw model output refers to passages by local labels (P12 within a document,
S4 within a topic bundle). Validation maps labels to stable passage ids, drops
anything that points at a missing passage, and drops every quote that is not
an exact copy of its passage. Only validated content lands in GENERATED_DIR.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import verify
from .topics import TOPIC_BY_ID

log = logging.getLogger(__name__)


def safe_name(doc_or_topic_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", doc_or_topic_id)


def parse_json(text: str) -> dict:
    """Parse model output that should be one JSON object (tolerates code fences)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in output")
    return json.loads(text[start:end + 1])


# ── Document notes ────────────────────────────────────────────────────────────

def validate_doc(raw: dict, doc_id: str, chunks: list[dict]) -> tuple[dict, dict]:
    """Return (clean, report). `chunks` are the document's passages in order (P0..Pn)."""
    label = {f"P{i}": c for i, c in enumerate(chunks)}
    report = {"doc_id": doc_id, "quotes": 0, "dropped_quotes": 0, "bad_labels": 0}

    takeaways = []
    for t in raw.get("key_takeaways") or []:
        c = label.get(str(t.get("passage", "")).strip())
        if c is None:
            report["bad_labels"] += 1
            continue
        report["quotes"] += 1
        quote = (t.get("quote") or "").strip()
        if quote and not verify.quote_in(quote, c["text"]):
            report["dropped_quotes"] += 1
            quote = ""  # keep the point, lose the unverifiable quote
        takeaways.append({"point": t.get("point", "").strip(), "chunk_id": c["id"],
                          "speaker": t.get("speaker", "").strip(), "quote": quote})

    frameworks = []
    for f in raw.get("frameworks") or []:
        c = label.get(str(f.get("passage", "")).strip())
        if c is None:
            report["bad_labels"] += 1
            continue
        frameworks.append({"name": f.get("name", "").strip(),
                           "description": f.get("description", "").strip(), "chunk_id": c["id"]})

    passage_topics = {}
    for pt in raw.get("passage_topics") or []:
        c = label.get(str(pt.get("passage", "")).strip())
        topics = [t for t in pt.get("topics") or [] if t in TOPIC_BY_ID][:3]
        if c is not None and topics:
            passage_topics[c["id"]] = topics

    clean = {
        "doc_id": doc_id,
        "summary": (raw.get("summary") or "").strip(),
        "key_takeaways": takeaways,
        "frameworks": frameworks,
        "passage_topics": passage_topics,
        "questions_answered": [q.strip() for q in raw.get("questions_answered") or [] if q.strip()],
    }
    return clean, report


# ── Topic pages ───────────────────────────────────────────────────────────────

def _map_sources(labels, label_map, report):
    out = []
    for s in labels or []:
        c = label_map.get(str(s).strip())
        if c is None:
            report["bad_labels"] += 1
        else:
            out.append(c["id"])
    return out


_CITE = re.compile(r"\[(S\d+)\]")


def validate_topic(raw: dict, topic_id: str, bundle: list[dict]) -> tuple[dict, dict]:
    """`bundle` is the ordered list of passages shown to the model as S0..Sn."""
    label = {f"S{i}": c for i, c in enumerate(bundle)}
    report = {"topic_id": topic_id, "quotes": 0, "dropped_quotes": 0, "bad_labels": 0,
              "qa_dropped": 0, "qa_quotes_unverified": 0}

    perspectives = [{"claim": p.get("claim", "").strip(),
                     "chunk_ids": _map_sources(p.get("sources"), label, report),
                     "speakers": p.get("speakers") or []}
                    for p in raw.get("perspectives") or []]
    disagreements = [{"question": d.get("question", "").strip(),
                      "sides": [{"position": s.get("position", "").strip(),
                                 "chunk_ids": _map_sources(s.get("sources"), label, report)}
                                for s in d.get("sides") or []]}
                     for d in raw.get("disagreements") or []]

    quotes = []
    for q in raw.get("key_quotes") or []:
        c = label.get(str(q.get("source", "")).strip())
        if c is None:
            report["bad_labels"] += 1
            continue
        report["quotes"] += 1
        if not verify.quote_in(q.get("quote", ""), c["text"]):
            report["dropped_quotes"] += 1
            continue
        quotes.append({"chunk_id": c["id"], "speaker": q.get("speaker", "").strip(),
                       "quote": q.get("quote", "").strip()})

    qa = []
    for item in raw.get("qa") or []:
        answer = item.get("answer", "")
        cited = list(dict.fromkeys(_CITE.findall(answer)))
        if not cited or any(c not in label for c in cited):
            report["qa_dropped"] += 1
            continue
        chunk_ids = [label[c]["id"] for c in cited]
        texts = [label[c]["text"] for c in cited]
        unverified = verify.check_answer(answer, texts)["unverified"]
        if unverified:
            report["qa_quotes_unverified"] += 1
            continue  # a saved answer with a bad quote is never served
        # Re-number citations [S4] -> [1], [2]… in order of first use.
        order = {c: i + 1 for i, c in enumerate(cited)}
        answer = _CITE.sub(lambda m: f"[{order[m.group(1)]}]", answer)
        qa.append({"question": item.get("question", "").strip(), "answer": answer.strip(),
                   "chunk_ids": chunk_ids})

    clean = {
        "topic_id": topic_id,
        "overview": (raw.get("overview") or "").strip(),
        "perspectives": perspectives,
        "disagreements": disagreements,
        "key_quotes": quotes,
        "related_questions": [q.strip() for q in raw.get("related_questions") or [] if q.strip()],
        "qa": qa,
    }
    return clean, report


# ── Storage ───────────────────────────────────────────────────────────────────

def save(kind: str, item_id: str, clean: dict, generated_dir: Path) -> Path:
    path = generated_dir / kind / f"{safe_name(item_id)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean, indent=1, ensure_ascii=False))
    return path


def load(generated_dir: Path, idx) -> dict:
    out = {"docs": {}, "topics": {}}
    for kind in ("docs", "topics"):
        folder = generated_dir / kind
        if not folder.exists():
            continue
        for p in folder.glob("*.json"):
            data = json.loads(p.read_text())
            key = data.get("doc_id") or data.get("topic_id")
            out[kind][key] = data
    # Pre-generated topic tags replace the provisional similarity tags.
    for data in out["docs"].values():
        for chunk_id, topics in data.get("passage_topics", {}).items():
            c = idx.chunk(chunk_id)
            if c is not None:
                c["topics"] = topics
                c["topics_source"] = "pregenerated"
    if out["docs"] or out["topics"]:
        log.info("Loaded pre-generated notes for %d documents and %d topics",
                 len(out["docs"]), len(out["topics"]))
    return out
