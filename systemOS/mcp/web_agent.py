"""
Playwright-based interactive web agent module.
Exposes a BrowserSession class that can be driven by the LLM to navigate,
click, fill forms, and extract text from web pages.
"""

import logging
import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Page, BrowserContext

logger = logging.getLogger(__name__)

class BrowserSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
    
    async def start(self, headless: bool = True):
        logger.info("[WEB_AGENT] Starting Playwright browser session")
        self.playwright = await async_playwright().start()
        # Launch Chromium (can be configured to use Firefox or WebKit)
        self.browser = await self.playwright.chromium.launch(headless=headless)
        self.context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()
    
    async def stop(self):
        logger.info("[WEB_AGENT] Stopping Playwright browser session")
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
            
    async def navigate(self, url: str) -> str:
        """Navigate to a URL and wait for load. Returns page text."""
        logger.info(f"[WEB_AGENT] Navigating to {url}")
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(2000) # Let dynamic content settle
            return await self.get_page_text()
        except Exception as e:
            return f"Error navigating to {url}: {str(e)}"
            
    async def click(self, selector: str) -> str:
        """Click an element matching the selector."""
        logger.info(f"[WEB_AGENT] Clicking {selector}")
        try:
            await self.page.click(selector, timeout=5000)
            await self.page.wait_for_timeout(1000) # Wait for potential navigation or modal
            return f"Clicked {selector} successfully."
        except Exception as e:
            return f"Error clicking {selector}: {str(e)}"
            
    async def fill(self, selector: str, value: str) -> str:
        """Fill an input field matching the selector with value."""
        logger.info(f"[WEB_AGENT] Filling {selector}")
        try:
            await self.page.fill(selector, value, timeout=5000)
            return f"Filled {selector} successfully."
        except Exception as e:
            return f"Error filling {selector}: {str(e)}"
            
    async def get_page_text(self) -> str:
        """Extract visible text from the page."""
        try:
            # Simple text extraction, could be improved with readability-lxml
            body_text = await self.page.evaluate("document.body.innerText")
            # Truncate to avoid blowing up context window
            return body_text[:15000]
        except Exception as e:
            return f"Error extracting text: {str(e)}"
            
    async def screenshot(self, path: str) -> str:
        """Take a screenshot of the current page."""
        try:
            await self.page.screenshot(path=path, full_page=True)
            return f"Screenshot saved to {path}"
        except Exception as e:
            return f"Error taking screenshot: {str(e)}"
            
    async def execute_action(self, action: str, **kwargs) -> str:
        """Dispatch an action based on tool call."""
        if action == "navigate":
            return await self.navigate(kwargs.get("url", ""))
        elif action == "click":
            return await self.click(kwargs.get("selector", ""))
        elif action == "fill":
            return await self.fill(kwargs.get("selector", ""), kwargs.get("value", ""))
        elif action == "get_page_text":
            return await self.get_page_text()
        elif action == "screenshot":
            return await self.screenshot(kwargs.get("path", "screenshot.png"))
        else:
            return f"Unknown action: {action}"
