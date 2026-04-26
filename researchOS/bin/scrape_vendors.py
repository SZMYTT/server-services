#!/usr/bin/env python3
"""
researchOS Vendor Intelligence Scraper — runs on Mac

Scrapes supplier websites to extract structured intelligence:
company type, pricing tiers, MOQs, lead times, wholesale availability,
potential upstream suppliers, comparable alternatives.

Requires on Mac:
    pip install playwright anthropic requests
    playwright install chromium

Usage:
    # Scrape one vendor directly
    python3 bin/scrape_vendors.py --url https://supplier.co.uk --name "Supplier Name" --skus SKU001,SKU002

    # Scrape from a JSON file (batch mode)
    python3 bin/scrape_vendors.py --file vendors.json

    # Poll server for pending jobs (runs continuously)
    python3 bin/scrape_vendors.py --poll --server http://100.119.217.120:4001

vendors.json format:
    [
      {"url": "https://supplier.co.uk", "name": "Supplier Name", "skus": ["SKU001"], "category": "packaging-glass"},
      ...
    ]

Environment variables (in .env or exported):
    ANTHROPIC_API_KEY   — required
    SUPPLY_SERVER_URL   — researchOS server URL for poll mode (e.g. http://100.119.217.120:4001)
    SUPPLY_API_KEY      — API key for server (set in researchOS .env as VENDOR_API_KEY)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import anthropic
import requests
from playwright.async_api import async_playwright, Page

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCOUT] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vendor_scout")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
SUPPLY_SERVER_URL = os.environ.get("SUPPLY_SERVER_URL", "http://100.119.217.120:4001")
SUPPLY_API_KEY = os.environ.get("SUPPLY_API_KEY", "")

# Pages to look for on every vendor site
TARGET_PAGE_PATTERNS = [
    r"/about", r"/about-us", r"/our-story", r"/company",
    r"/delivery", r"/shipping", r"/dispatch",
    r"/trade", r"/wholesale", r"/b2b", r"/trade-account", r"/business",
    r"/contact", r"/contact-us",
    r"/faq", r"/faqs",
]

# Signals that strongly suggest a dropshipper
DROPSHIPPER_SIGNALS = [
    "fulfilled by", "we don't hold stock", "dropship", "direct from manufacturer",
    "supplier ships", "no minimum order", "ships from china", "ships from china",
    "aliexpress", "alibaba", "print on demand", "made to order — no stock held",
]


# ── Playwright scraper ────────────────────────────────────────────────────────

async def _scrape_page(page: Page, url: str, max_chars: int = 6000) -> str:
    """Load a URL and return cleaned body text."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.5)  # let lazy-load JS settle
        text = await page.evaluate("""() => {
            const skip = ['script','style','nav','footer','header','noscript','aside','iframe'];
            function getText(el) {
                if (skip.includes(el.tagName?.toLowerCase())) return '';
                if (el.nodeType === 3) return el.textContent.trim();
                return Array.from(el.childNodes).map(getText).join(' ');
            }
            return getText(document.body);
        }""")
        text = re.sub(r'\s+', ' ', text or '').strip()
        return text[:max_chars]
    except Exception as e:
        logger.warning("Failed to scrape %s: %s", url, e)
        return ""


async def _get_nav_links(page: Page, base_url: str) -> list[str]:
    """Extract all internal links from the current page."""
    try:
        hrefs = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.getAttribute('href'))
                .filter(h => h && !h.startsWith('mailto:') && !h.startsWith('tel:') && !h.startsWith('#'));
        }""")
        base = urlparse(base_url)
        results = []
        for h in hrefs:
            full = urljoin(base_url, h)
            parsed = urlparse(full)
            if parsed.netloc == base.netloc:
                results.append(full.split('#')[0].split('?')[0])
        return list(dict.fromkeys(results))  # dedup preserving order
    except Exception:
        return []


def _score_url_for_target(url: str) -> int:
    """Higher score = more likely to contain useful info."""
    score = 0
    url_lower = url.lower()
    for pattern in TARGET_PAGE_PATTERNS:
        if re.search(pattern, url_lower):
            score += 10
    return score


async def scrape_vendor_site(vendor_url: str, vendor_name: str, skus: list[str]) -> dict:
    """
    Navigate a vendor site, scrape key pages, and return raw content dict.
    Returns {pages: [{url, content}], sku_pages: [{sku, url, content}]}
    """
    pages_scraped = []
    sku_pages = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        logger.info("Loading homepage: %s", vendor_url)
        homepage_text = await _scrape_page(page, vendor_url)
        pages_scraped.append({"url": vendor_url, "label": "homepage", "content": homepage_text})

        # Collect all nav links from homepage
        all_links = await _get_nav_links(page, vendor_url)
        logger.info("Found %d internal links on homepage", len(all_links))

        # Score and pick the most relevant pages
        scored = [(url, _score_url_for_target(url)) for url in all_links]
        scored = [(u, s) for u, s in scored if s > 0]
        scored.sort(key=lambda x: -x[1])
        target_urls = [u for u, _ in scored[:6]]  # up to 6 additional pages

        for url in target_urls:
            if url == vendor_url:
                continue
            label = _guess_page_label(url)
            logger.info("  Scraping %s → %s", label, url)
            content = await _scrape_page(page, url)
            if content:
                pages_scraped.append({"url": url, "label": label, "content": content})

        # Try to find SKU-specific pages
        for sku in skus[:5]:
            sku_url = await _find_sku_page(page, vendor_url, sku, all_links)
            if sku_url:
                logger.info("  Found SKU %s at %s", sku, sku_url)
                content = await _scrape_page(page, sku_url)
                sku_pages.append({"sku": sku, "url": sku_url, "content": content})
            else:
                logger.info("  SKU %s not found on site", sku)
                sku_pages.append({"sku": sku, "url": None, "content": ""})

        await browser.close()

    return {"pages": pages_scraped, "sku_pages": sku_pages}


def _guess_page_label(url: str) -> str:
    url_lower = url.lower()
    for label, pattern in [
        ("about", r"/about"),
        ("delivery", r"/deliver|/shipping|/dispatch"),
        ("trade", r"/trade|/wholesale|/b2b"),
        ("contact", r"/contact"),
        ("faq", r"/faq"),
    ]:
        if re.search(pattern, url_lower):
            return label
    return "page"


async def _find_sku_page(page: Page, base_url: str, sku: str, existing_links: list[str]) -> Optional[str]:
    """Try to find a product page for a given SKU."""
    sku_lower = sku.lower()

    # Check if any existing links contain the SKU
    for link in existing_links:
        if sku_lower in link.lower():
            return link

    # Try site search
    parsed = urlparse(base_url)
    search_urls = [
        f"{parsed.scheme}://{parsed.netloc}/search?q={sku}",
        f"{parsed.scheme}://{parsed.netloc}/search?query={sku}",
        f"{parsed.scheme}://{parsed.netloc}/catalogsearch/result/?q={sku}",
        f"{parsed.scheme}://{parsed.netloc}/?s={sku}",
    ]

    for search_url in search_urls:
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(0.8)
            # Look for product links in results
            links = await _get_nav_links(page, base_url)
            for link in links:
                if sku_lower in link.lower():
                    return link
            # Look for first product link
            product_links = [l for l in links if any(p in l.lower() for p in ["/product", "/item", "/p/", "/products/"])]
            if product_links:
                return product_links[0]
        except Exception:
            continue

    return None


# ── Claude extraction ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are extracting supplier intelligence for NNL, a UK candle and fragrance brand.
Analyse all scraped pages from the vendor's website and extract structured data.

Be honest: if you can't find something, use null. Don't guess prices.
Flag anything suspicious (dropshipper signals, no UK address, generic product images, etc.).

Output ONLY a valid JSON object matching this schema exactly:
{
  "vendor_name": "string or null",
  "company_type": "manufacturer|distributor|wholesaler|dropshipper|retailer|unknown",
  "uk_based": true/false/null,
  "about": "1-3 sentence description of the company",
  "address": "string or null",
  "contact_email": "string or null",
  "contact_phone": "string or null",
  "certifications": ["ISO9001", "IFRA", etc.],
  "min_order_value": "string or null — e.g. '£50', '£500 minimum order'",
  "min_order_qty": "string or null — e.g. '1kg', '100 units', '1 case'",
  "lead_time": "string or null — e.g. '3-5 working days', '2-3 weeks'",
  "wholesale_available": true/false/null,
  "trade_account_required": true/false/null,
  "payment_terms": "string or null — e.g. '30 days net', 'pro-forma only'",
  "delivery_info": "string or null — carriers, costs, free delivery threshold",
  "products": [
    {
      "sku": "string — the NNL SKU we were looking for, or null if not a targeted SKU",
      "site_ref": "string — their product code/ref if visible",
      "name": "string",
      "url": "string or null",
      "price": "string or null — the listed price",
      "price_tiers": [{"qty": "string", "price": "string"}],
      "in_stock": true/false/null,
      "description": "1-2 sentences, focus on spec details useful for procurement",
      "weight_or_size": "string or null"
    }
  ],
  "potential_upstream_supplier": "string or null — who might supply THEM (e.g. if they resell Firmenich fragrances)",
  "alternatives": [
    {"name": "string", "url": "string or null", "notes": "string"}
  ],
  "risk_flags": ["any red flags or concerns"],
  "confidence_score": 1-10,
  "notes": "any other observations relevant to a procurement manager at NNL"
}"""


def _build_extraction_input(scrape_result: dict, vendor_url: str, vendor_name: str, skus: list[str]) -> str:
    parts = [
        f"Vendor: {vendor_name or vendor_url}",
        f"URL: {vendor_url}",
        f"SKUs we buy from them: {', '.join(skus) if skus else 'none specified'}",
        "",
    ]

    for page_data in scrape_result["pages"]:
        parts.append(f"=== PAGE: {page_data['label'].upper()} ({page_data['url']}) ===")
        parts.append(page_data["content"][:3000])
        parts.append("")

    for sku_data in scrape_result["sku_pages"]:
        if sku_data["content"]:
            parts.append(f"=== PRODUCT PAGE FOR SKU '{sku_data['sku']}' ({sku_data['url']}) ===")
            parts.append(sku_data["content"][:3000])
            parts.append("")
        else:
            parts.append(f"=== SKU '{sku_data['sku']}' — not found on site ===")
            parts.append("")

    return "\n".join(parts)


def extract_vendor_profile(client: anthropic.Anthropic, scrape_result: dict,
                           vendor_url: str, vendor_name: str, skus: list[str]) -> dict:
    content = _build_extraction_input(scrape_result, vendor_url, vendor_name, skus)

    logger.info("Sending to Claude for extraction...")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": content}],
    )

    text = message.content[0].text.strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError(f"Claude didn't return valid JSON. Response: {text[:200]}")

    profile = json.loads(json_match.group())

    # Enrich with metadata
    profile["vendor_url"] = vendor_url
    profile["scraped_at"] = datetime.utcnow().isoformat()
    profile["pages_scraped"] = [{"url": p["url"], "label": p["label"]} for p in scrape_result["pages"]]

    return profile


def generate_markdown_report(profile: dict) -> str:
    name = profile.get("vendor_name") or profile.get("vendor_url", "Unknown")
    lines = [
        f"# Vendor Intelligence: {name}",
        f"*Scraped: {profile.get('scraped_at', 'unknown')}*",
        "",
        "---",
        "",
        "## Company Overview",
        "",
    ]

    ctype = profile.get("company_type", "unknown")
    uk = "Yes" if profile.get("uk_based") else "No" if profile.get("uk_based") is False else "Unknown"
    lines += [
        f"- **Type:** {ctype.title()}",
        f"- **UK Based:** {uk}",
        f"- **Address:** {profile.get('address') or 'Not found'}",
        f"- **Contact:** {profile.get('contact_email') or 'Not found'} / {profile.get('contact_phone') or 'Not found'}",
    ]
    if profile.get("certifications"):
        lines.append(f"- **Certifications:** {', '.join(profile['certifications'])}")

    if profile.get("about"):
        lines += ["", profile["about"]]

    lines += ["", "---", "", "## Commercial Terms", ""]
    for label, key in [
        ("Min Order Value", "min_order_value"),
        ("Min Order Qty", "min_order_qty"),
        ("Lead Time", "lead_time"),
        ("Wholesale/Trade", "wholesale_available"),
        ("Trade Account Required", "trade_account_required"),
        ("Payment Terms", "payment_terms"),
        ("Delivery", "delivery_info"),
    ]:
        val = profile.get(key)
        if val is not None:
            if isinstance(val, bool):
                val = "Yes" if val else "No"
            lines.append(f"- **{label}:** {val}")

    products = profile.get("products") or []
    if products:
        lines += ["", "---", "", "## Products Found", ""]
        for prod in products:
            lines.append(f"### {prod.get('name', 'Unknown Product')}")
            if prod.get("sku"): lines.append(f"- **NNL SKU:** {prod['sku']}")
            if prod.get("site_ref"): lines.append(f"- **Supplier Ref:** {prod['site_ref']}")
            if prod.get("price"): lines.append(f"- **Price:** {prod['price']}")
            if prod.get("price_tiers"):
                tiers = prod["price_tiers"]
                lines.append(f"- **Price Tiers:** " + ", ".join(f"{t.get('qty','?')} @ {t.get('price','?')}" for t in tiers))
            stock = prod.get("in_stock")
            if stock is not None:
                lines.append(f"- **In Stock:** {'Yes' if stock else 'No'}")
            if prod.get("description"): lines.append(f"- {prod['description']}")
            if prod.get("url"): lines.append(f"- [Product page]({prod['url']})")
            lines.append("")

    upstream = profile.get("potential_upstream_supplier")
    alternatives = profile.get("alternatives") or []
    if upstream or alternatives:
        lines += ["---", "", "## Intelligence", ""]
        if upstream:
            lines += [f"**Potential upstream supplier:** {upstream}", ""]
        if alternatives:
            lines.append("**Comparable alternatives found:**")
            for alt in alternatives:
                url_part = f" — {alt.get('url')}" if alt.get("url") else ""
                note_part = f": {alt.get('notes')}" if alt.get("notes") else ""
                lines.append(f"- {alt.get('name', 'Unknown')}{url_part}{note_part}")
            lines.append("")

    risk_flags = profile.get("risk_flags") or []
    if risk_flags:
        lines += ["---", "", "## Risk Flags", ""]
        for flag in risk_flags:
            lines.append(f"- ⚠️ {flag}")
        lines.append("")

    score = profile.get("confidence_score")
    notes = profile.get("notes")
    if score or notes:
        lines += ["---", "", "## Assessment", ""]
        if score: lines.append(f"**Confidence score:** {score}/10")
        if notes: lines.append(f"\n{notes}")

    return "\n".join(lines)


# ── Server communication ───────────────────────────────────────────────────────

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if SUPPLY_API_KEY:
        h["X-API-Key"] = SUPPLY_API_KEY
    return h


def post_result(job_id: int, profile: dict, raw_report: str, error: str = None):
    url = f"{SUPPLY_SERVER_URL}/api/vendor-jobs/{job_id}/result"
    payload = {
        "profile": profile,
        "raw_report": raw_report,
        "error": error,
    }
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        logger.info("Posted result for job %d: %s", job_id, resp.json())
    except Exception as e:
        logger.error("Failed to post result for job %d: %s", job_id, e)


def fetch_pending_jobs() -> list[dict]:
    url = f"{SUPPLY_SERVER_URL}/api/vendor-jobs?status=pending"
    try:
        resp = requests.get(url, headers=_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("jobs", [])
    except Exception as e:
        logger.warning("Failed to fetch pending jobs: %s", e)
        return []


def mark_job_running(job_id: int):
    url = f"{SUPPLY_SERVER_URL}/api/vendor-jobs/{job_id}/start"
    try:
        requests.post(url, headers=_headers(), timeout=10)
    except Exception:
        pass


# ── Main scrape flow ───────────────────────────────────────────────────────────

async def process_vendor(vendor_url: str, vendor_name: str, skus: list[str],
                         client: anthropic.Anthropic) -> tuple[dict, str]:
    """Scrape + extract. Returns (profile_dict, markdown_report)."""
    logger.info("Scraping vendor: %s (%s)", vendor_name or vendor_url, vendor_url)

    scrape_result = await scrape_vendor_site(vendor_url, vendor_name, skus)

    pages_count = len(scrape_result["pages"])
    sku_count = sum(1 for s in scrape_result["sku_pages"] if s["content"])
    logger.info("Scraped %d pages, found %d/%d SKU pages", pages_count, sku_count, len(skus))

    profile = extract_vendor_profile(client, scrape_result, vendor_url, vendor_name, skus)
    report = generate_markdown_report(profile)

    return profile, report


async def run_single(args):
    if not ANTHROPIC_API_KEY:
        sys.exit("Error: ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    skus = [s.strip() for s in args.skus.split(",") if s.strip()] if args.skus else []

    profile, report = await process_vendor(args.url, args.name or "", skus, client)

    # Save locally
    out_dir = Path("vendor_reports")
    out_dir.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^\w-]", "_", (args.name or urlparse(args.url).netloc).lower())
    out_file = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M')}_{safe_name}.md"
    out_file.write_text(report)
    logger.info("Saved report: %s", out_file)
    print(report[:2000])

    # Post to server if configured
    if args.job_id:
        post_result(int(args.job_id), profile, report)


async def run_file(args):
    if not ANTHROPIC_API_KEY:
        sys.exit("Error: ANTHROPIC_API_KEY not set")

    vendors = json.loads(Path(args.file).read_text())
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for vendor in vendors:
        url = vendor["url"]
        name = vendor.get("name", "")
        skus = vendor.get("skus", [])
        job_id = vendor.get("job_id")

        try:
            profile, report = await process_vendor(url, name, skus, client)
            if job_id:
                post_result(int(job_id), profile, report)
            else:
                out_dir = Path("vendor_reports")
                out_dir.mkdir(exist_ok=True)
                safe_name = re.sub(r"[^\w-]", "_", (name or urlparse(url).netloc).lower())
                out_file = out_dir / f"{datetime.now().strftime('%Y%m%d_%H%M')}_{safe_name}.md"
                out_file.write_text(report)
                logger.info("Saved: %s", out_file)
        except Exception as e:
            logger.error("Failed %s: %s", url, e)


async def run_poll(args):
    if not ANTHROPIC_API_KEY:
        sys.exit("Error: ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("Poll mode: checking %s every 30s", SUPPLY_SERVER_URL)

    while True:
        jobs = fetch_pending_jobs()
        if jobs:
            logger.info("Found %d pending job(s)", len(jobs))
        for job in jobs:
            job_id = job["id"]
            vendor_url = job["vendor_url"]
            vendor_name = job.get("vendor_name", "")
            skus = job.get("skus") or []

            mark_job_running(job_id)
            try:
                profile, report = await process_vendor(vendor_url, vendor_name, skus, client)
                post_result(job_id, profile, report)
            except Exception as e:
                logger.error("Job %d failed: %s", job_id, e)
                post_result(job_id, {}, "", error=str(e))

        if not jobs:
            logger.debug("No pending jobs. Waiting 30s...")
        await asyncio.sleep(30)


def main():
    parser = argparse.ArgumentParser(description="researchOS Vendor Intelligence Scraper")
    sub = parser.add_subparsers(dest="cmd")

    # Direct scrape
    single = sub.add_parser("scrape", help="Scrape one vendor")
    single.add_argument("--url", required=True, help="Vendor website URL")
    single.add_argument("--name", default="", help="Vendor name")
    single.add_argument("--skus", default="", help="Comma-separated SKUs to find")
    single.add_argument("--job-id", default=None, help="Server job ID (to post result back)")

    # Batch from file
    batch = sub.add_parser("batch", help="Scrape from JSON file")
    batch.add_argument("--file", required=True, help="Path to vendors.json")

    # Poll server
    poll = sub.add_parser("poll", help="Poll server for pending jobs")
    poll.add_argument("--server", default=None, help="Override SUPPLY_SERVER_URL")

    # Legacy: if called with --url directly (old style)
    parser.add_argument("--url", default=None)
    parser.add_argument("--name", default="")
    parser.add_argument("--skus", default="")
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--server", default=None)

    args = parser.parse_args()

    if args.server:
        global SUPPLY_SERVER_URL
        SUPPLY_SERVER_URL = args.server

    # Dispatch
    if hasattr(args, 'cmd') and args.cmd == "scrape":
        asyncio.run(run_single(args))
    elif hasattr(args, 'cmd') and args.cmd == "batch":
        asyncio.run(run_file(args))
    elif hasattr(args, 'cmd') and args.cmd == "poll":
        asyncio.run(run_poll(args))
    elif getattr(args, 'poll', False):
        asyncio.run(run_poll(args))
    elif getattr(args, 'file', None):
        asyncio.run(run_file(args))
    elif getattr(args, 'url', None):
        asyncio.run(run_single(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
