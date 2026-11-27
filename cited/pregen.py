"""One-time content generation with Claude.

Two passes:
  1. documents: one request per episode/post -> summary, takeaways with exact
     quotes, frameworks, topic tags per passage, questions it answers.
  2. topics: one request per topic over a diverse bundle of passages ->
     overview, perspectives, disagreements, quotes, related questions, Q&A.

Two ways to run each pass, producing identical files:
  - Batch API (needs ANTHROPIC_API_KEY): `python scripts/pregenerate.py submit ...`
  - Claude Code on your own machine (uses your Claude plan instead of API
    credit): `python scripts/pregenerate.py export ...`, run
    pregen/run_with_claude_code.sh, then `python scripts/pregenerate.py import ...`
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from . import config, generated, links, models
from .index import Index
from .topics import TOPIC_BY_ID, TOPICS

log = logging.getLogger(__name__)

PROMPTS_DIR = config.ROOT / "pregen" / "prompts"
RAW_DIR = config.DATA_DIR / "pregen_raw"        # raw model output (git-ignored)
INPUTS_DIR = config.DATA_DIR / "pregen_inputs"  # prompts + source text (git-ignored)

TOPIC_BUNDLE_SIZE = 60
TOPIC_BUNDLE_PER_DOC = 2

# Prices per million tokens for the cost estimate (Batch API is 50% off).
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}


def _topic_list() -> str:
    return "\n".join(f"- {t['id']}: {t['name']}" for t in TOPICS)


def document_prompt() -> str:
    return (PROMPTS_DIR / "document.md").read_text().replace("{TOPIC_LIST}", _topic_list())


def topic_prompt(topic: dict) -> str:
    text = (PROMPTS_DIR / "topic.md").read_text()
    return (text.replace("{TOPIC_ID}", topic["id"]).replace("{TOPIC_NAME}", topic["name"])
                .replace("{TOPIC_DESCRIPTION}", topic["description"]))


def document_source(idx: Index, doc_id: str) -> str:
    doc = idx.docs[doc_id]
    chunks = idx.doc_chunks(doc_id)
    kind = "Podcast episode" if doc["kind"] == "podcast" else "Newsletter post"
    head = [f"doc_id: {doc_id}", f"{kind}: {doc['title']}", f"Date: {doc['date']}"]
    if doc.get("guests"):
        head.append("Guests: " + ", ".join(doc["guests"]))
    parts = ["\n".join(head)]
    for i, c in enumerate(chunks):
        where = f" ({c['timestamp']})" if c.get("timestamp") else (f" [{c['heading']}]" if c.get("heading") else "")
        parts.append(f"[P{i}]{where}\n{c['text']}")
    return "\n\n".join(parts)


def topic_bundle(idx: Index, topic_id: str) -> list[dict]:
    """A diverse set of the passages most about this topic: at most 2 per source."""
    topic = TOPIC_BY_ID[topic_id]
    tagged = [i for i, c in enumerate(idx.chunks) if topic_id in c.get("topics", [])]
    if not tagged:
        return []
    tvec = models.embed_queries([f"{topic['name']}. {topic['description']}"])[0]
    scores = idx.emb[tagged] @ tvec
    picked, per_doc = [], {}
    for j in np.argsort(-scores):
        c = idx.chunks[tagged[j]]
        if per_doc.get(c["doc_id"], 0) >= TOPIC_BUNDLE_PER_DOC:
            continue
        per_doc[c["doc_id"]] = per_doc.get(c["doc_id"], 0) + 1
        picked.append(c)
        if len(picked) >= TOPIC_BUNDLE_SIZE:
            break
    return picked


def topic_source(idx: Index, bundle: list[dict]) -> str:
    parts = []
    for i, c in enumerate(bundle):
        d = idx.docs[c["doc_id"]]
        kind = "Podcast" if d["kind"] == "podcast" else "Newsletter"
        when = f", {c['timestamp']}" if c.get("timestamp") else ""
        parts.append(f"[S{i}] {kind}: {d['title']} ({d['date']}{when})\n{c['text']}")
    return "\n\n".join(parts)


# ── JSON schemas for structured output (Batch API path) ───────────────────────

def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


_S = {"type": "string"}
_SA = {"type": "array", "items": _S}

DOC_SCHEMA = _obj({
    "doc_id": _S, "summary": _S,
    "key_takeaways": {"type": "array", "items": _obj({"point": _S, "passage": _S, "speaker": _S, "quote": _S})},
    "frameworks": {"type": "array", "items": _obj({"name": _S, "description": _S, "passage": _S})},
    "passage_topics": {"type": "array", "items": _obj({"passage": _S, "topics": _SA})},
    "questions_answered": _SA,
})

TOPIC_SCHEMA = _obj({
    "topic_id": _S, "overview": _S,
    "perspectives": {"type": "array", "items": _obj({"claim": _S, "sources": _SA, "speakers": _SA})},
    "disagreements": {"type": "array", "items": _obj({
        "question": _S, "sides": {"type": "array", "items": _obj({"position": _S, "sources": _SA})}})},
    "key_quotes": {"type": "array", "items": _obj({"source": _S, "speaker": _S, "quote": _S})},
    "related_questions": _SA,
    "qa": {"type": "array", "items": _obj({"question": _S, "answer": _S, "sources": _SA})},
})


# ── Work items ────────────────────────────────────────────────────────────────

def work_items(idx: Index, kind: str, only: list[str] | None = None) -> list[dict]:
    """Each item: custom_id, kind, item_id, prompt, source, schema."""
    items = []
    if kind == "documents":
        for doc_id in sorted(idx.docs):
            if only and doc_id not in only:
                continue
            items.append({"custom_id": "d-" + generated.safe_name(doc_id)[:60], "kind": "docs",
                          "item_id": doc_id, "prompt": document_prompt(),
                          "source": document_source(idx, doc_id), "schema": DOC_SCHEMA})
    elif kind == "topics":
        for t in TOPICS:
            if only and t["id"] not in only:
                continue
            bundle = topic_bundle(idx, t["id"])
            if not bundle:
                log.warning("No passages tagged %s; skipping", t["id"])
                continue
            items.append({"custom_id": "t-" + t["id"], "kind": "topics", "item_id": t["id"],
                          "prompt": topic_prompt(t), "source": topic_source(idx, bundle),
                          "schema": TOPIC_SCHEMA, "bundle": [c["id"] for c in bundle]})
    else:
        raise ValueError(kind)
    return items


def estimate(items: list[dict], model: str, batch: bool = True) -> dict:
    words = sum(len((i["prompt"] + i["source"]).split()) for i in items)
    in_tok = int(words * 1.35)
    # Output (incl. thinking) per request: rough planning numbers, not measurements.
    out_tok = sum(3500 if i["kind"] == "docs" else 12000 for i in items)
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    factor = 0.5 if batch else 1.0
    cost = (in_tok * pin + out_tok * pout) / 1e6 * factor
    return {"requests": len(items), "input_tokens": in_tok, "output_tokens_est": out_tok,
            "model": model, "batch": batch, "est_cost_usd": round(cost, 2)}


# ── Claude Code path: export inputs / import outputs ──────────────────────────

def export_inputs(items: list[dict], out_dir: Path = INPUTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for it in items:
        name = it["custom_id"]
        (out_dir / f"{name}.md").write_text(it["prompt"] + "\n\n---\n\nSOURCE\n\n" + it["source"])
        manifest.append({k: it[k] for k in ("custom_id", "kind", "item_id")} | {"bundle": it.get("bundle")})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return out_dir


def import_outputs(idx: Index, outputs_dir: Path, manifest_path: Path,
                   generated_dir: Path = config.GENERATED_DIR) -> list[dict]:
    manifest = json.loads(manifest_path.read_text())
    reports = []
    for m in manifest:
        path = outputs_dir / f"{m['custom_id']}.json"
        if not path.exists():
            continue
        try:
            raw = generated.parse_json(path.read_text())
        except (ValueError, json.JSONDecodeError) as e:
            reports.append({"item": m["item_id"], "error": f"unparseable output: {e}"})
            continue
        reports.append(_store(idx, m, raw, generated_dir))
    return reports


def _store(idx: Index, m: dict, raw: dict, generated_dir: Path) -> dict:
    if m["kind"] == "docs":
        clean, report = generated.validate_doc(raw, m["item_id"], idx.doc_chunks(m["item_id"]))
    else:
        bundle = [idx.chunk(cid) for cid in m["bundle"]]
        if any(c is None for c in bundle):
            return {"item": m["item_id"], "error": "index changed since export; re-export topics"}
        clean, report = generated.validate_topic(raw, m["item_id"], bundle)
    generated.save(m["kind"], m["item_id"], clean, generated_dir)
    return report


# ── Batch API path ────────────────────────────────────────────────────────────

def submit_batch(items: list[dict], model: str = config.PREGEN_MODEL, effort: str = "high") -> str:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    requests = [Request(custom_id=it["custom_id"], params=MessageCreateParamsNonStreaming(
        model=model,
        max_tokens=16000 if it["kind"] == "docs" else 32000,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": it["schema"]}},
        system=it["prompt"],
        messages=[{"role": "user", "content": it["source"]}],
    )) for it in items]
    batch = client.messages.batches.create(requests=requests)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = [{k: it[k] for k in ("custom_id", "kind", "item_id")} | {"bundle": it.get("bundle")}
                for it in items]
    (RAW_DIR / f"{batch.id}.manifest.json").write_text(json.dumps(manifest, indent=1))
    log.info("Submitted batch %s with %d requests", batch.id, len(requests))
    return batch.id


def collect_batch(idx: Index, batch_id: str, wait: bool = True,
                  generated_dir: Path = config.GENERATED_DIR) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended" or not wait:
            break
        log.info("Batch %s: %s (%s processing)", batch_id, b.processing_status, b.request_counts.processing)
        time.sleep(60)
    if b.processing_status != "ended":
        return [{"batch": batch_id, "status": b.processing_status}]

    manifest = {m["custom_id"]: m for m in
                json.loads((RAW_DIR / f"{batch_id}.manifest.json").read_text())}
    reports = []
    for result in client.messages.batches.results(batch_id):
        m = manifest.get(result.custom_id)
        if m is None:
            continue
        if result.result.type != "succeeded":
            reports.append({"item": m["item_id"], "error": result.result.type})
            continue
        msg = result.result.message
        if msg.stop_reason == "refusal":
            reports.append({"item": m["item_id"], "error": "refused"})
            continue
        text = next((blk.text for blk in msg.content if blk.type == "text"), "")
        (RAW_DIR / f"{result.custom_id}.json").write_text(text)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            reports.append({"item": m["item_id"], "error": f"bad JSON ({msg.stop_reason}): {e}"})
            continue
        reports.append(_store(idx, m, raw, generated_dir))
    return reports


def source_link_for(idx: Index, chunk_id: str) -> str:
    c = idx.chunk(chunk_id)
    return links.source_link(c, idx.docs[c["doc_id"]]) if c else ""
