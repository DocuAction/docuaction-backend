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

Configuration (environment) — Railway variable names are the source of truth;
legacy names are still read as fallbacks for backward compatibility:
  SENDGRID_API_KEY   SendGrid API key. Unset => dry-run (log only, nothing sent).
  EMAIL_FROM         Sender address (fallback MAIL_FROM; default admin@docuaction.io).
                     MUST be a SendGrid-verified Single Sender / domain or SendGrid
                     returns 403.
  EMAIL_FROM_NAME    Sender display name (fallback MAIL_FROM_NAME; default
                     "DocuAction Security").
  APP_URL            Base URL for links in emails (fallback APP_BASE_URL; default
                     https://app.docuaction.io).
"""
import os
import logging
from typing import Any, Dict, Iterable, Optional, Union

logger = logging.getLogger("docuaction.email")

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


def _sender() -> tuple:
    # Railway config is the source of truth (EMAIL_FROM / EMAIL_FROM_NAME); the
    # legacy MAIL_FROM* names remain as fallbacks so nothing breaks mid-migration.
    return (
        os.getenv("EMAIL_FROM") or os.getenv("MAIL_FROM") or "admin@docuaction.io",
        os.getenv("EMAIL_FROM_NAME") or os.getenv("MAIL_FROM_NAME") or "DocuAction Security",
    )


def app_url() -> str:
    """Base URL for links embedded in emails (no trailing slash)."""
    return (os.getenv("APP_URL") or os.getenv("APP_BASE_URL") or "https://app.docuaction.io").rstrip("/")


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
        # Log the recipient + subject only — NEVER the API key (it lives in the
        # Authorization header and is never logged).
        logger.info("Email sent to %s (subject=%r)", recipients, subject)
        return {"sent": True, "recipients": recipients}
    except Exception as e:  # noqa: BLE001 — best-effort, never propagate
        # `e` (httpx errors) contains URL + status, not the request headers, so the
        # API key is not exposed here either.
        logger.error("Email send failed (subject=%r -> %s): %s", subject, recipients, e)
        return {"sent": False, "reason": str(e)[:160], "recipients": recipients}


# ─── Transactional templates ────────────────────────────────────────────────
# Each builds subject + text + html and delegates to send_email(). Bodies contain
# NO passwords and NO raw tokens — only secure links (P7 / security requirements).

async def send_invitation_email(to: str, full_name: str, set_password_url: str) -> Dict[str, Any]:
    """User invitation: welcome + login URL + a secure set-password link."""
    greeting = f" {full_name}" if (full_name or "").strip() else ""
    login = f"{app_url()}/login"
    text = (
        f"Hello{greeting},\n\n"
        f"You've been invited to DocuAction.\n\n"
        f"Set your password to activate your account (link expires in 72 hours):\n"
        f"{set_password_url}\n\n"
        f"After setting your password, sign in at: {login}\n\n"
        f"If you weren't expecting this invitation, you can ignore this email.\n\n"
        f"— DocuAction Security"
    )
    html = (
        f"<p>Hello{greeting},</p>"
        f"<p>You've been invited to <strong>DocuAction</strong>.</p>"
        f"<p><a href=\"{set_password_url}\">Set your password</a> to activate your "
        f"account (link expires in 72 hours).</p>"
        f"<p>After setting your password, <a href=\"{login}\">sign in here</a>.</p>"
        f"<p>If you weren't expecting this invitation, you can ignore this email.</p>"
        f"<p>— DocuAction Security</p>"
    )
    return await send_email(to, "DocuAction — You've been invited", text=text, html=html)


async def send_password_reset_email(to: str, reset_url: str) -> Dict[str, Any]:
    """Password reset: secure reset link + expiry + security notice."""
    text = (
        "We received a request to reset your DocuAction password.\n\n"
        f"Reset your password (link expires in 1 hour, single use):\n{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email — your "
        "password will not change.\n\n"
        "— DocuAction Security"
    )
    html = (
        "<p>We received a request to reset your DocuAction password.</p>"
        f"<p><a href=\"{reset_url}\">Reset your password</a> "
        "(link expires in 1 hour, single use).</p>"
        "<p>If you did not request this, you can safely ignore this email — your "
        "password will not change.</p><p>— DocuAction Security</p>"
    )
    return await send_email(to, "DocuAction — Password Reset Request", text=text, html=html)


async def send_password_changed_email(to: str) -> Dict[str, Any]:
    """Confirmation that a password was changed, with a 'not you?' warning."""
    login = f"{app_url()}/login"
    text = (
        "Your DocuAction password was just changed.\n\n"
        f"You can sign in with your new password at: {login}\n\n"
        "If you did NOT make this change, contact your administrator immediately — "
        "your account may be compromised.\n\n"
        "— DocuAction Security"
    )
    html = (
        "<p>Your DocuAction password was just changed.</p>"
        f"<p>You can <a href=\"{login}\">sign in</a> with your new password.</p>"
        "<p><strong>If you did NOT make this change</strong>, contact your "
        "administrator immediately — your account may be compromised.</p>"
        "<p>— DocuAction Security</p>"
    )
    return await send_email(to, "DocuAction — Password Changed", text=text, html=html)
