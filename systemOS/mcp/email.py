"""
Email MCP — send transactional email via Resend API.

Import from any project:
    from systemOS.mcp.email import send_email, send_template

Setup:
    Set RESEND_API_KEY in your project's .env
    Sign up free at https://resend.com — 3,000 emails/month free

Usage:
    # Simple text/HTML email
    ok = await send_email(
        to="customer@example.com",
        subject="Your order is confirmed!",
        html="<h1>Thanks!</h1><p>Your order #1234 is confirmed.</p>",
        from_name="Bakery Name",
    )

    # From a dict template (fills {{placeholders}})
    ok = await send_template(
        to="customer@example.com",
        subject="Order #{{order_id}} Confirmed",
        template_path="templates/email/order_confirm.html",
        data={"order_id": "1234", "items": "2x Sourdough, 1x Croissant"},
    )

    # Quick plain text alert to yourself
    await alert("Low stock: bread flour below 2kg")
"""

import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY   = os.getenv("RESEND_API_KEY", "")
DEFAULT_FROM     = os.getenv("EMAIL_FROM",     "noreply@yourdomain.com")
DEFAULT_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "SystemOS")
ALERT_TO         = os.getenv("EMAIL_ALERT_TO", "")   # your own email for internal alerts


async def send_email(
    to: str | list[str],
    subject: str,
    html: str,
    text: str | None = None,
    from_email: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
) -> bool:
    """
    Send an email via Resend. Returns True on success.

    Args:
        to:          Recipient address or list of addresses
        subject:     Email subject line
        html:        HTML body (required — Resend needs at least one of html/text)
        text:        Plain text fallback (auto-stripped from html if not provided)
        from_email:  Sender address (default: EMAIL_FROM env)
        from_name:   Sender display name (default: EMAIL_FROM_NAME env)
        reply_to:    Reply-to address
    """
    if not RESEND_API_KEY:
        logger.error("[EMAIL] RESEND_API_KEY not set — cannot send email")
        return False

    sender = f"{from_name or DEFAULT_FROM_NAME} <{from_email or DEFAULT_FROM}>"
    recipients = [to] if isinstance(to, str) else to

    if not text:
        # Strip HTML tags for plain-text fallback
        text = re.sub(r"<[^>]+>", "", html).strip()

    payload: dict = {
        "from":    sender,
        "to":      recipients,
        "subject": subject,
        "html":    html,
        "text":    text,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info("[EMAIL] sent to %s — id=%s", recipients, data.get("id"))
                return True
            else:
                logger.error("[EMAIL] Resend error %d: %s", resp.status_code, resp.text[:300])
                return False
    except Exception as e:
        logger.error("[EMAIL] exception sending email: %s", e)
        return False


async def send_template(
    to: str | list[str],
    subject: str,
    template_path: str | Path,
    data: dict,
    from_email: str | None = None,
    from_name: str | None = None,
) -> bool:
    """
    Send an email using an HTML template file with {{placeholder}} substitution.

    Args:
        template_path: Path to the .html template file
        data:          Dict of placeholder values — {{key}} → value
    """
    path = Path(template_path)
    if not path.exists():
        logger.error("[EMAIL] Template not found: %s", path)
        return False

    html = path.read_text(encoding="utf-8")

    # Replace {{key}} placeholders
    for key, value in data.items():
        html = html.replace("{{" + key + "}}", str(value))
        subject = subject.replace("{{" + key + "}}", str(value))

    return await send_email(
        to=to,
        subject=subject,
        html=html,
        from_email=from_email,
        from_name=from_name,
    )


async def alert(
    message: str,
    subject: str = "SystemOS Alert",
    to: str | None = None,
) -> bool:
    """
    Send a quick plain-text alert email to the operator (yourself).
    Reads EMAIL_ALERT_TO from env. Returns True on success.
    """
    recipient = to or ALERT_TO
    if not recipient:
        logger.warning("[EMAIL] No alert recipient set (EMAIL_ALERT_TO env)")
        return False

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
      <h2 style="color:#162920;">{subject}</h2>
      <p style="color:#3A5244;white-space:pre-wrap;">{message}</p>
      <hr style="border:none;border-top:1px solid #E2D9C4;margin:24px 0;">
      <p style="color:#6B8578;font-size:12px;">Sent by SystemOS</p>
    </div>
    """
    return await send_email(to=recipient, subject=subject, html=html)
