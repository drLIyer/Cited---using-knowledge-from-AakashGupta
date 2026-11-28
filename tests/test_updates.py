import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import index, updates  # noqa: E402


def fake_index(docs):
    return SimpleNamespace(docs={d["id"]: d for d in docs})


def test_first_seen_keeps_earlier_stamps(tmp_path):
    updates.record_first_seen(["a", "b"], tmp_path, when="2026-11-01T00:00:00Z")
    seen = updates.record_first_seen(["b", "c"], tmp_path, when="2026-11-28T00:00:00Z")
    # A fresh index folder starts from the committed baseline (index_seed/first_seen.json), then adds to it.
    mine = {k: seen[k] for k in ("a", "b", "c")}
    assert mine == {"a": "2026-11-01T00:00:00Z", "b": "2026-11-01T00:00:00Z", "c": "2026-11-28T00:00:00Z"}
    assert json.loads((tmp_path / "first_seen.json").read_text()) == seen


def test_new_docs_need_arrival_after_since_and_recent_publication():
    idx = fake_index([
        {"id": "fresh", "date": "2026-11-27"},
        {"id": "old-but-just-exposed", "date": "2022-01-01"},
        {"id": "seen-before", "date": "2026-11-20"},
    ])
    seen = {"fresh": "2026-11-28T00:10:00Z", "old-but-just-exposed": "2026-11-28T00:10:00Z",
            "seen-before": "2026-11-21T00:00:00Z", "missing": "2026-11-28T00:10:00Z"}
    assert updates.new_doc_ids(idx, seen, "2026-11-27T20:00:00Z") == ["fresh"]
    assert updates.new_doc_ids(idx, seen, "2026-11-28T01:00:00Z") == []
    assert updates.new_doc_ids(idx, seen, "not a date") == []


def test_promo_detector():
    assert index.is_promo("Today's episode is brought to you by Attio. Use my code AAKASH for 20% off.")
    assert index.is_promo("I am accepting a group of 30 PMs into a 12-week cohort.")
    assert not index.is_promo("We raised prices on new customers first and grandfathered existing ones.")
    assert not index.is_promo("Our cohort analysis showed retention improved after onboarding changes.")
