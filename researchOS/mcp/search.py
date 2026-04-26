"""SearXNG search — already running on the server at port 8080."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")


async def run_search(query: str, num_results: int = 5) -> list[dict]:
    """Search via SearXNG. Returns list of {title, url, content}."""
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
            )
            if resp.status_code != 200:
                logger.error("[SEARCH] SearXNG %s → %d", query[:50], resp.status_code)
                return []
            data = resp.json()
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in data.get("results", [])[:num_results]
            ]
    except Exception as exc:
        logger.error("[SEARCH] Failed: %s", exc)
        return []
