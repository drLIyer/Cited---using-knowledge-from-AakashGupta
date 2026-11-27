# Cited · knowledge from Aakash Gupta

Ask product questions and get answers grounded in **[The Growth Podcast with Aakash Gupta](https://www.news.aakashg.com)** (free transcripts) and his **free Product Growth newsletter posts**. Every answer cites its sources and links to the moment it was said. Save answers, quotes and topics into notebooks, then export them as Markdown or PDF for Google Drive, Notion or your files.

> Independent project. Not affiliated with or endorsed by Aakash Gupta or his guests. Transcripts are used under the [growth-podcast-transcripts license](https://github.com/aakashg/growth-podcast-transcripts/blob/main/LICENSE): credit and link back, link quotes to the moment, don't re-host the raw files.

## What it does

- **Chat**: hybrid search (keyword + meaning) over ~11,000 passages from 164 episodes and 181 free posts, re-ranked, at most 2 passages per source. Claude writes the answer from those passages only, citing them as [1], [2]…
- **Two follow-ups after every answer**: *explore related topics?* (related questions, topics, more from the archive) and *save this to your notebook?*
- **Topic pages**: 18 fixed topics shared by podcasts, newsletters and notebooks.
- **Notebooks**: kept in the browser; export to Markdown or PDF with sources and the credit line.
- **Guardrails**: links built only from stored metadata (never by the model); every quote checked word for word against its source; low-coverage questions get "not covered" instead of a guess; saved answers reused only when the question *and* its sources match; per-IP hourly limit.

Without `ANTHROPIC_API_KEY` the app runs in **search-only mode**: same search, sources, topics, notebooks and export, no written answer.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest.py                 # fetch sources, build the index
uvicorn main:app --port 8080             # http://localhost:8080
```

Turn on written answers:

```bash
ANTHROPIC_API_KEY=... uvicorn main:app --port 8080
```

## Usage dashboard

Set `CITED_ADMIN_TOKEN` and open `/admin`. It shows daily, weekly and monthly active visitors, stickiness, 7-day return, **weekly savers** (visitors who saved anything: the main number), questions per session and the one-and-done rate, notebooks created, saves and exports, the topics people ask about, and how many questions the archive couldn't cover.

Tracking is anonymous: a random browser id and a random per-visit session id, no names, emails, IP addresses or cookies. Visitors are browsers, so one person on two devices counts twice. Question text is stored only with `CITED_LOG_QUESTIONS=1` (then mention it in your privacy note), and is erased after 30 days. Events are kept 400 days. Data lives in `data/analytics.sqlite3`; on Railway or Render point `CITED_ANALYTICS_DB` at a persistent volume.

## No sign-in (by design, for the free MVP)

Chats aren't kept after the tab closes, and nothing carries over between visits. Notebooks live in the visitor's own browser (about 500 saved items fit) and don't sync across devices; Markdown or PDF export is how people keep them. If browser storage fills up, the app says so instead of failing silently.

## Pre-generated content

Summaries, takeaways, topic pages, related questions and a Q&A bank are generated once and stored in `generated/`. See **[pregen/README.md](pregen/README.md)**: run it with Claude Code on your own machine (uses your Claude plan) or with the Batch API.

## Evals and tests

```bash
python -m pytest -q                      # fast unit tests
python evals/run_evals.py                # retrieval, confidence, links, latency
```

`evals/questions.json` holds 50 questions (broad, specific, newsletter, out-of-scope). Use it to calibrate the confidence thresholds in `cited/search.py` and the saved-answer thresholds in `cited/config.py`.

## Layout

| Path | What |
|---|---|
| `cited/sources.py` | Load transcripts (GitHub) and free newsletter posts (Substack API) |
| `cited/chunking.py` | ~220-word passages from whole speaker turns / paragraphs |
| `cited/index.py`, `cited/search.py` | Index build; BM25 + embeddings → rank fusion → reranker |
| `cited/answer.py`, `cited/verify.py` | Live answers; exact-quote checks |
| `cited/cache.py` | Saved-answer reuse rules |
| `cited/pregen.py`, `cited/generated.py` | One-time generation and validation |
| `cited/export.py` | Markdown and PDF export |
| `cited/analytics.py`, `static/admin.html` | Anonymous usage events and the admin dashboard |
| `main.py`, `static/` | FastAPI app and the web UI (logo from the Cited repo: `static/logo.svg`, `static/logo-mark.svg`) |
| `index_seed/` | Saved passage vectors (no text) so deploys skip re-embedding |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | (unset) | Turns on written answers |
| `CITED_ANSWER_MODEL` | `claude-haiku-5-5` | Model for live answers |
| `CITED_PREGEN_MODEL` | `claude-opus-5-5` | Model for pre-generation (Batch API path) |
| `CITED_RATE_LIMIT_PER_HOUR` | `20` | Questions per IP per hour |
| `CITED_ADMIN_TOKEN` | (unset) | Turns on `/admin`; the token to open it |
| `CITED_ANALYTICS_DB` | `./data/analytics.sqlite3` | Where usage events are stored |
| `CITED_LOG_QUESTIONS` | `0` | `1` stores question text (30 days) to list uncovered questions |
| `CITED_DATA_DIR` / `CITED_INDEX_DIR` | `./data` / `./index` | Raw sources / built index (both git-ignored) |

PDF export bundles the DejaVu Sans font (free license, see dejavu-fonts.github.io).
