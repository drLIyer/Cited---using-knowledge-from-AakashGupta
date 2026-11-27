"""Fast tests: no network, no models."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cited import chunking, export, generated, links, sources, verify  # noqa: E402

EPISODE = """---
title: "Pricing Masterclass"
date: "2024-08-19"
guests: ["Kyle Poyar"]
speaker_names: "substack"
substack_url: "https://www.news.aakashg.com/p/pricing-masterclass"
youtube_url: "https://www.youtube.com/watch?v=WQMQ4XPTJ_U"
---

**Sponsors:** Attio

# Pricing Masterclass

**Aakash Gupta** (00:00:02)

Welcome back. Today we talk pricing with Kyle.

<!-- sponsor -->
**Aakash Gupta** (00:01:10)

Today's episode is brought to you by Attio, the CRM for startups.

**Kyle Poyar** (00:01:58)

If someone's telling you we tried it, it didn't work, don't stop there. Like think about 10 different ways.

**Kyle Poyar** (01:02:03)

Raise prices on new customers first and grandfather existing ones for a while.
"""


def _episode(tmp_path, text=EPISODE, name="2024-08-19-pricing.md"):
    p = tmp_path / name
    p.write_text(text)
    return sources.parse_episode(p)


def test_parse_episode_skips_sponsor_and_keeps_timestamps(tmp_path):
    doc = _episode(tmp_path)
    texts = [u["text"] for u in doc["units"]]
    assert not any("brought to you by" in t for t in texts)
    assert doc["units"][1]["speaker"] == "Kyle Poyar"
    assert doc["units"][1]["seconds"] == 118
    assert doc["units"][2]["seconds"] == 3723
    assert doc["id"] == "pod:2024-08-19-pricing"


def test_unseparated_speakers_are_never_guessed(tmp_path):
    doc = _episode(tmp_path, EPISODE.replace('speaker_names: "substack"', 'speaker_names: "none (YouTube captions)"'))
    assert {u["speaker"] for u in doc["units"]} == {"Unidentified speaker"}


def test_chunks_keep_whole_turns_and_start_time(tmp_path):
    doc = _episode(tmp_path)
    chunks = chunking.chunk_document(doc, target=20, max_words=40)
    assert chunks[0]["timestamp"] == "00:00:02"
    joined = "\n".join(c["text"] for c in chunks)
    assert "Kyle Poyar: If someone's telling you" in joined
    for c in chunks:
        assert c["words"] <= 40


def test_long_turn_split_on_sentences():
    doc = {"id": "pod:x", "kind": "podcast", "units": [
        {"speaker": "A", "timestamp": "00:00:01", "seconds": 1, "text": "One two three four five. " * 30}]}
    chunks = chunking.chunk_document(doc, target=20, max_words=30)
    assert len(chunks) > 1 and all(c["words"] <= 30 for c in chunks)


def test_newsletter_html_skips_widgets():
    html = ('<h2>Why pricing matters</h2><p>Price is your biggest lever.</p>'
            '<div class="subscription-widget"><p>Subscribe now</p></div>'
            '<p>Subscribe to get more</p><ul><li>Charge more</li></ul>')
    units = sources.html_to_units(html)
    assert [u["text"] for u in units] == ["Price is your biggest lever.", "Charge more"]
    assert units[0]["heading"] == "Why pricing matters"


def test_youtube_link_keeps_video_and_sets_time():
    url = links.youtube_at("https://www.youtube.com/watch?v=abc&t=5", 3723)
    assert "v=abc" in url and "t=3723s" in url and url.count("t=") == 1


def test_text_fragment_escapes_dashes():
    url = links.text_fragment("https://x.com/p/post#old", "Self-serve pricing, explained for everyone")
    assert url.startswith("https://x.com/p/post#:~:text=")
    assert "%2D" in url and "%2C" in url and "-" not in url.split("text=")[1]


def test_quote_verification():
    src = "Kyle Poyar: If someone’s telling you we tried it, it didn’t work, don’t stop there."
    assert verify.quote_in("If someone's telling you we tried it", src)
    assert verify.quote_in("If someone's telling you ... don't stop there", src)
    assert not verify.quote_in("If someone tells you it failed, stop", src)
    report = verify.check_answer('Kyle says "we tried it, it didn\'t work, don\'t stop there" [1].', [src])
    assert report == {"quotes": 1, "unverified": []}


def test_validate_doc_drops_bad_quotes_and_labels():
    chunks = [{"id": "pod:x#0", "text": "Aakash: welcome"},
              {"id": "pod:x#1", "text": "Kyle Poyar: Raise prices on new customers first."}]
    raw = {"summary": "s", "key_takeaways": [
        {"point": "ok", "passage": "P1", "speaker": "Kyle Poyar", "quote": "Raise prices on new customers first"},
        {"point": "bad quote", "passage": "P1", "speaker": "Kyle Poyar", "quote": "Always raise prices"},
        {"point": "bad label", "passage": "P9", "speaker": "", "quote": ""}],
        "frameworks": [], "passage_topics": [{"passage": "P1", "topics": ["pricing", "not-a-topic"]}],
        "questions_answered": ["When to raise prices?"]}
    clean, report = generated.validate_doc(raw, "pod:x", chunks)
    assert report["dropped_quotes"] == 1 and report["bad_labels"] == 1
    assert clean["key_takeaways"][0]["chunk_id"] == "pod:x#1"
    assert clean["key_takeaways"][1]["quote"] == ""
    assert clean["passage_topics"] == {"pod:x#1": ["pricing"]}


def test_validate_topic_renumbers_and_rejects_unverified_qa():
    bundle = [{"id": "a#0", "text": "Kyle: Raise prices on new customers first."},
              {"id": "b#3", "text": "Elena: Usage-based pricing aligns cost with value."}]
    raw = {"overview": "o", "perspectives": [], "disagreements": [], "key_quotes": [],
           "related_questions": ["q"], "qa": [
               {"question": "good", "answer": "Start with new customers [S0] and align to value [S1].", "sources": ["S0", "S1"]},
               {"question": "bad quote", "answer": 'Kyle says "never raise prices at all" [S0].', "sources": ["S0"]},
               {"question": "no cites", "answer": "Just do it.", "sources": []}]}
    clean, report = generated.validate_topic(raw, "pricing", bundle)
    assert [q["question"] for q in clean["qa"]] == ["good"]
    assert clean["qa"][0]["answer"] == "Start with new customers [1] and align to value [2]."
    assert clean["qa"][0]["chunk_ids"] == ["a#0", "b#3"]
    assert report["qa_quotes_unverified"] == 1 and report["qa_dropped"] == 1


def test_parse_json_tolerates_fences():
    assert generated.parse_json('Here:\n```json\n{"a": 1}\n```') == {"a": 1}


NOTEBOOK = {"title": "Pricing research", "created": "2026-11-27", "entries": [
    {"kind": "answer", "question": "How to raise prices?", "answer": "Start with new customers [1].",
     "sources": [{"title": "Pricing Masterclass", "kind": "podcast", "speakers": ["Kyle Poyar"],
                  "timestamp": "1:02:03", "date": "2024-08-19",
                  "url": "https://www.youtube.com/watch?v=abc&t=3723s", "excerpt": "Raise prices…"}],
     "note": "Try this in Q1"},
    {"kind": "quote", "source": {"title": "Post", "kind": "newsletter", "speakers": [], "date": "2025-01-01",
                                 "url": "https://www.news.aakashg.com/p/x", "excerpt": "Price is a lever. ünïcödé —"}},
]}


def test_markdown_export_has_links_note_and_credit():
    md = export.to_markdown(NOTEBOOK)
    assert "# Pricing research" in md
    assert "[Watch](https://www.youtube.com/watch?v=abc&t=3723s)" in md
    assert "around 1:02:03" in md and "Try this in Q1" in md
    assert "The Growth Podcast with Aakash Gupta" in md and "not affiliated" in md


def test_pdf_export_renders_unicode():
    pdf = export.to_pdf(NOTEBOOK)
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
