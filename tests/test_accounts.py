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
        assert a.chat_allowance(s, "chat_1", "anon")["ok"]
        a.record_turn(s, "chat_1")
    full = a.chat_allowance(s, "chat_1", "anon")
    assert not full["ok"] and full["reason"] == "chat_full"
    for i in range(2, accounts.FREE_CHATS_PER_DAY + 1):
        assert a.chat_allowance(s, f"chat_{i}", "anon")["ok"]
        a.record_turn(s, f"chat_{i}")
    day = a.chat_allowance(s, "chat_new", "anon")
    assert not day["ok"] and day["reason"] == "day_full"
    assert a.chat_allowance(s, "chat_new", "member")["ok"]


def test_signed_in_daily_questions(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    s = "u:user_1234"
    for i in range(accounts.SIGNED_IN_QUESTIONS_PER_DAY):
        assert a.chat_allowance(s, "one_long_chat", "free")["ok"]  # no per-chat limit when signed in
        a.record_turn(s, "one_long_chat")
    out = a.chat_allowance(s, "another_chat", "free")
    assert not out["ok"] and out["reason"] == "questions_full"


def test_membership_payments_and_teaser(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    user, _ = a.get_or_create_user("pat@example.com")
    assert not a.is_member(user)
    paid = a.record_payment("cs_1", user["id"], "pat@example.com", 500, "usd")
    assert a.is_member(paid) and paid["member_until"] > time.time() + 89 * 86400
    assert a.record_payment("cs_1", user["id"], "pat@example.com", 500, "usd") is None  # idempotent
    again = a.record_payment("cs_2", None, "pat@example.com", 700, "usd")             # found by email; stacks
    assert again["member_until"] > paid["member_until"] + 89 * 86400
    stranger = a.record_payment("cs_3", None, "new@example.com", 500, "usd")          # pays before signing in
    assert stranger["email"] == "new@example.com" and a.is_member(stranger)
    a._db.execute("UPDATE users SET member_until=? WHERE id=?", (time.time() - 1, user["id"]))
    expired = a.get_or_create_user("pat@example.com")[0]
    assert not a.is_member(expired)
    # Teaser: only from the 2nd visit, on TEASER_VISITS distinct visits.
    a.set_plan(user["id"], "free")
    u = a.get_or_create_user("pat@example.com")[0]
    assert not a.teaser_due(u)                                   # first visit
    for visit in (2, 3):
        a._db.execute("UPDATE users SET visits=? WHERE id=?", (visit, user["id"]))
        u = a.get_or_create_user("pat@example.com")[0]
        assert a.teaser_due(u)
        a.record_teaser(u); a.record_teaser(a.get_or_create_user("pat@example.com")[0])  # once per visit
    a._db.execute("UPDATE users SET visits=4 WHERE id=?", (user["id"],))
    assert not a.teaser_due(a.get_or_create_user("pat@example.com")[0])


def test_monthly_quota_keys(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    for _ in range(3):
        assert a.quota("v:visitor_1234", 3)["ok"]
        a.record_email("v:visitor_1234")
    assert not a.quota("v:visitor_1234", 3)["ok"]
    assert a.quota("v:other_visitor", 3)["ok"]


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
