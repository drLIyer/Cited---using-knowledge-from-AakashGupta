"""Export a notebook to Markdown or PDF.

A notebook (kept in the user's browser) looks like:
    {"title", "created", "entries": [entry, ...]}
entry kinds:
    answer: {"kind": "answer", "question", "answer", "sources": [source], "note"?}
    quote:  {"kind": "quote", "source": source, "note"?}
    topic:  {"kind": "topic", "topic_name", "overview", "points": [str], "sources": [source]}
source: {"title", "kind", "speakers", "timestamp", "date", "url", "excerpt"?}

Exports carry the credit line the transcript license asks for, link every
source back to its original, and quote only short excerpts.
"""
from __future__ import annotations

import re
import time
from io import BytesIO

from . import config

MAX_ENTRIES = 300
EXCERPT_WORDS = 60


def _excerpt(text: str, words: int = EXCERPT_WORDS) -> str:
    parts = (text or "").split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def _source_line(n: int | None, s: dict) -> str:
    who = ", ".join(s.get("speakers") or []) or ("Aakash Gupta" if s.get("kind") == "newsletter" else "")
    bits = [f"**{s.get('title', 'Untitled')}**"]
    if who:
        bits.append(who)
    if s.get("timestamp"):
        bits.append(f"around {s['timestamp']}")
    if s.get("date"):
        bits.append(s["date"])
    label = "Watch" if s.get("kind") == "podcast" else "Read"
    line = " · ".join(bits)
    if s.get("url"):
        line += f" · [{label}]({s['url']})"
    return f"{n}. {line}" if n is not None else f"- {line}"


def _credit() -> str:
    return (f"Sources: [{config.CREDIT_NAME}]({config.CREDIT_URL}) and Aakash Gupta's free Product Growth "
            "newsletter. Quotes are from machine-made transcripts; check the linked clip before reusing a "
            "quote. Cited is an independent project, not affiliated with or endorsed by Aakash Gupta or his guests.")


def to_markdown(nb: dict) -> str:
    lines = [f"# {nb.get('title') or 'Cited notebook'}", ""]
    created = nb.get("created") or time.strftime("%Y-%m-%d")
    lines += [f"*Created {created[:10]} with Cited · exported {time.strftime('%Y-%m-%d')}*", ""]
    for e in (nb.get("entries") or [])[:MAX_ENTRIES]:
        kind = e.get("kind")
        if kind == "answer":
            lines += [f"## {e.get('question', '').strip() or 'Answer'}", "", e.get("answer", "").strip(), ""]
            if e.get("sources"):
                lines += ["**Sources**", ""]
                lines += [_source_line(i, s) for i, s in enumerate(e["sources"], 1)]
                lines.append("")
        elif kind == "quote":
            s = e.get("source") or {}
            lines += [f"> {_excerpt(s.get('excerpt') or s.get('text', ''))}", ">", f"> {_source_line(None, s)[2:]}", ""]
        elif kind == "topic":
            lines += [f"## Topic: {e.get('topic_name', '')}", "", e.get("overview", "").strip(), ""]
            lines += [f"- {p}" for p in e.get("points") or []]
            if e.get("sources"):
                lines += ["", "**Sources**", ""]
                lines += [_source_line(i, s) for i, s in enumerate(e["sources"], 1)]
            lines.append("")
        if e.get("note"):
            lines += [f"**My note:** {e['note'].strip()}", ""]
        lines += ["---", ""]
    lines += [f"<sub>{_credit()}</sub>", ""]
    return "\n".join(lines)


# ── PDF ───────────────────────────────────────────────────────────────────────

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\*)([^*]+)\*(?!\*)")


def _plain_inline(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    return text.replace("<sub>", "").replace("</sub>", "")


def to_pdf(nb: dict) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    fonts = config.ROOT / "static" / "fonts"
    pdf = FPDF(format="Letter")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, 18)
    pdf.add_font("Body", "", str(fonts / "DejaVuSans.ttf"))
    pdf.add_font("Body", "B", str(fonts / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    width = pdf.epw

    def para(text, size=10.5, style="", color=(30, 30, 30), gap=2.5, link=""):
        pdf.set_font("Body", style, size)
        pdf.set_text_color(*color)
        pdf.multi_cell(width, size * 0.5, _plain_inline(text), markdown=True, link=link,
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(gap)

    def source_rows(sources):
        for i, s in enumerate(sources, 1):
            line = _source_line(i, s)
            para(line, size=9, color=(70, 70, 70), gap=1, link=s.get("url", ""))
            if s.get("url"):
                para(s["url"], size=7.5, color=(37, 99, 160), gap=1.5, link=s["url"])

    para(nb.get("title") or "Cited notebook", size=20, style="B", gap=1)
    para(f"Exported {time.strftime('%Y-%m-%d')} with Cited", size=9, color=(110, 110, 110), gap=6)

    for e in (nb.get("entries") or [])[:MAX_ENTRIES]:
        kind = e.get("kind")
        if kind == "answer":
            para(e.get("question", "Answer"), size=14, style="B", gap=2)
            for block in re.split(r"\n\s*\n", e.get("answer", "").strip()):
                para(block.replace("\n", " "), gap=2.5)
            if e.get("sources"):
                para("Sources", size=10, style="B", gap=1)
                source_rows(e["sources"])
        elif kind == "quote":
            s = e.get("source") or {}
            para(f"“{_excerpt(s.get('excerpt') or s.get('text', ''))}”", size=11, gap=1.5)
            source_rows([s])
        elif kind == "topic":
            para(f"Topic: {e.get('topic_name', '')}", size=14, style="B", gap=2)
            para(e.get("overview", ""), gap=2)
            for p in e.get("points") or []:
                para(f"• {p}", gap=1.5)
            if e.get("sources"):
                para("Sources", size=10, style="B", gap=1)
                source_rows(e["sources"])
        if e.get("note"):
            para(f"**My note:** {e['note']}", size=10, color=(60, 60, 60), gap=2)
        pdf.ln(3)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + width, pdf.get_y())
        pdf.ln(4)

    para(_credit(), size=8, color=(110, 110, 110), gap=0)
    out = BytesIO()
    pdf.output(out)
    return out.getvalue()
