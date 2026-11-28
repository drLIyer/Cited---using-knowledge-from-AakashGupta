"""Accounts (email + emailed code), synced notebooks and chats, and free-tier limits.

Everything lives in one SQLite file (CITED_APP_DB, default data/cited.sqlite3).
Codes and session tokens are stored only as keyed hashes. Visitors who never
sign in are never stored here except as counters for the daily chat limit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config

CODE_TTL = 10 * 60                    # an emailed code works for 10 minutes
CODE_ATTEMPTS = 5                     # wrong guesses before a code is void
SESSION_DAYS = int(os.getenv("CITED_SESSION_DAYS", "180"))
CODES_PER_EMAIL_PER_HOUR = 5
CODES_PER_IP_PER_HOUR = 20

FREE_TURNS_PER_CHAT = int(os.getenv("CITED_FREE_TURNS_PER_CHAT", "5"))
FREE_CHATS_PER_DAY = int(os.getenv("CITED_FREE_CHATS_PER_DAY", "5"))
FREE_EMAILED_NOTEBOOKS_PER_MONTH = int(os.getenv("CITED_FREE_EMAILS_PER_MONTH", "3"))
MEMBER_EMAILED_NOTEBOOKS_PER_MONTH = int(os.getenv("CITED_MEMBER_EMAILS_PER_MONTH", "30"))

MAX_NOTEBOOK_BYTES = 1_000_000
MAX_NOTEBOOKS_PER_USER = 200
MAX_CHATS_PER_USER = 500

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def normalize_email(email: str) -> str | None:
    e = (email or "").strip().lower()
    return e if EMAIL_RE.match(e) else None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _secret() -> bytes:
    env = os.getenv("CITED_SECRET")
    if env:
        return env.encode()
    path = config.DATA_DIR / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return path.read_text().strip().encode()


class Accounts:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._key = _secret()
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created REAL NOT NULL,
            last_seen REAL NOT NULL, visits INTEGER NOT NULL DEFAULT 1,
            plan TEXT NOT NULL DEFAULT 'free', member_since REAL);
        CREATE TABLE IF NOT EXISTS codes (
            email TEXT NOT NULL, code_hash TEXT NOT NULL, expires REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, ip TEXT);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL NOT NULL,
            expires REAL NOT NULL, renewed REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS notebooks (
            id TEXT NOT NULL, user_id TEXT NOT NULL, title TEXT, updated REAL NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY (user_id, id));
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT NOT NULL, user_id TEXT NOT NULL, title TEXT, created REAL NOT NULL,
            updated REAL NOT NULL, turns TEXT NOT NULL, PRIMARY KEY (user_id, id));
        CREATE TABLE IF NOT EXISTS chat_usage (
            subject TEXT NOT NULL, day TEXT NOT NULL, chat TEXT NOT NULL, turns INTEGER NOT NULL,
            PRIMARY KEY (subject, day, chat));
        CREATE TABLE IF NOT EXISTS email_usage (
            email TEXT NOT NULL, month TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (email, month));
        """)
        self._db.commit()

    def _h(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    # ── Codes ────────────────────────────────────────────────────────────────

    def create_code(self, email: str, ip: str = "") -> tuple[str | None, str]:
        """Return (code, "") or (None, reason) when rate-limited."""
        now = time.time()
        with self._lock:
            by_email = self._db.execute("SELECT COUNT(*) FROM codes WHERE email=? AND created>?",
                                        (email, now - 3600)).fetchone()[0]
            by_ip = self._db.execute("SELECT COUNT(*) FROM codes WHERE ip=? AND created>?",
                                     (ip, now - 3600)).fetchone()[0] if ip else 0
            if by_email >= CODES_PER_EMAIL_PER_HOUR or by_ip >= CODES_PER_IP_PER_HOUR:
                return None, "Too many codes requested. Please wait a bit and try again."
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._db.execute("DELETE FROM codes WHERE expires < ?", (now - 86400,))
            self._db.execute("INSERT INTO codes VALUES (?,?,?,?,?,?)",
                             (email, self._h(f"{email}:{code}"), now + CODE_TTL, 0, now, ip))
            self._db.commit()
        return code, ""

    def verify_code(self, email: str, code: str) -> bool:
        now = time.time()
        code = re.sub(r"\D", "", code or "")
        with self._lock:
            rows = self._db.execute(
                "SELECT rowid, code_hash, attempts FROM codes WHERE email=? AND expires>? ORDER BY created DESC",
                (email, now)).fetchall()
            for r in rows:
                if r["attempts"] >= CODE_ATTEMPTS:
                    continue
                if hmac.compare_digest(r["code_hash"], self._h(f"{email}:{code}")):
                    self._db.execute("DELETE FROM codes WHERE email=?", (email,))
                    self._db.commit()
                    return True
            for r in rows:
                self._db.execute("UPDATE codes SET attempts = attempts + 1 WHERE rowid=?", (r["rowid"],))
            self._db.commit()
        return False

    # ── Users and sessions ───────────────────────────────────────────────────

    def get_or_create_user(self, email: str) -> tuple[dict, bool]:
        now = time.time()
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row:
                return dict(row), False
            uid = "u_" + secrets.token_urlsafe(12)
            self._db.execute("INSERT INTO users (id, email, created, last_seen) VALUES (?,?,?,?)",
                             (uid, email, now, now))
            self._db.commit()
            return dict(self._db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()), True

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                             (self._h(token), user_id, now, now + SESSION_DAYS * 86400, now))
            self._db.commit()
        return token

    def user_for_session(self, token: str | None) -> tuple[dict | None, bool]:
        """Return (user, renewed). A session used at least a day after its last
        renewal is extended to a fresh SESSION_DAYS (sliding expiry)."""
        if not token:
            return None, False
        now = time.time()
        th = self._h(token)
        with self._lock:
            s = self._db.execute("SELECT * FROM sessions WHERE token_hash=? AND expires>?", (th, now)).fetchone()
            if not s:
                return None, False
            user = self._db.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
            if not user:
                return None, False
            renewed = now - s["renewed"] > 86400
            if renewed:
                self._db.execute("UPDATE sessions SET expires=?, renewed=? WHERE token_hash=?",
                                 (now + SESSION_DAYS * 86400, now, th))
            # A "visit" = first request after 30 minutes of quiet.
            visit = now - user["last_seen"] > 1800
            self._db.execute("UPDATE users SET last_seen=?, visits = visits + ? WHERE id=?",
                             (now, 1 if visit else 0, user["id"]))
            self._db.commit()
            return dict(user), renewed

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._db.execute("DELETE FROM sessions WHERE token_hash=?", (self._h(token),))
                self._db.commit()

    def set_plan(self, user_id: str, plan: str) -> bool:
        if plan not in ("free", "member"):
            return False
        with self._lock:
            cur = self._db.execute(
                "UPDATE users SET plan=?, member_since=CASE WHEN ?='member' THEN COALESCE(member_since, ?) ELSE NULL END WHERE id=?",
                (plan, plan, time.time(), user_id))
            self._db.commit()
            return cur.rowcount == 1

    def list_users(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute("""
                SELECT u.id, u.email, u.created, u.last_seen, u.visits, u.plan, u.member_since,
                       (SELECT COUNT(*) FROM notebooks n WHERE n.user_id=u.id) AS notebooks,
                       (SELECT COUNT(*) FROM chats c WHERE c.user_id=u.id) AS chats
                FROM users u ORDER BY u.last_seen DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            q = lambda sql, *a: self._db.execute(sql, a).fetchone()[0]  # noqa: E731
            return {
                "users": q("SELECT COUNT(*) FROM users"),
                "members": q("SELECT COUNT(*) FROM users WHERE plan='member'"),
                "new_last_7_days": q("SELECT COUNT(*) FROM users WHERE created>?", now - 7 * 86400),
                "active_last_7_days": q("SELECT COUNT(*) FROM users WHERE last_seen>?", now - 7 * 86400),
                "returning": q("SELECT COUNT(*) FROM users WHERE visits>=2"),
                "synced_notebooks": q("SELECT COUNT(*) FROM notebooks"),
                "saved_chats": q("SELECT COUNT(*) FROM chats"),
            }

    # ── Notebooks ────────────────────────────────────────────────────────────

    def list_notebooks(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM notebooks WHERE user_id=? ORDER BY updated DESC",
                                    (user_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_notebook(self, user_id: str, nb: dict) -> tuple[bool, str]:
        nb_id = str(nb.get("id", ""))
        if not ID_RE.match(nb_id):
            return False, "bad notebook id"
        data = json.dumps(nb, ensure_ascii=False)
        if len(data.encode()) > MAX_NOTEBOOK_BYTES:
            return False, "This notebook is too large to sync (1 MB). Export it and start a new one."
        with self._lock:
            exists = self._db.execute("SELECT 1 FROM notebooks WHERE user_id=? AND id=?", (user_id, nb_id)).fetchone()
            if not exists and self._db.execute("SELECT COUNT(*) FROM notebooks WHERE user_id=?",
                                               (user_id,)).fetchone()[0] >= MAX_NOTEBOOKS_PER_USER:
                return False, "Notebook limit reached."
            self._db.execute("INSERT OR REPLACE INTO notebooks VALUES (?,?,?,?,?)",
                             (nb_id, user_id, str(nb.get("title", ""))[:200], time.time(), data))
            self._db.commit()
        return True, ""

    def delete_notebook(self, user_id: str, nb_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM notebooks WHERE user_id=? AND id=?", (user_id, nb_id))
            self._db.commit()

    # ── Chats ────────────────────────────────────────────────────────────────

    def save_chat(self, user_id: str, chat_id: str, title: str, turns: list[dict]) -> bool:
        if not ID_RE.match(chat_id):
            return False
        now = time.time()
        data = json.dumps(turns[-50:], ensure_ascii=False)
        if len(data.encode()) > MAX_NOTEBOOK_BYTES:
            return False
        with self._lock:
            row = self._db.execute("SELECT created FROM chats WHERE user_id=? AND id=?", (user_id, chat_id)).fetchone()
            if not row and self._db.execute("SELECT COUNT(*) FROM chats WHERE user_id=?",
                                            (user_id,)).fetchone()[0] >= MAX_CHATS_PER_USER:
                self._db.execute("DELETE FROM chats WHERE user_id=? AND id=(SELECT id FROM chats WHERE user_id=? "
                                 "ORDER BY updated LIMIT 1)", (user_id, user_id))
            self._db.execute("INSERT OR REPLACE INTO chats VALUES (?,?,?,?,?,?)",
                             (chat_id, user_id, title[:200], row["created"] if row else now, now, data))
            self._db.commit()
        return True

    def list_chats(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, title, created, updated, turns FROM chats WHERE user_id=? "
                                    "ORDER BY updated DESC LIMIT ?", (user_id, limit)).fetchall()
        return [{"id": r["id"], "title": r["title"], "updated": r["updated"],
                 "turns": len(json.loads(r["turns"]))} for r in rows]

    def get_chat(self, user_id: str, chat_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM chats WHERE user_id=? AND id=?", (user_id, chat_id)).fetchone()
        return None if not r else {"id": r["id"], "title": r["title"], "turns": json.loads(r["turns"])}

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM chats WHERE user_id=? AND id=?", (user_id, chat_id))
            self._db.commit()

    # ── Free-tier limits ─────────────────────────────────────────────────────

    def chat_allowance(self, subject: str, chat: str, member: bool) -> dict:
        """Can this subject ask in this chat now? Doesn't record anything."""
        if member:
            return {"ok": True, "member": True}
        day = _today()
        with self._lock:
            rows = self._db.execute("SELECT chat, turns FROM chat_usage WHERE subject=? AND day=?",
                                    (subject, day)).fetchall()
        used = {r["chat"]: r["turns"] for r in rows}
        turns = used.get(chat, 0)
        chats_today = len(used) + (0 if chat in used else 1)
        out = {"ok": True, "turns_used": turns, "turns_max": FREE_TURNS_PER_CHAT,
               "chats_today": min(chats_today, FREE_CHATS_PER_DAY), "chats_max": FREE_CHATS_PER_DAY}
        if turns >= FREE_TURNS_PER_CHAT:
            out.update(ok=False, reason="chat_full")
        elif chat not in used and len(used) >= FREE_CHATS_PER_DAY:
            out.update(ok=False, reason="day_full")
        return out

    def record_turn(self, subject: str, chat: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO chat_usage VALUES (?,?,?,1) ON CONFLICT(subject, day, chat) "
                             "DO UPDATE SET turns = turns + 1", (subject, _today(), chat))
            self._db.execute("DELETE FROM chat_usage WHERE day < ?", (
                datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc).strftime("%Y-%m-%d"),))
            self._db.commit()

    def email_allowance(self, email: str, member: bool) -> dict:
        cap = MEMBER_EMAILED_NOTEBOOKS_PER_MONTH if member else FREE_EMAILED_NOTEBOOKS_PER_MONTH
        with self._lock:
            row = self._db.execute("SELECT count FROM email_usage WHERE email=? AND month=?",
                                   (email, _month())).fetchone()
        used = row["count"] if row else 0
        return {"ok": used < cap, "used": used, "max": cap}

    def record_email(self, email: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO email_usage VALUES (?,?,1) ON CONFLICT(email, month) "
                             "DO UPDATE SET count = count + 1", (email, _month()))
            self._db.commit()


def default_path() -> Path:
    return Path(os.getenv("CITED_APP_DB", str(config.DATA_DIR / "cited.sqlite3")))
