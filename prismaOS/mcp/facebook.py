import os
import logging
import httpx

logger = logging.getLogger("prisma.mcp.facebook")

GRAPH_BASE = "https://graph.facebook.com/v19.0"
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "")


def _fb_ok() -> bool:
    if not all([FACEBOOK_PAGE_TOKEN, FACEBOOK_PAGE_ID]):
        logger.warning("[FACEBOOK] FACEBOOK_PAGE_TOKEN / FACEBOOK_PAGE_ID not set — skipping")
        return False
    return True


def _ig_ok() -> bool:
    if not all([FACEBOOK_PAGE_TOKEN, INSTAGRAM_ACCOUNT_ID]):
        logger.warning("[INSTAGRAM] INSTAGRAM_ACCOUNT_ID not set — skipping")
        return False
    return True


async def fetch_page_mentions(workspace: str = "") -> list[dict]:
    """Fetch posts that tag the Facebook page."""
    if not _fb_ok():
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{FACEBOOK_PAGE_ID}/tagged",
                params={"access_token": FACEBOOK_PAGE_TOKEN, "fields": "id,from,message,created_time"},
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {
                    "post_id": p.get("id"),
                    "author": p.get("from", {}).get("name", ""),
                    "content": p.get("message", ""),
                    "timestamp": p.get("created_time", ""),
                }
                for p in data.get("data", [])
            ]
    except httpx.HTTPStatusError as exc:
        logger.error("[FACEBOOK] fetch_page_mentions HTTP %s: %s", exc.response.status_code, exc.response.text)
        return []
    except Exception as exc:
        logger.error("[FACEBOOK] fetch_page_mentions error: %s", exc)
        return []


async def publish_post(workspace: str = "", message: str = "", link: str = "") -> bool:
    """Publish a text post (+ optional link) to the Facebook page."""
    if not _fb_ok():
        return False
    payload: dict = {"message": message, "access_token": FACEBOOK_PAGE_TOKEN}
    if link:
        payload["link"] = link
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{FACEBOOK_PAGE_ID}/feed",
                data=payload,
            )
            resp.raise_for_status()
            post_id = resp.json().get("id")
            logger.info("[FACEBOOK] Published post %s for workspace=%s", post_id, workspace)
            return True
    except httpx.HTTPStatusError as exc:
        logger.error("[FACEBOOK] publish_post HTTP %s: %s", exc.response.status_code, exc.response.text)
        return False
    except Exception as exc:
        logger.error("[FACEBOOK] publish_post error: %s", exc)
        return False


async def fetch_post_insights(post_id: str, metrics: list[str] | None = None) -> dict:
    """Fetch engagement insights for a Facebook post."""
    if not _fb_ok():
        return {}
    metrics = metrics or ["post_impressions", "post_engaged_users", "post_reactions_by_type_total"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": ",".join(metrics),
                    "access_token": FACEBOOK_PAGE_TOKEN,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {item["name"]: item.get("values", [{}])[-1].get("value") for item in data.get("data", [])}
    except httpx.HTTPStatusError as exc:
        logger.error("[FACEBOOK] fetch_post_insights HTTP %s", exc.response.status_code)
        return {}
    except Exception as exc:
        logger.error("[FACEBOOK] fetch_post_insights error: %s", exc)
        return {}


async def publish_to_instagram(image_url: str, caption: str) -> str | None:
    """Publish an image post to Instagram via the two-step container → publish flow.

    Returns the Instagram media ID on success, or None on failure.
    """
    if not _ig_ok():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: create media container
            create_resp = await client.post(
                f"{GRAPH_BASE}/{INSTAGRAM_ACCOUNT_ID}/media",
                data={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": FACEBOOK_PAGE_TOKEN,
                },
            )
            create_resp.raise_for_status()
            container_id = create_resp.json().get("id")
            if not container_id:
                logger.error("[INSTAGRAM] No container_id returned")
                return None

            # Step 2: publish the container
            pub_resp = await client.post(
                f"{GRAPH_BASE}/{INSTAGRAM_ACCOUNT_ID}/media_publish",
                data={
                    "creation_id": container_id,
                    "access_token": FACEBOOK_PAGE_TOKEN,
                },
            )
            pub_resp.raise_for_status()
            media_id = pub_resp.json().get("id")
            logger.info("[INSTAGRAM] Published media %s", media_id)
            return media_id
    except httpx.HTTPStatusError as exc:
        logger.error("[INSTAGRAM] publish HTTP %s: %s", exc.response.status_code, exc.response.text)
        return None
    except Exception as exc:
        logger.error("[INSTAGRAM] publish error: %s", exc)
        return None


async def fetch_instagram_insights(media_id: str) -> dict:
    """Fetch insights (reach, likes, comments, shares, saves) for an Instagram post."""
    if not _ig_ok():
        return {}
    metrics = ["impressions", "reach", "likes", "comments", "shares", "saved"]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{media_id}/insights",
                params={
                    "metric": ",".join(metrics),
                    "access_token": FACEBOOK_PAGE_TOKEN,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {item["name"]: item.get("values", [{}])[-1].get("value", 0) for item in data.get("data", [])}
    except httpx.HTTPStatusError as exc:
        logger.error("[INSTAGRAM] fetch_insights HTTP %s", exc.response.status_code)
        return {}
    except Exception as exc:
        logger.error("[INSTAGRAM] fetch_insights error: %s", exc)
        return {}
