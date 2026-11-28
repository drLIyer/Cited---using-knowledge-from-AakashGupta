"""Send email. Resend when configured; otherwise write to the server log.

Settings:
    RESEND_API_KEY      turns on real sending through Resend (https://resend.com)
    CITED_MAIL_FROM     sender, e.g. "Cited <hello@your-domain.com>" (domain verified in Resend)

Without RESEND_API_KEY every email (codes included) is logged, so the whole
sign-in flow can be tested locally. Never run a public site that way: anyone
with log access could sign in as anyone.
"""
from __future__ import annotations

import base64
import logging
import os

log = logging.getLogger("cited.mail")


def configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def send(to: str, subject: str, text: str, html: str | None = None,
         attachments: list[tuple[str, bytes]] | None = None) -> bool:
    if not configured():
        names = ", ".join(n for n, _ in attachments or [])
        log.warning("EMAIL (not sent: RESEND_API_KEY unset) to=%s subject=%r attachments=[%s]\n%s",
                    to, subject, names, text)
        return True
    import httpx

    payload = {
        "from": os.getenv("CITED_MAIL_FROM", "Cited <onboarding@resend.dev>"),
        "to": [to], "subject": subject, "text": text,
    }
    if html:
        payload["html"] = html
    if attachments:
        payload["attachments"] = [{"filename": n, "content": base64.b64encode(b).decode()} for n, b in attachments]
    try:
        r = httpx.post("https://api.resend.com/emails", json=payload, timeout=20,
                       headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"})
    except httpx.HTTPError as e:
        log.error("Email to %s failed: %s", to, e)
        return False
    if r.status_code >= 300:
        log.error("Email to %s failed: %s %s", to, r.status_code, r.text[:300])
        return False
    return True
