# Pre-generate Cited's content with Claude Code (your terminal)

This runs every free episode and newsletter post through Claude Code on your own
machine, using the Claude plan of the account you're logged in with. There is no
API key and no API charge, but your plan's usage limits apply, so it may take a
few sittings. The script picks up where it stopped.

What you get: for each source, a summary, key takeaways with exact quotes,
frameworks, topic tags and the questions it answers. Then, for each topic, an
overview, perspectives, disagreements, quotes, related questions and a Q&A bank.
Everything is checked before it's saved: quotes must match the source word for
word, and citations must point at real passages.

## 1. One-time setup (about 15 minutes)

You need Python 3.11+, git and Claude Code (`claude`) logged in.

```bash
git clone https://github.com/drLIyer/Cited---using-knowledge-from-AakashGupta.git
cd Cited---using-knowledge-from-AakashGupta
git checkout claude/adoring-gates-xcmb0c
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest.py
```

`ingest.py` downloads the free transcripts and newsletter posts, plus two small
search models (~100 MB), and builds the index. It takes 5 to 15 minutes.

## 2. Try three documents first

```bash
python scripts/pregenerate.py export documents --limit 3
bash pregen/run_with_claude_code.sh
python scripts/pregenerate.py import
```

Check what `import` prints: how many items were stored and how many quotes were
dropped for not matching exactly. Open a file in `generated/docs/` to see the
result. If the drop rate looks high, stop and tell Claude in the main session.

## 3. All documents (pass 1)

```bash
python scripts/pregenerate.py export documents
bash pregen/run_with_claude_code.sh
python scripts/pregenerate.py import
```

There are about 345 documents. If Claude Code hits a usage limit, the script
stops. Run `bash pregen/run_with_claude_code.sh` again later and it continues.
Run `import` whenever you like; it is safe to repeat.

## 4. Topic pages (pass 2, after pass 1)

```bash
python scripts/ingest.py            # optional: only if sources changed
python scripts/pregenerate.py export topics
bash pregen/run_with_claude_code.sh
python scripts/pregenerate.py import
```

About 18 requests, each larger than a document request.

## 5. Send the results back

Only `generated/` is shared: summaries and short quotes, no raw transcripts.

```bash
git add generated/
git commit -m "Add pre-generated notes and topic pages"
git push
```

## Notes

- Model: Opus by default. Set `CITED_CC_MODEL=sonnet` to use less of your plan.
- Passage ids depend on the chunk settings, so keep `CITED_CHUNK_WORDS` and
  `CITED_CHUNK_MAX_WORDS` at their defaults (220 and 340). Otherwise the deployed
  app won't match your generated files.
- Using the API instead: `python scripts/pregenerate.py estimate documents` shows
  the estimated Batch API cost; `submit` and `collect` run it with
  `ANTHROPIC_API_KEY` set.
- Files under `data/` (raw sources, inputs, outputs) are never committed: the
  transcript license asks that the raw files aren't re-hosted.
