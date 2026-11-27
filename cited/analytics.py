"""Anonymous usage analytics, stored in SQLite on the server.

What is recorded: a random visitor id (made in the browser, kept in
localStorage), a random session id (one per browser tab visit), the event type,
and small facts about it (topic, turn number, confidence level, save kind,
export format). No names, emails, IP addresses or cookies. Question text is
recorded only if CITED_LOG_QUESTIONS=1 (useful for finding coverage gaps and
growing the eval set; say so in your privacy note if you turn it on), and
questions older than CITED_QUESTION_RETENTION_DAYS are erased.

"Users" are browsers: one person on two devices counts twice, and private
windows count as new visitors. Trends are reliable; absolute counts run high.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

CLIENT_EVENTS = {"visit", "save", "notebook_created", "export", "related_click", "topic_open"}
SERVER_EVENTS = {"ask"}
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_FIELD_LIMITS = {"topic": 40, "kind": 20, "value": 40, "confidence": 10}

LOG_QUESTIONS = os.getenv("CITED_LOG_QUESTIONS", "0") == "1"
QUESTION_RETENTION_DAYS = int(os.getenv("CITED_QUESTION_RETENTION_DAYS", "30"))
EVENT_RETENTION_DAYS = int(os.getenv("CITED_EVENT_RETENTION_DAYS", "400"))  # > a year, for year-on-year


def _clean_id(value) -> str | None:
    return value if isinstance(value, str) and _ID.match(value) else None


class Analytics:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts REAL NOT NULL, day TEXT NOT NULL, visitor TEXT NOT NULL, session TEXT NOT NULL,
            type TEXT NOT NULL, topic TEXT, kind TEXT, value TEXT, turn INTEGER,
            confidence TEXT, question TEXT)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS ev_day ON events(day)")
        self._db.execute("CREATE INDEX IF NOT EXISTS ev_visitor ON events(visitor)")
        self._db.commit()

    def log(self, type_: str, visitor: str | None, session: str | None, *, topic: str | None = None,
            kind: str | None = None, value: str | None = None, turn: int | None = None,
            confidence: str | None = None, question: str | None = None, now: float | None = None) -> bool:
        visitor, session = _clean_id(visitor), _clean_id(session)
        if type_ not in CLIENT_EVENTS | SERVER_EVENTS or not visitor or not session:
            return False
        fields = {"topic": topic, "kind": kind, "value": value, "confidence": confidence}
        fields = {k: (str(v)[:_FIELD_LIMITS[k]] if v is not None else None) for k, v in fields.items()}
        ts = now or time.time()
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        q = question[:500] if (LOG_QUESTIONS and question) else None
        with self._lock:
            self._db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, day, visitor, session, type_, fields["topic"], fields["kind"], fields["value"],
                 int(turn) if turn is not None else None, fields["confidence"], q))
            self._db.commit()
        return True

    def purge_questions(self, now: float | None = None) -> None:
        """Erase question text past its retention and drop events older than a year+."""
        now = now or time.time()
        with self._lock:
            self._db.execute("UPDATE events SET question = NULL WHERE question IS NOT NULL AND ts < ?",
                             (now - QUESTION_RETENTION_DAYS * 86400,))
            self._db.execute("DELETE FROM events WHERE ts < ?", (now - EVENT_RETENTION_DAYS * 86400,))
            self._db.commit()

    # ── Metrics ───────────────────────────────────────────────────────────────

    def metrics(self, days: int = 30, now: float | None = None) -> dict:
        now = now or time.time()
        today = datetime.fromtimestamp(now, timezone.utc).date()
        start = today - timedelta(days=days - 1)
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, day, visitor, session, type, topic, kind, value, turn, confidence, question "
                "FROM events WHERE day >= ? ORDER BY ts", ((start - timedelta(days=30)).isoformat(),)).fetchall()
            first_seen = dict(self._db.execute("SELECT visitor, MIN(day) FROM events GROUP BY visitor").fetchall())
        cols = ["ts", "day", "visitor", "session", "type", "topic", "kind", "value", "turn", "confidence", "question"]
        all_events = [dict(zip(cols, r)) for r in rows]
        events = [e for e in all_events if e["day"] >= start.isoformat()]

        by_day = defaultdict(set)
        for e in all_events:
            by_day[e["day"]].add(e["visitor"])

        def active(day, window):
            seen = set()
            for i in range(window):
                seen |= by_day.get((day - timedelta(days=i)).isoformat(), set())
            return len(seen)

        series = []
        for i in range(days):
            d = start + timedelta(days=i)
            series.append({"day": d.isoformat(), "dau": len(by_day.get(d.isoformat(), set())),
                           "new": sum(1 for v in by_day.get(d.isoformat(), set()) if first_seen.get(v) == d.isoformat()),
                           "wau": active(d, 7), "mau": active(d, 30)})
        dau_today = series[-1]["dau"]
        mau = series[-1]["mau"]
        avg_dau = sum(s["dau"] for s in series) / max(1, days)

        # Sessions: turns per session, one-and-done, saves/exports per session.
        sessions = defaultdict(lambda: {"asks": 0, "saves": 0, "exports": 0, "visitor": None})
        for e in events:
            s = sessions[e["session"]]
            s["visitor"] = e["visitor"]
            if e["type"] == "ask":
                s["asks"] += 1
            elif e["type"] == "save":
                s["saves"] += 1
            elif e["type"] == "export":
                s["exports"] += 1
        asking = [s for s in sessions.values() if s["asks"] > 0]
        hist = Counter()
        for s in asking:
            n = s["asks"]
            hist["1" if n == 1 else "2" if n == 2 else "3-5" if n <= 5 else "6+"] += 1

        def share(n, d):
            return round(n / d, 3) if d else None

        # Weekly savers: the suggested main number.
        week_start = (today - timedelta(days=6)).isoformat()
        weekly_savers = len({e["visitor"] for e in events if e["type"] == "save" and e["day"] >= week_start})

        # 7-day return: visitors first seen 8-37 days ago who came back on a later day within 7 days.
        returned, cohort = 0, 0
        for v, first in first_seen.items():
            f = datetime.fromisoformat(first).date()
            if not (today - timedelta(days=37) <= f <= today - timedelta(days=8)):
                continue
            cohort += 1
            if any(v in by_day.get((f + timedelta(days=i)).isoformat(), set()) for i in range(1, 8)):
                returned += 1

        asks = [e for e in events if e["type"] == "ask"]
        topic_asks = Counter(e["topic"] for e in asks if e["topic"])
        topic_opens = Counter(e["topic"] for e in events if e["type"] == "topic_open" and e["topic"])
        out = {
            "window_days": days,
            "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            "users": {
                "dau_today": dau_today, "avg_dau": round(avg_dau, 1), "wau": series[-1]["wau"], "mau": mau,
                "stickiness_dau_over_mau": share(avg_dau, mau),
                "new_visitors": sum(s["new"] for s in series),
                "returned_within_7_days": share(returned, cohort), "return_cohort_size": cohort,
                "weekly_savers": weekly_savers,
            },
            "sessions": {
                "with_questions": len(asking),
                "turns_histogram": {k: hist.get(k, 0) for k in ("1", "2", "3-5", "6+")},
                "one_and_done": share(hist.get("1", 0), len(asking)),
                "avg_turns": round(sum(s["asks"] for s in asking) / len(asking), 2) if asking else None,
                "with_save": share(sum(1 for s in asking if s["saves"]), len(asking)),
                "with_export": share(sum(1 for s in asking if s["exports"]), len(asking)),
            },
            "notebooks": {
                "created": sum(1 for e in events if e["type"] == "notebook_created"),
                "saves_by_kind": dict(Counter(e["kind"] for e in events if e["type"] == "save")),
                "exports_by_format": dict(Counter(e["kind"] for e in events if e["type"] == "export")),
                "related_clicks": sum(1 for e in events if e["type"] == "related_click"),
            },
            "topics": {
                "asked": topic_asks.most_common(),
                "opened": topic_opens.most_common(),
                "questions_without_topic": sum(1 for e in asks if not e["topic"]),
            },
            "answers": {
                "asks": len(asks),
                "confidence": dict(Counter(e["confidence"] for e in asks if e["confidence"])),
                "modes": dict(Counter(e["kind"] for e in asks if e["kind"])),
            },
            "daily": series,
        }
        if LOG_QUESTIONS:
            gaps = Counter(e["question"].strip().lower() for e in asks
                           if e["confidence"] == "low" and e["question"])
            out["answers"]["not_covered_questions"] = gaps.most_common(25)
        return out


def default_path() -> Path:
    return Path(os.getenv("CITED_ANALYTICS_DB", str(config.DATA_DIR / "analytics.sqlite3")))


def to_json(metrics: dict) -> str:
    return json.dumps(metrics, indent=2)
