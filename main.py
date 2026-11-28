"""Cited web app: FastAPI backend + static single-page UI.

    uvicorn main:app --port 8080

Runs in search-only mode until ANTHROPIC_API_KEY is set.
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response as FastResponse
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cited import accounts, analytics, answer, config, export, mailer, models, search, sources, updates
from cited import index as index_mod
from cited.cache import AnswerCache, seed_from_generated
from cited.index import Index
from cited.topics import TOPIC_BY_ID, TOPICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cited")

STATE: dict = {}
DEFAULT_TOPICS = ["ai-pm-skills", "pricing", "careers"]


def _load_index() -> tuple[Index, AnswerCache, int]:
    idx = Index.load()
    cache = AnswerCache(config.INDEX_DIR / "answer_cache.json", idx.meta["version"])
    return idx, cache, seed_from_generated(cache, idx.generated)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    idx, cache, seeded = _load_index()
    models.embed_queries(["warm up"])
    models.rerank("warm up", ["warm up"])
    stats = analytics.Analytics(analytics.default_path())
    stats.purge_questions()
    STATE.update(index=idx, cache=cache, analytics=stats, accounts=accounts.Accounts(accounts.default_path()))
    if not mailer.configured():
        log.warning("RESEND_API_KEY is not set: sign-in codes are written to this log, not emailed. "
                    "Fine for testing; never for a public site.")
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
    out["related"] = search.related(idx, out["results"], q, out["question_topics"])
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
            "related": search.related(idx, res["results"], question_topics=[topic_id])}
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
    visitor_id: str | None = None  # anonymous ids made in the browser (see cited/analytics.py)
    session_id: str | None = None
    chat_id: str | None = None     # one conversation; a new chat gets a new id


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/ask")
async def ask(req: AskRequest, request: Request, response: FastResponse):
    if _rate_limited(_client_ip(request)):
        return JSONResponse({"error": f"You've reached {config.RATE_LIMIT_PER_HOUR} questions this hour. "
                                      "Please come back a little later."}, status_code=429)
    user = _current_user(request, response)
    acc: accounts.Accounts = STATE["accounts"]
    tier = "member" if acc.is_member(user) else ("free" if user else "anon")
    chat = req.chat_id if req.chat_id and accounts.ID_RE.match(req.chat_id) else (req.session_id or "default")
    # Not signed in: 5 questions per chat, 5 chats a day (by browser id, and loosely by
    # network address). Signed in: 25 questions a day. Supporters: no limit.
    subject = f"u:{user['id']}" if user else f"v:{req.visitor_id or 'none'}"
    allowance = acc.chat_allowance(subject, chat, tier)
    if allowance["ok"] and tier == "anon":
        ip_allow = acc.chat_allowance(f"ip:{_client_ip(request)}", chat, "anon")
        if not ip_allow["ok"] and ip_allow.get("reason") == "day_full" and ip_allow["chats_today"] >= allowance["chats_max"] * 4:
            allowance = {**allowance, "ok": False, "reason": "day_full"}
    if not allowance["ok"]:
        msg = {
            "chat_full": f"This chat has reached {accounts.FREE_TURNS_PER_CHAT} questions. Start a new chat to keep going, "
                         f"or sign in for up to {accounts.SIGNED_IN_QUESTIONS_PER_DAY} questions a day.",
            "day_full": f"You've used today's {accounts.FREE_CHATS_PER_DAY} free chats. Sign in for up to "
                        f"{accounts.SIGNED_IN_QUESTIONS_PER_DAY} questions a day, or come back tomorrow.",
            "questions_full": f"You've asked {accounts.SIGNED_IN_QUESTIONS_PER_DAY} questions today. Come back tomorrow, "
                              "or chip in to keep Cited free and get unlimited chats.",
        }[allowance["reason"]]
        STATE["analytics"].log("limit_hit", req.visitor_id, req.session_id, kind=allowance["reason"])
        return JSONResponse({"error": msg, "limit": allowance}, status_code=429)
    acc.record_turn(subject, chat)
    if tier == "anon":
        acc.record_turn(f"ip:{_client_ip(request)}", chat)
        allowance["turns_used"] += 1
    elif tier == "free":
        allowance["questions_today"] += 1
    idx: Index = STATE["index"]
    cache: AnswerCache = STATE["cache"]
    question = req.question.strip()
    retrieval_q = question
    if len(question.split()) < 6 and req.history:
        last_user = next((h["content"] for h in reversed(req.history) if h.get("role") == "user"), "")
        retrieval_q = f"{last_user} {question}"[:600]

    found = search.search(idx, retrieval_q)
    results = found["results"]
    if found["confidence"]["level"] == "low":
        # Don't build suggestions from weak matches; offer the question's own topics or popular ones.
        related = search.related(idx, [], question, DEFAULT_TOPICS)
    else:
        related = search.related(idx, results, question, found["question_topics"])

    def log_ask(mode: str) -> None:
        low = found["confidence"]["level"] == "low"
        topics = [] if low else found["question_topics"]
        STATE["analytics"].log(
            "ask", req.visitor_id, req.session_id, topic=topics[0] if topics else None, kind=mode,
            turn=1 + sum(1 for h in req.history if h.get("role") == "user"),
            confidence=found["confidence"]["level"], question=question)

    async def events():
        yield _sse({"type": "allowance", **allowance, "signed_in": bool(user)})
        yield _sse({"type": "sources", "sources": results, "confidence": found["confidence"],
                    "more": found["more"]})
        hit = None
        if not req.fresh and not req.history:
            q_vec = models.embed_queries([question])[0]
            hit = cache.lookup(q_vec, [r["chunk_id"] for r in results])
        low = not results or found["confidence"]["level"] == "low"
        log_ask("saved" if hit else "not_covered" if low else
                "search_only" if not answer.llm_enabled() else "answered")
        if hit:
            srcs = [search.present(idx, c) for c in map(idx.chunk, hit["chunk_ids"]) if c]
            yield _sse({"type": "saved", "question": hit["question"], "origin": hit["origin"],
                        "answer": hit["answer"], "sources": srcs})
        elif not results or found["confidence"]["level"] == "low":
            yield _sse({"type": "not_covered",
                        "message": "The free podcast and newsletter archive doesn't cover this, so "
                                   "Cited won't guess. Try one of the topics below."})
        elif not answer.llm_enabled():
            yield _sse({"type": "search_only"})
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
    visitor_id: str | None = None


def _anon_export_check(request: Request, response, visitor_id: str | None):
    """Not signed in: downloads and emailed PDFs share ANON_EXPORTS_PER_MONTH.
    Returns (key to record or None, error response or None)."""
    if _current_user(request, response):
        return None, None
    key = f"v:{visitor_id}" if visitor_id and analytics._clean_id(visitor_id) else f"ip:{_client_ip(request)}"
    allow = STATE["accounts"].quota(key, accounts.ANON_EXPORTS_PER_MONTH)
    if not allow["ok"]:
        return None, JSONResponse({"error": f"You've used this month's {allow['max']} free exports. Sign in (free) for "
                                            "unlimited downloads and notebooks on every device.",
                                   "allowance": allow, "signin": True}, status_code=429)
    return key, None


def _filename(title: str, ext: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in title).strip().replace(" ", "-")
    return f"{base[:60] or 'cited-notebook'}.{ext}"


@app.post("/api/export/markdown")
def export_markdown(nb: Notebook, request: Request, response: FastResponse):
    key, err = _anon_export_check(request, response, nb.visitor_id)
    if err:
        return err
    md = export.to_markdown(nb.model_dump())
    if key:
        STATE["accounts"].record_email(key)
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(nb.title, "md")}"'})


@app.post("/api/export/pdf")
def export_pdf(nb: Notebook, request: Request, response: FastResponse):
    key, err = _anon_export_check(request, response, nb.visitor_id)
    if err:
        return err
    pdf = export.to_pdf(nb.model_dump())
    if key:
        STATE["accounts"].record_email(key)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(nb.title, "pdf")}"'})


# ── New content for notebooks ────────────────────────────────────────────────

class NotebookSummary(BaseModel):
    id: str = Field(max_length=40)
    since: str = Field(max_length=40)
    title: str = Field(default="", max_length=120)
    questions: list[str] = Field(default_factory=list, max_length=20)
    chunk_ids: list[str] = Field(default_factory=list, max_length=100)
    doc_ids: list[str] = Field(default_factory=list, max_length=100)
    topics: list[str] = Field(default_factory=list, max_length=10)


class UpdatesRequest(BaseModel):
    notebooks: list[NotebookSummary] = Field(default_factory=list, max_length=20)


@app.post("/api/updates")
def notebook_updates(req: UpdatesRequest, request: Request, response: FastResponse):
    """New episodes and posts that match each notebook since it was last checked.

    Supporters: items are returned to be added to their notebooks.
    Signed-in free users on their 2nd and 3rd visits: a preview (topics, which
    notebooks match, and the items to view); adding them is for supporters.
    Everyone else: nothing. Notebook summaries are used for this request only."""
    idx: Index = STATE["index"]
    acc: accounts.Accounts = STATE["accounts"]
    user = _current_user(request, response)
    member = acc.is_member(user)
    preview = bool(user and not member and acc.teaser_due(user))
    if not (member or preview):
        return {"checked_at": updates.now_iso(), "notebooks": {}, "member": False, "preview": False}
    nbs = [n.model_dump() for n in req.notebooks]
    for n in nbs:
        n["questions"] = [q[:300] for q in n["questions"]]
    out = updates.check(idx, nbs, idx.first_seen)
    out.update(member=member, preview=preview)
    if preview:
        found = [it for items in out["notebooks"].values() for it in items]
        if not found:
            return {**out, "preview": False}  # nothing new: don't spend a preview visit
        acc.record_teaser(user)
        names = {t["id"]: t["name"] for t in TOPICS}
        topic_counts: dict[str, int] = defaultdict(int)
        for it in found:
            for t in it.get("topics", [])[:1]:
                topic_counts[names.get(t, t)] += 1
        out["topics"] = sorted(topic_counts.items(), key=lambda x: -x[1])
        out["previews_left"] = max(0, accounts.TEASER_VISITS - (user.get("teaser_views", 0) + 1))
        STATE["analytics"].log("preview_shown", None, None)
    return out


# ── Accounts: email + code sign-in, synced notebooks and chats ───────────────

SESSION_COOKIE = "cited_session"


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _set_session_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=_is_https(request), path="/")


def _current_user(request: Request, response=None) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    user, renewed = STATE["accounts"].user_for_session(token)
    if user and renewed and response is not None:
        _set_session_cookie(response, request, token)  # sliding 180-day window
    return user


def _require_user(request: Request, response=None) -> dict:
    user = _current_user(request, response)
    if not user:
        raise HTTPException(401, "Sign in first.")
    return user


def _public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    acc: accounts.Accounts = STATE["accounts"]
    member = acc.is_member(user)
    until = user.get("member_until")
    return {"email": user["email"], "plan": "member" if member else "free",
            "member_until": datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d") if member and until else None,
            "emails_this_month": acc.email_allowance(user["email"], member)}


class CodeRequest(BaseModel):
    email: str = Field(max_length=254)


class VerifyRequest(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=12)
    visitor_id: str | None = None
    session_id: str | None = None


def _send_code(email: str, ip: str, attachment: tuple[str, bytes] | None = None, title: str = "") -> tuple[bool, str]:
    code, why = STATE["accounts"].create_code(email, ip)
    if not code:
        return False, why
    if attachment:
        subject = f"Your Cited notebook: {title}"[:120]
        text = (f"Your notebook \"{title}\" is attached as a PDF.\n\n"
                f"Want your notebooks and chats on every device? Enter this code on Cited within 10 minutes:\n\n"
                f"    {code}\n\nIf you didn't ask for this, ignore this email.\n\n"
                f"Cited is built on {config.CREDIT_NAME} ({config.CREDIT_URL}) and is not affiliated with Aakash Gupta.")
    else:
        subject = f"Your Cited sign-in code: {code}"
        text = (f"Your Cited sign-in code is:\n\n    {code}\n\nIt works for 10 minutes. "
                f"If you didn't ask for it, ignore this email.")
    ok = mailer.send(email, subject, text, attachments=[attachment] if attachment else None)
    return (True, "") if ok else (False, "We couldn't send the email. Please try again in a minute.")


@app.post("/api/auth/request-code")
def request_code(req: CodeRequest, request: Request):
    email = accounts.normalize_email(req.email)
    if not email:
        raise HTTPException(400, "That doesn't look like an email address.")
    ok, why = _send_code(email, _client_ip(request))
    if not ok:
        return JSONResponse({"error": why}, status_code=429)
    return {"sent": True}


@app.post("/api/auth/verify")
def verify_code(req: VerifyRequest, request: Request, response: FastResponse):
    email = accounts.normalize_email(req.email)
    acc: accounts.Accounts = STATE["accounts"]
    if not email or not acc.verify_code(email, req.code):
        raise HTTPException(400, "That code didn't work. Check the latest email, or ask for a new code.")
    user, created = acc.get_or_create_user(email)
    _set_session_cookie(response, request, acc.create_session(user["id"]))
    STATE["analytics"].log("signup" if created else "signin", req.visitor_id, req.session_id)
    return {"user": _public_user(user), "created": created}


@app.get("/api/auth/me")
def me(request: Request, response: FastResponse):
    return {"user": _public_user(_current_user(request, response)), "email_ready": mailer.configured(),
            "limits": {"turns_per_chat": accounts.FREE_TURNS_PER_CHAT, "chats_per_day": accounts.FREE_CHATS_PER_DAY,
                       "signed_in_questions_per_day": accounts.SIGNED_IN_QUESTIONS_PER_DAY,
                       "anon_exports_per_month": accounts.ANON_EXPORTS_PER_MONTH,
                       "emails_per_month": accounts.FREE_EMAILED_NOTEBOOKS_PER_MONTH},
            "support": {"available": bool(os.getenv("CITED_STRIPE_PAYMENT_LINK")), "min_usd": SUPPORT_MIN_USD,
                        "days": accounts.MEMBERSHIP_DAYS}}


@app.post("/api/auth/signout")
def signout(request: Request, response: FastResponse):
    STATE["accounts"].end_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me/notebooks")
def my_notebooks(request: Request, response: FastResponse):
    user = _require_user(request, response)
    return {"notebooks": STATE["accounts"].list_notebooks(user["id"])}


@app.put("/api/me/notebooks/{nb_id}")
async def put_notebook(nb_id: str, request: Request, response: FastResponse):
    user = _require_user(request, response)
    body = await request.body()
    if len(body) > accounts.MAX_NOTEBOOK_BYTES:
        raise HTTPException(413, "This notebook is too large to sync.")
    nb = json.loads(body or b"{}")
    if not isinstance(nb, dict) or nb.get("id") != nb_id:
        raise HTTPException(400, "bad notebook")
    ok, why = STATE["accounts"].save_notebook(user["id"], nb)
    if not ok:
        raise HTTPException(400, why)
    return {"ok": True}


@app.delete("/api/me/notebooks/{nb_id}")
def delete_notebook(nb_id: str, request: Request, response: FastResponse):
    user = _require_user(request, response)
    STATE["accounts"].delete_notebook(user["id"], nb_id)
    return {"ok": True}


class ChatIn(BaseModel):
    title: str = Field(default="", max_length=200)
    turns: list[dict] = Field(default_factory=list, max_length=50)


@app.get("/api/me/chats")
def my_chats(request: Request, response: FastResponse):
    user = _require_user(request, response)
    return {"chats": STATE["accounts"].list_chats(user["id"])}


@app.get("/api/me/chats/{chat_id}")
def my_chat(chat_id: str, request: Request, response: FastResponse):
    user = _require_user(request, response)
    chat = STATE["accounts"].get_chat(user["id"], chat_id)
    if not chat:
        raise HTTPException(404, "No such chat.")
    return chat


@app.put("/api/me/chats/{chat_id}")
def put_chat(chat_id: str, body: ChatIn, request: Request, response: FastResponse):
    user = _require_user(request, response)
    if not STATE["accounts"].save_chat(user["id"], chat_id, body.title, body.turns):
        raise HTTPException(400, "Couldn't save this chat.")
    return {"ok": True}


@app.delete("/api/me/chats/{chat_id}")
def delete_chat(chat_id: str, request: Request, response: FastResponse):
    user = _require_user(request, response)
    STATE["accounts"].delete_chat(user["id"], chat_id)
    return {"ok": True}


class EmailNotebookRequest(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    notebook: dict
    visitor_id: str | None = None
    session_id: str | None = None


@app.post("/api/notebooks/email")
def email_notebook(req: EmailNotebookRequest, request: Request, response: FastResponse):
    """Email a notebook as a PDF. For visitors who aren't signed in, the same
    email carries a sign-in code, so entering it keeps their notebooks for good."""
    acc: accounts.Accounts = STATE["accounts"]
    user = _current_user(request, response)
    email = user["email"] if user else accounts.normalize_email(req.email or "")
    if not email:
        raise HTTPException(400, "Enter the email address to send it to.")
    anon_key, err = _anon_export_check(request, response, req.visitor_id)
    if err:
        return err
    allow = acc.email_allowance(email, acc.is_member(user))
    if not allow["ok"]:
        return JSONResponse({"error": f"You've emailed {allow['max']} notebooks this month. "
                                      "You can still download them as PDF or Markdown.", "allowance": allow},
                            status_code=429)
    nb = Notebook(**{k: req.notebook.get(k) for k in ("title", "created", "entries") if req.notebook.get(k) is not None})
    pdf = export.to_pdf(nb.model_dump())
    name = _filename(nb.title, "pdf")
    if user:
        ok = mailer.send(email, f"Your Cited notebook: {nb.title}"[:120],
                         f"Your notebook \"{nb.title}\" is attached as a PDF.\n\n"
                         f"Cited is built on {config.CREDIT_NAME} ({config.CREDIT_URL}).",
                         attachments=[(name, pdf)])
        why = "" if ok else "We couldn't send the email. Please try again in a minute."
    else:
        ok, why = _send_code(email, _client_ip(request), attachment=(name, pdf), title=nb.title)
    if not ok:
        return JSONResponse({"error": why}, status_code=429)
    acc.record_email(email)
    if anon_key:
        acc.record_email(anon_key)
    STATE["analytics"].log("email_notebook", req.visitor_id, req.session_id, kind="member" if user else "anonymous")
    return {"sent": True, "email": email, "signed_in": bool(user),
            "allowance": acc.email_allowance(email, acc.is_member(user))}


# ── Support payments (Stripe Payment Link + webhook) ─────────────────────────
#
# CITED_STRIPE_PAYMENT_LINK: a Stripe Payment Link set to "customer chooses price"
#   with a $5 minimum. STRIPE_WEBHOOK_SECRET: signing secret of a webhook endpoint
#   at /api/stripe/webhook listening for checkout.session.completed.
# A completed payment gives MEMBERSHIP_DAYS of supporter perks (stacking).

SUPPORT_MIN_USD = int(os.getenv("CITED_SUPPORT_MIN_USD", "5"))


@app.get("/api/support/link")
def support_link(request: Request, response: FastResponse):
    base = os.getenv("CITED_STRIPE_PAYMENT_LINK", "")
    if not base:
        return {"url": None}
    user = _current_user(request, response)
    params = {}
    if user:
        params = {"client_reference_id": user["id"], "prefilled_email": user["email"]}
    from urllib.parse import urlencode
    return {"url": base + ("?" + urlencode(params) if params else "")}


def _verify_stripe(payload: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, sig)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    payload = await request.body()
    if not secret or not _verify_stripe(payload, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(400, "bad signature")
    event = json.loads(payload)
    if event.get("type") == "checkout.session.completed":
        obj = event["data"]["object"]
        if obj.get("payment_status") in ("paid", "no_payment_required"):
            email = accounts.normalize_email((obj.get("customer_details") or {}).get("email") or obj.get("customer_email") or "")
            user = STATE["accounts"].record_payment(obj["id"], obj.get("client_reference_id"), email,
                                                    int(obj.get("amount_total") or 0), obj.get("currency") or "usd")
            STATE["analytics"].log("payment", None, None, value=str(obj.get("amount_total") or 0))
            log.info("Payment %s: supporter %s", obj["id"], user["email"] if user else "(already recorded)")
    return {"received": True}


# ── Admin: check for new content (manual) ────────────────────────────────────

REFRESH: dict = {"status": "idle"}
_refresh_lock = threading.Lock()


def _run_refresh() -> None:
    started = time.time()
    try:
        before = set(STATE["index"].docs)
        repo = sources.ensure_transcripts(config.DATA_DIR / "transcripts")
        docs = sources.load_episodes(repo)
        docs += sources.newsletter_docs(sources.fetch_newsletter_posts(refresh=True))
        meta = index_mod.build(docs)
        idx, cache, _ = _load_index()
        STATE.update(index=idx, cache=cache)
        added = [d for d in idx.docs if d not in before]
        REFRESH.update(status="done", finished=updates.now_iso(), seconds=round(time.time() - started),
                       documents=meta["documents"], passages=meta["passages"],
                       new=[{"id": d, "title": idx.docs[d]["title"], "kind": idx.docs[d]["kind"],
                             "date": idx.docs[d]["date"]} for d in sorted(added)])
        log.info("Refresh done: %d new documents", len(added))
    except Exception as e:  # report the failure in the dashboard instead of crashing the app
        log.exception("Refresh failed")
        REFRESH.update(status="failed", finished=updates.now_iso(), error=str(e)[:300])
    finally:
        _refresh_lock.release()


@app.post("/api/admin/refresh")
def admin_refresh(request: Request):
    _require_admin(request)
    if not _refresh_lock.acquire(blocking=False):
        return {**REFRESH, "status": "running"}
    REFRESH.clear()
    REFRESH.update(status="running", started=updates.now_iso())
    threading.Thread(target=_run_refresh, daemon=True).start()
    return REFRESH


@app.get("/api/admin/refresh")
def admin_refresh_status(request: Request):
    _require_admin(request)
    idx: Index = STATE["index"]
    return {**REFRESH, "index_built_at": idx.meta.get("built_at"),
            "index_documents": idx.meta.get("documents")}


# ── Analytics ─────────────────────────────────────────────────────────────────

class EventIn(BaseModel):
    type: str = Field(max_length=20)
    visitor_id: str | None = None
    session_id: str | None = None
    topic: str | None = Field(default=None, max_length=40)
    kind: str | None = Field(default=None, max_length=20)
    value: str | None = Field(default=None, max_length=40)


@app.post("/api/event", status_code=204)
def track(ev: EventIn):
    if ev.type in analytics.CLIENT_EVENTS:
        topic = ev.topic if (ev.topic is None or ev.topic in TOPIC_BY_ID) else None
        STATE["analytics"].log(ev.type, ev.visitor_id, ev.session_id, topic=topic, kind=ev.kind, value=ev.value)
    return Response(status_code=204)


def _require_admin(request: Request) -> None:
    token = os.getenv("CITED_ADMIN_TOKEN", "")
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(404, "admin dashboard is off: set CITED_ADMIN_TOKEN")
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "wrong admin token")


@app.get("/admin")
def admin_page():
    return FileResponse(config.ROOT / "static" / "admin.html")


@app.get("/api/admin/metrics")
def admin_metrics(request: Request, days: int = 30):
    _require_admin(request)
    m = STATE["analytics"].metrics(days=min(max(days, 7), 365))
    m["accounts"] = STATE["accounts"].stats()
    return m


@app.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request)
    return {"users": STATE["accounts"].list_users()}


class PlanIn(BaseModel):
    plan: str


@app.post("/api/admin/users/{user_id}/plan")
def admin_set_plan(user_id: str, body: PlanIn, request: Request):
    _require_admin(request)
    if not STATE["accounts"].set_plan(user_id, body.plan):
        raise HTTPException(400, "Unknown user or plan.")
    return {"ok": True}


@app.get("/healthz")
def health():
    return {"ok": "index" in STATE}
