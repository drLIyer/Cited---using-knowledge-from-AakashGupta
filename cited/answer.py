"""Live answers with Claude, grounded in the retrieved passages.

Claude cites passages as [1], [2]...; the app turns those into links from
stored metadata. Claude never writes URLs. After streaming, every quote in the
answer is checked against the cited passages and any that do not match are
reported to the UI.

Without an API key the app runs in search-only mode (no generated answer).
"""
from __future__ import annotations

import logging
import os
import re
from typing import AsyncIterator

from . import config, verify

log = logging.getLogger(__name__)

SYSTEM = f"""You are Cited, a study assistant that answers product management questions using only passages from {config.CREDIT_NAME} (free podcast transcripts) and Aakash Gupta's free Product Growth newsletter posts.

How to answer:
- Use only the numbered passages in the user's message. If they don't answer the question, say so plainly and mention what nearby topics they do cover. Never fill gaps from general knowledge.
- Open with a one or two sentence direct answer. Then give the supporting points, naming who said each one (the guest, or Aakash in the newsletter).
- Put the passage number in square brackets after every claim, like [2] or [1][4].
- Where sources disagree, show both sides.
- You may include short quotes, copied exactly from a passage, in double quotes. If you can't copy it exactly, paraphrase without quotation marks.
- Never write URLs or links; the app adds them.
- Transcripts are machine-made, so names and numbers may be misheard; copy them as written.
- Never imply that a guest endorses Cited or any product.
- Keep it under 250 words unless the question needs more. Use plain markdown."""

_CITE = re.compile(r"\[(\d{1,2})\]")


def llm_enabled() -> bool:
    if os.getenv("CITED_LLM", "").lower() in ("0", "off", "false"):
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def format_passages(results: list[dict]) -> str:
    parts = []
    for n, r in enumerate(results, 1):
        kind = "Podcast" if r["kind"] == "podcast" else "Newsletter"
        who = ", ".join(r["speakers"]) if r["speakers"] else "Aakash Gupta"
        when = f" at about {r['timestamp']}" if r["timestamp"] else ""
        parts.append(f"[{n}] {kind}: \"{r['title']}\" ({r['date']}{when}); speaking: {who}\n{r['text']}")
    return "\n\n".join(parts)


def build_messages(question: str, history: list[dict], results: list[dict]) -> list[dict]:
    msgs = []
    for turn in history[-config.MAX_HISTORY_TURNS * 2:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            msgs.append({"role": turn["role"], "content": str(turn["content"])[:4000]})
    # Passages go into this turn only; they are not kept in history.
    msgs.append({"role": "user", "content": f"Passages:\n\n{format_passages(results)}\n\nQuestion: {question}"})
    return msgs


def cited_numbers(answer: str, n_results: int) -> list[int]:
    nums = [int(m) for m in _CITE.findall(answer)]
    return list(dict.fromkeys(n for n in nums if 1 <= n <= n_results))


def check(answer: str, results: list[dict]) -> dict:
    nums = cited_numbers(answer, len(results))
    texts = [results[n - 1]["text"] for n in nums] or [r["text"] for r in results]
    report = verify.check_answer(answer, texts)
    report["cited"] = nums
    return report


async def stream_answer(question: str, history: list[dict], results: list[dict]) -> AsyncIterator[dict]:
    """Yield {"type": "token", "text"} events, then {"type": "final", ...}."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    text = ""
    try:
        async with client.messages.stream(
            model=config.ANSWER_MODEL,
            max_tokens=4000,
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=build_messages(question, history, results),
        ) as stream:
            async for chunk in stream.text_stream:
                text += chunk
                yield {"type": "token", "text": chunk}
            final = await stream.get_final_message()
        if final.stop_reason == "refusal":
            yield {"type": "error", "message": "This question couldn't be answered. Try rephrasing it."}
            return
        yield {"type": "final", "text": text, "stop_reason": final.stop_reason,
               "usage": {"input": final.usage.input_tokens, "output": final.usage.output_tokens}}
    except anthropic.RateLimitError:
        yield {"type": "error", "message": "Cited is busy right now. Please try again in a minute."}
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
        yield {"type": "error", "message": "The answer service had a problem. The sources below still apply."}
    except anthropic.APIConnectionError:
        yield {"type": "error", "message": "Couldn't reach the answer service. The sources below still apply."}
