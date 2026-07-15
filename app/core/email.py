"""
Shared transactional email sender (SendGrid via httpx).

The platform already sends email through SendGrid's HTTP API in several places
(TEFCA QA alerts in app/Tefca/qa_engine.py, bulletin briefings/alerts in
app/bulletin_intelligence/*) using httpx directly — the `sendgrid` library is
intentionally NOT a dependency. This module centralises that proven pattern so
transactional flows (user invitations, password resets) send the same way instead
of each re-implementing the SendGrid call.

FAIL-SAFE / DRY-RUN: with no SENDGRID_API_KEY set, nothing is sent — the call logs
the intended message and returns {"sent": False, "reason": "no_sendgrid_key"} so the
caller's request still succeeds. This function NEVER raises; it always returns a
status dict. Callers treat email as best-effort and must not let a delivery failure
break the primary operation (creating the user / issuing the reset token).

Configuration (environment):
  SENDGRID_API_KEY   SendGrid API key. Unset => dry-run (log only, nothing sent).
  MAIL_FROM          Sender address (default imran@agtbi.com). MUST be a
                     SendGrid-verified Single Sender / domain, or SendGrid returns
                     403 (agtbi.com sends have 403'd before — verify the sender).
  MAIL_FROM_NAME     Sender display name (default "DocuAction").
"""
import os
import logging
from typing import Any, Dict, Iterable, Optional, Union

logger = logging.getLogger("docuaction.email")

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


def _sender() -> tuple:
    return (
        os.getenv("MAIL_FROM", "imran@agtbi.com"),
        os.getenv("MAIL_FROM_NAME", "DocuAction"),
    )


async def send_email(
    to: Union[str, Iterable[str]],
    subject: str,
    *,
    text: Optional[str] = None,
    html: Optional[str] = None,
) -> Dict[str, Any]:
    """Send a transactional email via SendGrid. Best-effort; never raises.

    Returns a status dict, e.g. {"sent": True, "recipients": [...]} or
    {"sent": False, "reason": "no_sendgrid_key", "recipients": [...]}.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [e.strip() for e in recipients if e and e.strip()]

    key = os.getenv("SENDGRID_API_KEY", "")
    if not key:
        logger.warning(
            "[EMAIL — no SENDGRID_API_KEY, logged only] to=%s subject=%r",
            recipients, subject,
        )
        return {"sent": False, "reason": "no_sendgrid_key", "recipients": recipients}
    if not recipients:
        return {"sent": False, "reason": "no_recipients"}
    if not text and not html:
        return {"sent": False, "reason": "no_body", "recipients": recipients}

    from_email, from_name = _sender()
    # SendGrid requires content in increasing order of preference: text/plain first.
    content = []
    if text:
        content.append({"type": "text/plain", "value": text})
    if html:
        content.append({"type": "text/html", "value": html})

    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.post(
                SENDGRID_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": e} for e in recipients]}],
                    "from": {"email": from_email, "name": from_name},
                    "subject": subject,
                    "content": content,
                },
            )
            resp.raise_for_status()
        logger.info("Email sent: %r -> %s", subject, recipients)
        return {"sent": True, "recipients": recipients}
    except Exception as e:  # noqa: BLE001 — best-effort, never propagate
        logger.error("Email send failed (%r -> %s): %s", subject, recipients, e)
        return {"sent": False, "reason": str(e)[:160], "recipients": recipients}
