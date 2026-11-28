import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import accounts  # noqa: E402


def make(tmp_path, monkeypatch):
    monkeypatch.setenv("CITED_SECRET", "test-secret")
    return accounts.Accounts(tmp_path / "app.sqlite3")


def test_email_normalization():
    assert accounts.normalize_email("  Pat@Example.COM ") == "pat@example.com"
    assert accounts.normalize_email("not-an-email") is None
    assert accounts.normalize_email("a@b") is None


def test_code_flow_and_hashing(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    code, why = a.create_code("pat@example.com", "1.2.3.4")
    assert code and len(code) == 6 and not why
    stored = a._db.execute("SELECT code_hash FROM codes").fetchone()[0]
    assert code not in stored                      # only a keyed hash is stored
    assert not a.verify_code("pat@example.com", "000000" if code != "000000" else "111111")
    assert a.verify_code("pat@example.com", code)
    assert not a.verify_code("pat@example.com", code)  # single use


def test_code_attempt_cap_and_expiry(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    code, _ = a.create_code("pat@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(accounts.CODE_ATTEMPTS):
        assert not a.verify_code("pat@example.com", wrong)
    assert not a.verify_code("pat@example.com", code)  # locked after too many wrong guesses
    code2, _ = a.create_code("other@example.com")
    a._db.execute("UPDATE codes SET expires = ?", (time.time() - 1,))
    assert not a.verify_code("other@example.com", code2)


def test_code_rate_limit(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    for _ in range(accounts.CODES_PER_EMAIL_PER_HOUR):
        assert a.create_code("pat@example.com")[0]
    code, why = a.create_code("pat@example.com")
    assert code is None and "Too many" in why


def test_sessions_slide_and_end(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    user, created = a.get_or_create_user("pat@example.com")
    assert created and user["plan"] == "free"
    assert a.get_or_create_user("pat@example.com")[1] is False
    token = a.create_session(user["id"])
    assert a.user_for_session(token)[0]["email"] == "pat@example.com"
    # Pretend the last renewal was 2 days ago: the next use extends to a fresh 180 days.
    a._db.execute("UPDATE sessions SET renewed = ?, expires = ?", (time.time() - 2 * 86400, time.time() + 5 * 86400))
    _, renewed = a.user_for_session(token)
    exp = a._db.execute("SELECT expires FROM sessions").fetchone()[0]
    assert renewed and exp > time.time() + (accounts.SESSION_DAYS - 1) * 86400
    a.end_session(token)
    assert a.user_for_session(token)[0] is None
    assert a.user_for_session("made-up-token")[0] is None


def test_free_chat_limits(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    s = "v:visitor_1234"
    for _ in range(accounts.FREE_TURNS_PER_CHAT):
        assert a.chat_allowance(s, "chat_1", member=False)["ok"]
        a.record_turn(s, "chat_1")
    full = a.chat_allowance(s, "chat_1", member=False)
    assert not full["ok"] and full["reason"] == "chat_full"
    for i in range(2, accounts.FREE_CHATS_PER_DAY + 1):
        assert a.chat_allowance(s, f"chat_{i}", member=False)["ok"]
        a.record_turn(s, f"chat_{i}")
    day = a.chat_allowance(s, "chat_new", member=False)
    assert not day["ok"] and day["reason"] == "day_full"
    assert a.chat_allowance(s, "chat_new", member=True)["ok"]


def test_email_allowance(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    for _ in range(accounts.FREE_EMAILED_NOTEBOOKS_PER_MONTH):
        assert a.email_allowance("pat@example.com", member=False)["ok"]
        a.record_email("pat@example.com")
    assert not a.email_allowance("pat@example.com", member=False)["ok"]
    assert a.email_allowance("pat@example.com", member=True)["ok"]


def test_notebooks_chats_plans_and_stats(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    user, _ = a.get_or_create_user("pat@example.com")
    ok, _ = a.save_notebook(user["id"], {"id": "nb_abcd", "title": "Pricing", "entries": [{"kind": "quote"}]})
    assert ok and a.list_notebooks(user["id"])[0]["title"] == "Pricing"
    assert not a.save_notebook(user["id"], {"id": "bad id!", "title": "x"})[0]
    huge = {"id": "nb_huge", "title": "x", "entries": ["y" * (accounts.MAX_NOTEBOOK_BYTES + 10)]}
    assert not a.save_notebook(user["id"], huge)[0]
    assert a.save_chat(user["id"], "chat_abcd", "How to price?", [{"q": "How to price?", "answer": "..."}])
    assert a.list_chats(user["id"])[0]["turns"] == 1
    assert a.get_chat(user["id"], "chat_abcd")["title"] == "How to price?"
    assert a.set_plan(user["id"], "member") and not a.set_plan(user["id"], "gold")
    st = a.stats()
    assert st["users"] == 1 and st["members"] == 1 and st["synced_notebooks"] == 1 and st["saved_chats"] == 1
