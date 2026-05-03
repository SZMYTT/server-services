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
from typing import Optional

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from playwright.async_api import async_playwright, Page, BrowserContext

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


class InteractiveBrowser:
    """
    Stateful, interactive browser session using Playwright.
    Used for Phase 3 Vision-Verified Browsing Loops and authenticated execution.
    """
    def __init__(self, headless: bool = True, user_data_dir: str = "/tmp/playwright_sessions"):
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.playwright = None
        self.browser_context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self):
        """Start the persistent browser session to maintain authentication state."""
        self.playwright = await async_playwright().start()
        # Using launch_persistent_context saves cookies and local storage between runs
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 800}
        )
        if not self.browser_context.pages:
            self.page = await self.browser_context.new_page()
        else:
            self.page = self.browser_context.pages[0]
        logger.info("[INTERACTIVE_BROWSER] Session started.")

    async def close(self):
        """Safely close the browser session."""
        if self.browser_context:
            await self.browser_context.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("[INTERACTIVE_BROWSER] Session closed.")

    async def navigate(self, url: str):
        if not self.page: raise RuntimeError("Browser not started.")
        await self.page.goto(url, wait_until="networkidle")

    async def click(self, selector: str):
        if not self.page: raise RuntimeError("Browser not started.")
        await self.page.click(selector)
        await asyncio.sleep(1)  # Brief wait to allow UI to react

    async def type_text(self, text: str, selector: str):
        if not self.page: raise RuntimeError("Browser not started.")
        await self.page.fill(selector, text)

    async def capture_screenshot(self, full_page: bool = False) -> bytes:
        """Capture a screenshot for the Vision model to analyze."""
        if not self.page: raise RuntimeError("Browser not started.")
        return await self.page.screenshot(full_page=full_page)
