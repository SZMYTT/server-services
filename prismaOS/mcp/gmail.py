import os
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("prisma.mcp.gmail")

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "credentials/google_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def _build_service():
    """Build and return an authenticated Gmail API service, or None if not configured."""
    if not CREDENTIALS_PATH or not os.path.exists(CREDENTIALS_PATH):
        logger.warning("[GMAIL] GOOGLE_CREDENTIALS_PATH not set or file missing — skipping")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(TOKEN_PATH) or ".", exist_ok=True)
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())

        return build("gmail", "v1", credentials=creds)
    except Exception as exc:
        logger.error("[GMAIL] Failed to build service: %s", exc)
        return None


async def fetch_unread_emails(workspace: str = "", max_results: int = 20) -> list[dict]:
    """Fetch unread emails from Gmail inbox."""
    import asyncio

    def _fetch():
        service = _build_service()
        if not service:
            return []
        try:
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=max_results,
            ).execute()
            messages = results.get("messages", [])
            out = []
            for m in messages:
                msg = service.users().messages().get(
                    userId="me",
                    id=m["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                snippet = msg.get("snippet", "")
                out.append({
                    "email_id": m["id"],
                    "thread_id": msg.get("threadId"),
                    "sender": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "body": snippet,
                    "received_at": headers.get("Date", ""),
                })
            return out
        except Exception as exc:
            logger.error("[GMAIL] fetch_unread_emails error: %s", exc)
            return []

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def send_email(to: str, subject: str, body: str, html: bool = False) -> bool:
    """Send an email via Gmail API."""
    import asyncio

    def _send():
        service = _build_service()
        if not service:
            return False
        try:
            if html:
                msg = MIMEMultipart("alternative")
                msg["To"] = to
                msg["Subject"] = subject
                msg.attach(MIMEText(body, "html"))
            else:
                msg = MIMEText(body, "plain")
                msg["To"] = to
                msg["Subject"] = subject

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info("[GMAIL] Sent email to %s — %s", to, subject)
            return True
        except Exception as exc:
            logger.error("[GMAIL] send_email error: %s", exc)
            return False

    return await asyncio.get_event_loop().run_in_executor(None, _send)


async def send_booking_confirmation(
    client_email: str,
    client_name: str,
    service_name: str,
    appointment_dt: str,
    price: float = 0,
    notes: str = "",
) -> bool:
    """Send a nursing appointment confirmation email."""
    subject = f"Appointment Confirmed — {service_name}"
    price_line = f"<p><strong>Price:</strong> £{price:.2f}</p>" if price else ""
    notes_line = f"<p><strong>Notes:</strong> {notes}</p>" if notes else ""
    body = f"""
<html><body style="font-family:sans-serif;color:#222;max-width:500px;margin:auto">
  <h2 style="color:#1F3B2D">Appointment Confirmed</h2>
  <p>Hi {client_name},</p>
  <p>Your appointment has been booked. Here are the details:</p>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:6px 0"><strong>Service:</strong></td><td>{service_name}</td></tr>
    <tr><td style="padding:6px 0"><strong>Date &amp; Time:</strong></td><td>{appointment_dt}</td></tr>
    {price_line}
  </table>
  {notes_line}
  <p>If you need to reschedule or have any questions, please get in touch.</p>
  <p style="color:#888;font-size:12px">Asta Nursing &amp; Massage</p>
</body></html>
"""
    return await send_email(client_email, subject, body, html=True)


async def mark_as_read(email_id: str) -> bool:
    """Remove UNREAD label from a Gmail message."""
    import asyncio

    def _mark():
        service = _build_service()
        if not service:
            return False
        try:
            service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except Exception as exc:
            logger.error("[GMAIL] mark_as_read error: %s", exc)
            return False

    return await asyncio.get_event_loop().run_in_executor(None, _mark)
