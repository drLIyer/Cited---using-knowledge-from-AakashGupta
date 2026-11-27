import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import analytics  # noqa: E402

DAY = 86400
NOW = datetime(2026, 11, 27, 12, tzinfo=timezone.utc).timestamp()
V1, V2, V3 = "visitor_aaaa1111", "visitor_bbbb2222", "visitor_cccc3333"


def make(tmp_path):
    return analytics.Analytics(tmp_path / "a.sqlite3")


def test_rejects_bad_ids_and_unknown_events(tmp_path):
    a = make(tmp_path)
    assert not a.log("visit", "x", "session_1234", now=NOW)            # id too short
    assert not a.log("visit", "visitor_1234", "bad id!", now=NOW)       # invalid characters
    assert not a.log("delete_everything", V1, "session_1234", now=NOW)  # unknown type
    assert a.log("visit", V1, "session_1234", now=NOW)


def test_active_users_sessions_and_notebook_rates(tmp_path):
    a = make(tmp_path)
    # V1: today, one session with 3 questions, saves and exports.
    for t in range(3):
        a.log("ask", V1, "sess_v1_today", topic="pricing", kind="answered", turn=t + 1, confidence="high", now=NOW)
    a.log("notebook_created", V1, "sess_v1_today", now=NOW)
    a.log("save", V1, "sess_v1_today", kind="answer", now=NOW)
    a.log("export", V1, "sess_v1_today", kind="pdf", now=NOW)
    # V2: today, one question and leaves (off-topic).
    a.log("ask", V2, "sess_v2_today", kind="not_covered", turn=1, confidence="low", now=NOW)
    # V3: 10 days ago only.
    a.log("ask", V3, "sess_v3_old", topic="careers", kind="answered", turn=1, confidence="high", now=NOW - 10 * DAY)

    m = a.metrics(days=30, now=NOW)
    assert m["users"]["dau_today"] == 2
    assert m["users"]["wau"] == 2 and m["users"]["mau"] == 3
    assert m["users"]["weekly_savers"] == 1
    s = m["sessions"]
    assert s["with_questions"] == 3
    assert s["turns_histogram"] == {"1": 2, "2": 0, "3-5": 1, "6+": 0}
    assert s["one_and_done"] == round(2 / 3, 3)
    assert s["with_save"] == round(1 / 3, 3) and s["with_export"] == round(1 / 3, 3)
    assert m["notebooks"]["created"] == 1 and m["notebooks"]["exports_by_format"] == {"pdf": 1}
    assert dict(m["topics"]["asked"]) == {"pricing": 3, "careers": 1}
    assert m["topics"]["questions_without_topic"] == 1
    assert m["answers"]["confidence"] == {"high": 4, "low": 1}
    assert len(m["daily"]) == 30 and m["daily"][-1]["dau"] == 2


def test_seven_day_return(tmp_path):
    a = make(tmp_path)
    a.log("visit", V1, "sess_first_v1", now=NOW - 20 * DAY)
    a.log("visit", V1, "sess_back_v1", now=NOW - 17 * DAY)  # came back 3 days later
    a.log("visit", V2, "sess_first_v2", now=NOW - 15 * DAY)  # never came back
    m = a.metrics(days=30, now=NOW)
    assert m["users"]["return_cohort_size"] == 2
    assert m["users"]["returned_within_7_days"] == 0.5


def test_questions_not_stored_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "LOG_QUESTIONS", False)
    a = make(tmp_path)
    a.log("ask", V1, "sess_v1_today", confidence="low", question="secret question", now=NOW)
    assert a._db.execute("SELECT question FROM events").fetchone()[0] is None
    assert "not_covered_questions" not in a.metrics(now=NOW)["answers"]


def test_retention_purges_old_questions_and_events(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "LOG_QUESTIONS", True)
    a = make(tmp_path)
    a.log("ask", V1, "sess_v1_old", confidence="low", question="old question", now=NOW - 40 * DAY)
    a.log("ask", V1, "sess_v1_ancient", confidence="low", question="ancient", now=NOW - 500 * DAY)
    a.log("ask", V2, "sess_v2_new", confidence="low", question="new question", now=NOW)
    a.purge_questions(now=NOW)
    rows = a._db.execute("SELECT question FROM events ORDER BY ts").fetchall()
    assert rows == [(None,), ("new question",)]
    assert a.metrics(now=NOW)["answers"]["not_covered_questions"] == [("new question", 1)]
