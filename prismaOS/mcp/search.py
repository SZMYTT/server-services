import os
import httpx
import logging

logger = logging.getLogger("prisma.mcp.search")

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

async def run_search(query: str, num_results: int = 5) -> list[dict]:
    """
    Search the web using SearXNG.
    Returns a list of dicts with keys: titles, url, content
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for res in data.get("results", [])[:num_results]:
                    results.append({
                        "title": res.get("title", ""),
                        "url": res.get("url", ""),
                        "content": res.get("content", "")
                    })
                return results
            else:
                logger.error(f"[SEARCH] SearXNG returned status {resp.status_code}")
                return []
    except Exception as e:
        logger.error(f"[SEARCH] SearXNG request failed: {e}")
        return []
