"""
Page scraper — uses Crawl4AI (Playwright-backed) for all scraping.

Shared module. Import from any project:
    from systemOS.mcp.browser import scrape, scrape_many

Replaces the old httpx + HTMLParser approach. Benefits:
- Full JavaScript execution (JS-rendered content captured)
- Clean structured markdown output (not raw noisy text)
- Handles redirects, lazy-load, and dynamic pages

Requires crawl4ai in the project's venv:
    pip install crawl4ai && playwright install chromium
"""

import asyncio
import logging

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

logger = logging.getLogger(__name__)


async def scrape(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return clean markdown content."""
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20000)
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url, config=config)
            if not result.success:
                logger.warning("[BROWSER] Failed: %s", url)
                return ""
            return (result.markdown or "")[:max_chars]
    except Exception as exc:
        logger.warning("[BROWSER] Error scraping %s: %s", url, exc)
        return ""


async def scrape_many(urls: list[str], max_chars: int = 6000) -> list[str]:
    """Scrape multiple URLs in parallel under one browser instance."""
    if not urls:
        return []
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20000)
        async with AsyncWebCrawler() as crawler:
            tasks = [crawler.arun(url, config=config) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            output = []
            for url, result in zip(urls, results):
                if isinstance(result, Exception):
                    logger.warning("[BROWSER] Error scraping %s: %s", url, result)
                    output.append("")
                elif result.success and result.markdown:
                    output.append(result.markdown[:max_chars])
                else:
                    output.append("")
            return output
    except Exception as exc:
        logger.warning("[BROWSER] Batch scrape error: %s", exc)
        return [""] * len(urls)
