You are writing a topic page for Cited, a study tool built on Aakash Gupta's free Growth Podcast transcripts and free Product Growth newsletter posts.

Topic: {TOPIC_NAME} ({TOPIC_ID}): {TOPIC_DESCRIPTION}

Below are passages on this topic from many episodes and posts, labeled [S0], [S1], and so on, each with its source title, date and speaker. Use only these passages.

Return a single JSON object, and nothing else, with these fields:

- "topic_id": "{TOPIC_ID}"
- "overview": 3 to 5 sentences on what these guests and posts say about the topic overall.
- "perspectives": 4 to 8 distinct, concrete points of view. Each: "claim" (one or two sentences, in your words), "sources" (labels like ["S3", "S9"]), "speakers" (names as labeled).
- "disagreements": where sources genuinely disagree, 0 to 4 items. Each: "question" (what they disagree about) and "sides": a list of {"position", "sources"}. Use [] if they don't disagree. Do not invent disagreement.
- "key_quotes": 5 to 10 items. Each: "source" (label), "speaker" (as labeled), "quote": 8 to 40 words copied character for character from that passage.
- "related_questions": 8 specific questions a product manager could explore next on this topic, answerable from these passages.
- "qa": 10 to 15 question-and-answer pairs a product manager is likely to ask. Each: "question"; "answer": 80 to 180 words of plain markdown, citing passage labels in square brackets like [S4] after each claim; "sources": the labels cited.

Rules:
- Name who said what. Where guests disagree, keep both sides visible; don't blend them into one view.
- Quotes must be exact copies. If you can't quote exactly, paraphrase without quotation marks.
- Transcripts are machine-made, so names and numbers may be misheard. Copy them as written.
- Do not present any guest as endorsing a product, Cited, or anyone else.
