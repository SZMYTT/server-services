"""FCM push notifications + meal nudge logic."""

import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

_FCM_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


def is_configured() -> bool:
    return bool(
        os.getenv("FCM_CREDENTIALS_FILE")
        and os.getenv("FCM_DEVICE_TOKEN")
    )


def _get_project_id() -> str | None:
    creds_file = os.getenv("FCM_CREDENTIALS_FILE", "")
    if not creds_file:
        return None
    try:
        import json
        with open(creds_file) as f:
            return json.load(f).get("project_id")
    except Exception:
        return None


async def _get_access_token() -> str | None:
    """Get a short-lived Google OAuth2 token from the service account."""
    creds_file = os.getenv("FCM_CREDENTIALS_FILE", "")
    if not creds_file:
        return None
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        req = google.auth.transport.requests.Request()
        creds.refresh(req)
        return creds.token
    except Exception as exc:
        logger.error("[FCM] Failed to get access token: %s", exc)
        return None


async def send_fcm(title: str, body: str, device_token: str | None = None) -> bool:
    """
    Send a push notification via FCM v1 HTTP API.
    Returns True on success, False otherwise.
    Silently skips if FCM is not configured.
    """
    if not is_configured():
        logger.info("[FCM] Not configured — skipping notification: %s", title)
        return False

    token = device_token or os.getenv("FCM_DEVICE_TOKEN", "")
    project_id = _get_project_id()
    access_token = await _get_access_token()

    if not (token and project_id and access_token):
        return False

    url = _FCM_SEND_URL.format(project_id=project_id)
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code == 200:
            logger.info("[FCM] Sent: %s", title)
            return True
        else:
            logger.warning("[FCM] Send failed %d: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.error("[FCM] Request error: %s", exc)
        return False


async def check_meal_nudge() -> None:
    """
    APScheduler job: runs at 13:05 daily.
    If no meal logged today, send an FCM nudge (spec 3.2).
    """
    if not is_configured():
        return

    from db import get_conn

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM health.meal_logs
                WHERE user_id = 1 AND consumed_at >= %s
            """, (day_start,))
            count = cur.fetchone()[0]

    if count == 0:
        logger.info("[NUDGE] No meals logged today — sending nudge")
        await send_fcm(
            title="Log your lunch",
            body="No meal logged yet today. Don't forget to track your nutrition.",
        )
    else:
        logger.debug("[NUDGE] %d meal(s) logged today — no nudge needed", count)
