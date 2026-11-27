You are preparing study notes from ONE source: a Growth Podcast episode or a free Product Growth newsletter post by Aakash Gupta. The source follows, split into passages labeled [P0], [P1], and so on. Podcast passages show the speaker before each turn and the start time.

Use only what is in this source. Never add outside knowledge.

Return a single JSON object, and nothing else, with these fields:

- "doc_id": the doc_id shown in the source header, copied exactly.
- "summary": 2 to 3 plain sentences on what this source covers and who is speaking.
- "key_takeaways": 4 to 8 items. Each item:
  - "point": the takeaway in one sentence, in your words.
  - "passage": the label of the passage it comes from, like "P12".
  - "speaker": who said it, exactly as labeled in that passage ("" for newsletter posts).
  - "quote": 8 to 40 words copied character for character from that passage. Do not fix grammar, do not join sentences from different places, do not paraphrase.
- "frameworks": 0 to 5 named frameworks, models or step-by-step methods the source teaches. Each: "name", "description" (one or two sentences), "passage". Use [] if there are none.
- "passage_topics": for each passage with real substance, {"passage": "P3", "topics": ["topic-id", ...]} with 1 to 3 topic ids from the list below. Leave out host intros, sponsor reads, small talk and sign-offs.
- "questions_answered": 3 to 6 questions a product manager might ask that this source answers well.

Topic ids (use only these):
{TOPIC_LIST}

Rules:
- Attribute every takeaway to the person who said it. Never put words in a guest's mouth.
- Transcripts are machine-made, so names and numbers may be misheard. Copy them as written; don't correct them.
- Do not present any guest as endorsing a product, Cited, or anyone else.
