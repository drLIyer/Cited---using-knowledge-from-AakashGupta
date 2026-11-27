"""Cited web app: FastAPI backend + static single-page UI.

    uvicorn main:app --port 8080

Runs in search-only mode until ANTHROPIC_API_KEY is set.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cited import answer, config, export, models, search
from cited.cache import AnswerCache, seed_from_generated
from cited.index import Index
from cited.topics import TOPIC_BY_ID, TOPICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cited")

STATE: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    idx = Index.load()
    cache = AnswerCache(config.INDEX_DIR / "answer_cache.json", idx.meta["version"])
    seeded = seed_from_generated(cache, idx.generated)
    models.embed_queries(["warm up"])
    models.rerank("warm up", ["warm up"])
    STATE.update(index=idx, cache=cache)
    log.info("Ready: %s passages, %d saved answers (%d pre-generated), answers %s",
             idx.meta["passages"], len(cache.entries), seeded,
             "ON" if answer.llm_enabled() else "OFF (search-only)")
    yield


app = FastAPI(title="Cited", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.ROOT / "static")), name="static")

# ── Free-tier rate limit (per IP, in memory) ──────────────────────────────────
_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= config.RATE_LIMIT_PER_HOUR:
        return True
    q.append(now)
    return False


# ── Pages and metadata ────────────────────────────────────────────────────────

@app.get("/")
def home():
    return FileResponse(config.ROOT / "static" / "index.html")


@app.get("/api/meta")
def meta():
    idx: Index = STATE["index"]
    counts: dict[str, int] = defaultdict(int)
    for c in idx.chunks:
        for t in c.get("topics", []):
            counts[t] += 1
    return {
        "documents": idx.meta["documents"], "podcasts": idx.meta["podcasts"],
        "newsletters": idx.meta["newsletters"], "passages": idx.meta["passages"],
        "built_at": idx.meta["built_at"], "answers": answer.llm_enabled(),
        "pregenerated": {"docs": len(idx.generated.get("docs", {})),
                         "topics": len(idx.generated.get("topics", {}))},
        "credit": {"name": config.CREDIT_NAME, "url": config.CREDIT_URL},
        "topics": [{"id": t["id"], "name": t["name"], "passages": counts.get(t["id"], 0),
                    "has_page": t["id"] in idx.generated.get("topics", {})} for t in TOPICS],
    }


@app.get("/api/search")
def api_search(q: str, kind: str | None = None, topic: str | None = None, k: int = 8):
    if not q.strip():
        raise HTTPException(400, "empty query")
    idx: Index = STATE["index"]
    out = search.search(idx, q[:500], top_k=min(max(k, 1), 20), kind=kind, topic=topic)
    out["related"] = search.related(idx, out["results"], q)
    return out


@app.get("/api/topics/{topic_id}")
def topic_page(topic_id: str):
    idx: Index = STATE["index"]
    t = TOPIC_BY_ID.get(topic_id)
    if t is None:
        raise HTTPException(404, "unknown topic")
    page = idx.generated.get("topics", {}).get(topic_id)
    res = search.search(idx, f"{t['name']}. {t['description']}", top_k=10, topic=topic_id)
    body = {"id": topic_id, "name": t["name"], "description": t["description"],
            "pregenerated": page is not None, "passages": res["results"],
            "related": search.related(idx, res["results"])}
    if page:
        def src(cid):
            c = idx.chunk(cid)
            return search.present(idx, c) if c else None
        body["overview"] = page["overview"]
        body["perspectives"] = [{**p, "sources": [s for s in map(src, p["chunk_ids"]) if s]}
                                for p in page["perspectives"]]
        body["disagreements"] = page["disagreements"]
        body["key_quotes"] = [{**q, "source": src(q["chunk_id"])} for q in page["key_quotes"]]
        body["related"]["questions"] = page["related_questions"][:8]
    return body


@app.get("/api/docs/{doc_id:path}")
def doc_notes(doc_id: str):
    idx: Index = STATE["index"]
    d = idx.docs.get(doc_id)
    if d is None:
        raise HTTPException(404, "unknown document")
    notes = idx.generated.get("docs", {}).get(doc_id)
    return {"doc": d, "notes": notes}


# ── Ask: SSE stream ───────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict] = Field(default_factory=list)
    fresh: bool = False  # skip saved answers ("answer my exact question")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/ask")
async def ask(req: AskRequest, request: Request):
    if _rate_limited(_client_ip(request)):
        return JSONResponse({"error": f"You've reached {config.RATE_LIMIT_PER_HOUR} questions this hour. "
                                      "Please come back a little later."}, status_code=429)
    idx: Index = STATE["index"]
    cache: AnswerCache = STATE["cache"]
    question = req.question.strip()
    retrieval_q = question
    if len(question.split()) < 6 and req.history:
        last_user = next((h["content"] for h in reversed(req.history) if h.get("role") == "user"), "")
        retrieval_q = f"{last_user} {question}"[:600]

    found = search.search(idx, retrieval_q)
    results = found["results"]
    related = search.related(idx, results, question)

    async def events():
        yield _sse({"type": "sources", "sources": results, "confidence": found["confidence"],
                    "more": found["more"]})
        hit = None
        if not req.fresh and not req.history:
            q_vec = models.embed_queries([question])[0]
            hit = cache.lookup(q_vec, [r["chunk_id"] for r in results])
        if hit:
            srcs = [search.present(idx, c) for c in map(idx.chunk, hit["chunk_ids"]) if c]
            yield _sse({"type": "saved", "question": hit["question"], "origin": hit["origin"],
                        "answer": hit["answer"], "sources": srcs})
        elif not answer.llm_enabled():
            yield _sse({"type": "search_only"})
        elif not results or found["confidence"]["level"] == "low":
            yield _sse({"type": "not_covered",
                        "message": "The free podcast and newsletter archive doesn't cover this well, so "
                                   "Cited won't guess. Try one of the related topics below."})
        else:
            final = None
            async for ev in answer.stream_answer(question, req.history, results):
                if ev["type"] == "final":
                    final = ev
                else:
                    yield _sse(ev)
            if final:
                report = answer.check(final["text"], results)
                yield _sse({"type": "verification", **report})
                if not report["unverified"] and report["cited"] and not req.history:
                    cache.add(question, final["text"], [results[n - 1]["chunk_id"] for n in report["cited"]],
                              origin="live")
        yield _sse({"type": "related", **related})
        yield _sse({"type": "done"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Export ────────────────────────────────────────────────────────────────────

class Notebook(BaseModel):
    title: str = "Cited notebook"
    created: str | None = None
    entries: list[dict] = Field(default_factory=list, max_length=export.MAX_ENTRIES)


def _filename(title: str, ext: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in title).strip().replace(" ", "-")
    return f"{base[:60] or 'cited-notebook'}.{ext}"


@app.post("/api/export/markdown")
def export_markdown(nb: Notebook):
    md = export.to_markdown(nb.model_dump())
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(nb.title, "md")}"'})


@app.post("/api/export/pdf")
def export_pdf(nb: Notebook):
    pdf = export.to_pdf(nb.model_dump())
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(nb.title, "pdf")}"'})


@app.get("/healthz")
def health():
    return {"ok": "index" in STATE}
