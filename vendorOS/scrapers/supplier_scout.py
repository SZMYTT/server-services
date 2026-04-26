"""
vendorOS Phase 5 — Supplier Discovery Scraper

Searches for new suppliers per category, scrapes their sites,
and uses Claude to extract structured supplier intelligence.

Usage:
    python3 scrapers/supplier_scout.py --category "fragrance oil" --limit 15
    python3 scrapers/supplier_scout.py --category "glass jar" --limit 15
    python3 scrapers/supplier_scout.py --list-categories
"""

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

logging.basicConfig(level=logging.INFO, format="[SCOUT] %(message)s")
logger = logging.getLogger("vendoros.scout")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
OUTPUT_DIR = Path(os.getenv("SCRAPER_OUTPUT_DIR", "reports"))
MAX_RESULTS = int(os.getenv("SCRAPER_MAX_RESULTS_PER_QUERY", 10))
QUERIES_PER_CATEGORY = int(os.getenv("SCRAPER_QUERIES_PER_CATEGORY", 5))

CATEGORIES = [
    "fragrance oil",
    "candle wax",
    "candle wick",
    "glass jar candle vessel",
    "glass bottle fragrance",
    "pump dispenser closure",
    "lid cap closure packaging",
    "label printing UK",
    "gift box cartonage packaging",
    "reed diffuser reed sticks",
    "wax melt clamshell packaging",
    "home fragrance ingredient",
]


def list_categories():
    print("Available categories:")
    for cat in CATEGORIES:
        print(f"  - {cat}")


async def generate_search_queries(client: anthropic.Anthropic, category: str, n: int = 5) -> list[str]:
    prompt = f"""Generate {n} search queries to find UK wholesale suppliers of: {category}

The queries should be diverse — mix of:
- Direct product searches ("buy {category} wholesale UK")
- B2B supplier directory searches
- Manufacturer searches
- Trade supplier searches

Output ONLY a JSON array of query strings, nothing else.
Example: ["fragrance oil supplier UK wholesale", "buy fragrance oils bulk B2B UK", ...]"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    text = message.content[0].text.strip()
    try:
        queries = json.loads(text)
        return queries[:n]
    except json.JSONDecodeError:
        lines = [l.strip().strip('"').strip("'") for l in text.split("\n") if l.strip() and l.strip()[0] in '"\'']
        return lines[:n]


def search_searxng(query: str, max_results: int = MAX_RESULTS) -> list[str]:
    try:
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "categories": "general"},
            timeout=10
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [r["url"] for r in results[:max_results] if "url" in r]
    except Exception as e:
        logger.warning(f"SearXNG search failed for '{query}': {e}")
        return []


async def scrape_page(url: str) -> Optional[str]:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (compatible; research bot)"})
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            content = await page.evaluate("document.body.innerText")
            await browser.close()
            # Trim to ~4000 chars to keep Claude context manageable
            return content[:4000] if content else None
    except Exception as e:
        logger.warning(f"Scrape failed for {url}: {e}")
        return None


def extract_supplier_intelligence(client: anthropic.Anthropic, url: str, content: str, category: str) -> dict:
    prompt = f"""You are analysing a webpage to extract supplier intelligence for a UK candle/fragrance brand (NNL) looking for {category} suppliers.

URL: {url}
Page content:
{content}

Extract what you can and return a JSON object with these fields (use null for unknown):
{{
  "company_name": "string or null",
  "url": "{url}",
  "is_wholesaler": true/false/null,
  "is_dropshipper": true/false/null,
  "appears_to_be_manufacturer": true/false/null,
  "uk_based": true/false/null,
  "products": ["list of relevant products, max 5"],
  "min_order_value": "string or null (e.g. '£50', '£500')",
  "min_order_qty": "string or null (e.g. '1kg', '100 units')",
  "lead_time": "string or null (e.g. '3-5 days', '2 weeks')",
  "price_tiers": [{{ "qty": "X", "price": "Y" }}],
  "delivery_info": "string or null",
  "contact_email": "string or null",
  "potential_upstream_supplier": "string or null — who might supply THEM",
  "comparable_alternatives": ["any competitor companies mentioned"],
  "confidence": "high/medium/low",
  "notes": "any other relevant observations for a procurement manager"
}}

Dropshipper signals: no mention of stock, 'fulfilled by', reseller language, generic product images with no brand.
Output ONLY the JSON object, no other text."""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = message.content[0].text.strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"url": url, "confidence": "low", "notes": "Could not parse response"}
    except Exception as e:
        logger.warning(f"Claude extraction failed for {url}: {e}")
        return {"url": url, "confidence": "low", "notes": str(e)}


async def scout_category(category: str, limit: int = 15) -> list[dict]:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    logger.info(f"Generating search queries for: {category}")
    queries = await generate_search_queries(client, category, QUERIES_PER_CATEGORY)
    logger.info(f"Queries: {queries}")

    all_urls: set[str] = set()
    for query in queries:
        urls = search_searxng(query)
        all_urls.update(urls)
        if len(all_urls) >= limit * 2:
            break

    # Filter out obvious non-suppliers
    skip_domains = {"wikipedia.org", "reddit.com", "amazon.com", "amazon.co.uk", "ebay.co.uk", "ebay.com"}
    urls_to_scrape = [
        u for u in list(all_urls)
        if not any(d in u for d in skip_domains)
    ][:limit]

    logger.info(f"Scraping {len(urls_to_scrape)} URLs...")

    results = []
    for i, url in enumerate(urls_to_scrape):
        logger.info(f"  [{i+1}/{len(urls_to_scrape)}] {url}")
        content = await scrape_page(url)
        if not content:
            continue
        intel = extract_supplier_intelligence(client, url, content, category)
        intel["url"] = url
        results.append(intel)

    return results


def save_report(category: str, results: list[dict]) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_cat = re.sub(r'[^a-z0-9]+', '_', category.lower())
    path = OUTPUT_DIR / f"scout_{safe_cat}_{date_str}.md"

    high_conf = [r for r in results if r.get("confidence") == "high"]
    med_conf = [r for r in results if r.get("confidence") == "medium"]
    uk = [r for r in results if r.get("uk_based")]
    non_drop = [r for r in results if r.get("is_dropshipper") is False]

    lines = [
        f"# Supplier Scout Report: {category.title()}",
        f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"URLs analysed: {len(results)} | High confidence: {len(high_conf)} | UK-based: {len(uk)}",
        "",
        "---",
        "",
        "## Top Prospects",
        "",
    ]

    top = sorted(
        [r for r in results if r.get("confidence") in ("high", "medium") and r.get("is_dropshipper") is not True],
        key=lambda x: (x.get("uk_based", False), x.get("confidence") == "high"),
        reverse=True
    )

    for r in top[:10]:
        name = r.get("company_name") or r.get("url", "Unknown")
        lines.append(f"### {name}")
        lines.append(f"- **URL:** {r.get('url', '')}")
        lines.append(f"- **UK Based:** {'Yes' if r.get('uk_based') else 'No/Unknown'}")
        lines.append(f"- **Type:** {'Manufacturer' if r.get('appears_to_be_manufacturer') else 'Wholesaler' if r.get('is_wholesaler') else 'Unknown'}")
        if r.get("min_order_value"): lines.append(f"- **Min Order (value):** {r['min_order_value']}")
        if r.get("min_order_qty"): lines.append(f"- **Min Order (qty):** {r['min_order_qty']}")
        if r.get("lead_time"): lines.append(f"- **Lead Time:** {r['lead_time']}")
        if r.get("price_tiers"): lines.append(f"- **Price Tiers:** {r['price_tiers']}")
        if r.get("contact_email"): lines.append(f"- **Email:** {r['contact_email']}")
        if r.get("potential_upstream_supplier"): lines.append(f"- **Upstream Supplier:** {r['potential_upstream_supplier']}")
        if r.get("notes"): lines.append(f"- **Notes:** {r['notes']}")
        lines.append("")

    lines += [
        "---",
        "",
        "## All Results (raw)",
        "",
        "```json",
        json.dumps(results, indent=2),
        "```",
    ]

    path.write_text("\n".join(lines))
    return path


async def main():
    parser = argparse.ArgumentParser(description="vendorOS Supplier Scout")
    parser.add_argument("--category", type=str, help="Product category to search for")
    parser.add_argument("--limit", type=int, default=15, help="Max URLs to scrape (default: 15)")
    parser.add_argument("--list-categories", action="store_true", help="List available categories")
    args = parser.parse_args()

    if args.list_categories:
        list_categories()
        return

    if not args.category:
        parser.print_help()
        return

    results = await scout_category(args.category, args.limit)
    path = save_report(args.category, results)

    logger.info(f"Report saved: {path}")
    logger.info(f"Found {len(results)} suppliers. High confidence: {len([r for r in results if r.get('confidence') == 'high'])}")


if __name__ == "__main__":
    asyncio.run(main())
