"""
Push notifications via Ntfy (http://localhost:8002).

Import from any project:
    from systemOS.mcp.notify import notify, notify_done, notify_error

Usage:
    await notify("Vendor scrape for Carvansons complete", title="ResearchOS")
    await notify_done("30-min research job finished", topic="researchos")
    await notify_error("Vendor agent failed: timeout", topic="researchos")

Topics map to Ntfy channels — subscribe on phone/desktop via the Ntfy app.
Default topic: "systemos". Use project-specific topics (researchos, nnlos, etc.)
to filter notifications per app.

Ntfy priority levels: max, high, default, low, min
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

NTFY_URL = os.getenv("NTFY_URL", "http://localhost:8002")
NTFY_DEFAULT_TOPIC = os.getenv("NTFY_TOPIC", "systemos")


async def notify(
    message: str,
    title: str | None = None,
    topic: str | None = None,
    priority: str = "default",
    tags: list[str] | None = None,
) -> bool:
    """
    Send a push notification via Ntfy. Returns True on success.

    Args:
        message:  Notification body text
        title:    Notification title (optional)
        topic:    Ntfy topic/channel (default: NTFY_TOPIC env or "systemos")
        priority: max | high | default | low | min
        tags:     Emoji shortcodes shown on notification (e.g. ["white_check_mark"])
    """
    topic = topic or NTFY_DEFAULT_TOPIC
    headers = {"Priority": priority}
    if title:
        headers["Title"] = title
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{NTFY_URL}/{topic}",
                content=message.encode(),
                headers=headers,
            )
            if resp.status_code not in (200, 201):
                logger.warning("[NOTIFY] unexpected status %d for topic %s", resp.status_code, topic)
                return False
            return True
    except Exception as e:
        logger.warning("[NOTIFY] failed to send notification: %s", e)
        return False


async def notify_done(message: str, topic: str | None = None, title: str | None = None) -> bool:
    """Convenience: success notification with green tick tag."""
    return await notify(message, title=title, topic=topic, priority="default", tags=["white_check_mark"])


async def notify_error(message: str, topic: str | None = None, title: str | None = None) -> bool:
    """Convenience: error notification with high priority and warning tag."""
    return await notify(message, title=title or "Error", topic=topic, priority="high", tags=["warning"])


async def notify_start(message: str, topic: str | None = None, title: str | None = None) -> bool:
    """Convenience: job started notification with rocket tag."""
    return await notify(message, title=title, topic=topic, priority="low", tags=["rocket"])
