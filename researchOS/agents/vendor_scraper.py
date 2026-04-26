"""
researchOS Vendor Intelligence Agent — server-side (Crawl4AI edition)

Uses Crawl4AI (open source, Playwright-backed) to scrape supplier sites.
Crawl4AI returns clean markdown instead of raw HTML/text, giving Claude
much better signal to extract structured supplier intelligence from.

Entry point: run_vendor_job(job_id)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Optional

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

from systemOS.llm import complete
from db import get_conn

logger = logging.getLogger(__name__)

# Model used for vendor extraction — separate from research model so they can differ
VENDOR_MODEL = os.getenv("VENDOR_SCRAPER_MODEL") or os.getenv("OLLAMA_MODEL") or None

TARGET_PATTERNS = [
    (r"/about|/our-story|/who-we-are|/company",  "about"),
    (r"/deliver|/shipping|/dispatch|/postage",    "delivery"),
    (r"/trade|/wholesale|/b2b|/trade-account|/business-account", "trade"),
    (r"/contact|/contact-us|/get-in-touch",       "contact"),
    (r"/faq|/faqs|/help",                         "faq"),
    (r"/product|/catalogue|/catalog|/shop|/store","products"),
]

# ── Crawl4AI scraping ─────────────────────────────────────────────────────────

def _label(url: str) -> str:
    u = url.lower()
    for pat, label in TARGET_PATTERNS:
        if re.search(pat, u):
            return label
    return "page"


def _score(url: str) -> int:
    u = url.lower()
    score = 0
    for pat, _ in TARGET_PATTERNS[:5]:  # skip generic product pages for scoring
        if re.search(pat, u):
            score += 10
    return score


async def _crawl_page(crawler: AsyncWebCrawler, url: str) -> tuple[str, str]:
    """Crawl one URL, return (markdown, error). markdown is empty string on failure."""
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20000)
        result = await crawler.arun(url, config=config)
        if result.success and result.markdown:
            return result.markdown[:6000], ""
        return "", f"crawl returned success=False for {url}"
    except Exception as e:
        logger.warning("[VENDOR] Failed to crawl %s: %s", url, e)
        return "", str(e)


async def _get_homepage_data(crawler: AsyncWebCrawler, url: str) -> tuple[list[dict], str]:
    """Crawl homepage, return (internal_links, effective_base_url).
    effective_base_url is the final URL after any redirects — used for domain matching."""
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20000)
        result = await crawler.arun(url, config=config)
        if result.success:
            # Use redirected_url if available (handles domain redirects)
            effective = getattr(result, 'redirected_url', None) or url
            return result.links.get("internal", []), effective
    except Exception as e:
        logger.warning("[VENDOR] Failed to get links from %s: %s", url, e)
    return [], url


async def _find_product_url(crawler: AsyncWebCrawler, base_url: str, product_query: str,
                            internal_links: list[dict]) -> Optional[str]:
    """
    Search a vendor site for a product. product_query is a human-readable description
    (e.g. "coco apricot wax", "100ml glass bottle") — NOT an internal NNL A0 code.

    Strategy:
    1. Check existing links for the query string in the URL
    2. Run site search and use AI to pick the best match from results
    """
    query_lower = product_query.lower().strip()

    # 1. Quick check: does any known link already match?
    for link in internal_links:
        href = link.get("href", "")
        text = link.get("text", "").lower()
        if query_lower in href.lower() or query_lower in text:
            return href

    # 2. Try site search — wait longer for JS-rendered results
    parsed = urlparse(base_url)
    netloc = parsed.netloc
    search_attempts = [
        f"{parsed.scheme}://{netloc}/search?q={product_query}",
        f"{parsed.scheme}://{netloc}/search?query={product_query}",
        f"{parsed.scheme}://{netloc}/?s={product_query}",
        f"{parsed.scheme}://{netloc}/catalogsearch/result/?q={product_query}",
        f"{parsed.scheme}://{netloc}/search?type=product&q={product_query}",
    ]

    for search_url in search_attempts:
        try:
            # Longer wait — JS search results need time to render
            config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=15000,
                                      wait_until="networkidle")
            result = await crawler.arun(search_url, config=config)
            if not result.success:
                continue

            # Collect candidate product links from search results
            candidates = []
            for link in result.links.get("internal", []):
                href = link.get("href", "")
                text = link.get("text", "").strip()
                if not href or not text:
                    continue
                # Filter to product-looking URLs only
                if any(p in href.lower() for p in ["/product", "/p/", "/item", "/shop/", "/buy"]):
                    candidates.append({"href": href, "text": text})

            if not candidates:
                # Fallback: any link whose anchor text mentions words from the query
                words = set(query_lower.split())
                for link in result.links.get("internal", []):
                    text = link.get("text", "").lower()
                    href = link.get("href", "")
                    if href and sum(1 for w in words if w in text) >= min(2, len(words)):
                        candidates.append({"href": href, "text": link.get("text", "")})

            if not candidates:
                continue

            if len(candidates) == 1:
                return candidates[0]["href"]

            # 3. AI picks the best match when there are multiple candidates
            best = await _ai_pick_best_product(candidates[:10], product_query)
            if best:
                return best

        except Exception as e:
            logger.debug("[VENDOR] Search attempt failed for %s: %s", search_url, e)
            continue

    return None


async def _ai_pick_best_product(candidates: list[dict], query: str) -> Optional[str]:
    """Ask the LLM to pick the best matching product URL from a list of candidates."""
    if not candidates:
        return None

    listing = "\n".join(f"{i+1}. [{c['text']}]({c['href']})" for i, c in enumerate(candidates))
    prompt = f"""I'm looking for a product matching: "{query}"

These are product links found on a supplier's search results page:
{listing}

Which link (by number) is the best match? Reply with ONLY the number, e.g. "3".
If none are a good match, reply "0"."""

    try:
        text = await complete(
            messages=[{"role": "user", "content": prompt}],
            fast=True,
            max_tokens=10,
            model=VENDOR_MODEL,
        )
        n = int(re.search(r'\d+', text).group())
        if 1 <= n <= len(candidates):
            return candidates[n - 1]["href"]
    except Exception:
        pass
    return candidates[0]["href"]  # fallback to first


async def scrape_vendor(vendor_url: str, vendor_name: str, skus: list[str], emit=None) -> dict:
    """
    Multi-page crawl of a vendor site using Crawl4AI.
    Returns clean markdown per page — far better signal for LLM extraction.
    """
    pages = []
    sku_pages = []

    def log(msg):
        logger.info("[VENDOR] %s", msg)
        if emit:
            emit("info", msg)

    async with AsyncWebCrawler() as crawler:

        # ── Homepage ──────────────────────────────────────────────────────────
        log(f"Crawling homepage: {vendor_url}")
        home_md, err = await _crawl_page(crawler, vendor_url)
        if err:
            log(f"  Homepage error: {err}")
        pages.append({"url": vendor_url, "label": "homepage", "content": home_md})

        # ── Discover internal links (handles domain redirects) ───────────────
        internal_links, effective_base = await _get_homepage_data(crawler, vendor_url)
        effective_netloc = urlparse(effective_base).netloc
        log(f"Found {len(internal_links)} internal links (effective domain: {effective_netloc})")

        # Deduplicate + score — compare against the effective (post-redirect) domain
        seen_urls = {vendor_url, effective_base}
        scored = []
        for link in internal_links:
            href = link.get("href", "")
            if not href or href in seen_urls:
                continue
            if not href.startswith("http"):
                href = urljoin(effective_base, href)
            if urlparse(href).netloc != effective_netloc:
                continue
            score = _score(href)
            if score > 0:
                scored.append((href, score, _label(href)))
                seen_urls.add(href)

        scored.sort(key=lambda x: -x[1])
        targets = scored[:6]  # top 6 most useful pages

        # ── Target pages (about, delivery, trade, contact, faq) ───────────────
        for href, score, label in targets:
            log(f"  Crawling {label}: {href}")
            md, err = await _crawl_page(crawler, href)
            if md:
                pages.append({"url": href, "label": label, "content": md})
            elif err:
                log(f"    Failed: {err}")

        # ── Product pages (search by product name/description) ────────────────
        for product_query in skus[:5]:
            log(f"  Searching for: '{product_query}'")
            product_url = await _find_product_url(crawler, effective_base, product_query, internal_links)
            if product_url:
                log(f"    Found: {product_url}")
                md, err = await _crawl_page(crawler, product_url)
                sku_pages.append({"sku": product_query, "url": product_url, "content": md})
            else:
                log(f"    Not found on site")
                sku_pages.append({"sku": product_query, "url": None, "content": ""})

    log(f"Crawled {len(pages)} pages, {sum(1 for s in sku_pages if s['content'])} SKU pages found")
    return {"pages": pages, "sku_pages": sku_pages}


# ── LLM extraction ─────────────────────────────────────────────────────────────

EXTRACTION_SYSTEM = """You are a procurement analyst extracting supplier intelligence for NNL, a UK candle and fragrance brand.
You will receive clean markdown scraped from a supplier's website across multiple pages.
Extract structured data accurately. Use null when data is not present — do not guess or invent.
Flag any concerns: dropshipper signals, no UK presence, no real stock held, reseller language.
Return ONLY valid JSON — no explanation, no markdown code fences, just the raw JSON object."""

EXTRACTION_PROMPT = """Vendor: {vendor_name}
URL: {vendor_url}
NNL SKUs we buy from them: {skus}

--- SCRAPED PAGES ---
{pages_content}
--- END ---

Extract and return this JSON (use null for unknown fields):
{{
  "vendor_name": "string or null",
  "company_type": "manufacturer|distributor|wholesaler|dropshipper|retailer|unknown",
  "uk_based": true/false/null,
  "about": "2-3 sentence company summary",
  "address": "full address string or null",
  "contact_email": "string or null",
  "contact_phone": "string or null",
  "certifications": ["ISO9001", "IFRA", etc],
  "min_order_value": "e.g. £50 minimum or null",
  "min_order_qty": "e.g. 1kg, 100 units or null",
  "lead_time": "e.g. 3-5 working days or null",
  "wholesale_available": true/false/null,
  "trade_account_required": true/false/null,
  "payment_terms": "e.g. 30 days net, pro-forma or null",
  "delivery_info": "carriers, costs, free delivery threshold or null",
  "products": [
    {{
      "sku": "NNL SKU this matches (from our list above), or null if not a targeted product",
      "site_ref": "supplier's own product code if visible, or null",
      "name": "product name",
      "url": "product page URL or null",
      "price": "listed price e.g. £4.50/kg or null",
      "price_tiers": [{{"qty": "1kg", "price": "£4.50"}}, {{"qty": "5kg", "price": "£4.00"}}],
      "in_stock": true/false/null,
      "description": "spec detail useful for procurement: fragrance family, material, dimensions, weight",
      "weight_or_size": "string or null"
    }}
  ],
  "potential_upstream_supplier": "e.g. Firmenich, Givaudan, or Chinese manufacturer name if hinted at",
  "alternatives": [
    {{"name": "company name", "url": "URL or null", "notes": "why relevant"}}
  ],
  "risk_flags": ["list any: appears to be dropshipper, no UK address, no real stock, reseller language, no contact info, etc"],
  "confidence_score": 8,
  "notes": "any other procurement-relevant observations"
}}"""


async def extract_profile(scrape_result: dict, vendor_url: str, vendor_name: str, skus: list[str]) -> dict:
    parts = []
    for pg in scrape_result["pages"]:
        if pg["content"]:
            parts.append(f"### {pg['label'].upper()} — {pg['url']}\n\n{pg['content']}")

    for sp in scrape_result["sku_pages"]:
        if sp["content"]:
            parts.append(f"### PRODUCT PAGE FOR SKU '{sp['sku']}' — {sp['url']}\n\n{sp['content']}")
        else:
            parts.append(f"### SKU '{sp['sku']}' — not found on this site")

    prompt = EXTRACTION_PROMPT.format(
        vendor_name=vendor_name or vendor_url,
        vendor_url=vendor_url,
        skus=", ".join(skus) if skus else "none specified",
        pages_content="\n\n---\n\n".join(parts) if parts else "No content scraped.",
    )

    text = await complete(
        messages=[{"role": "user", "content": prompt}],
        system=EXTRACTION_SYSTEM,
        fast=False,
        max_tokens=4000,
        model=VENDOR_MODEL,
    )

    # Strip any accidental markdown fences
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        raise ValueError(f"LLM returned no JSON. Got: {text[:300]}")

    profile = json.loads(json_match.group())
    profile["vendor_url"] = vendor_url
    profile["pages_scraped"] = [{"url": pg["url"], "label": pg["label"]} for pg in scrape_result["pages"]]
    return profile


def _make_report(profile: dict) -> str:
    name = profile.get("vendor_name") or profile.get("vendor_url", "Unknown")
    ts = datetime.now().strftime("%d %b %Y %H:%M")
    lines = [f"# Vendor Intelligence: {name}", f"*Scraped: {ts}*", "", "---", "", "## Overview", ""]

    ctype = profile.get("company_type", "—")
    uk = "✅ Yes" if profile.get("uk_based") else "❌ No" if profile.get("uk_based") is False else "—"
    lines += [
        f"- **Type:** {ctype.title()}",
        f"- **UK Based:** {uk}",
        f"- **Address:** {profile.get('address') or '—'}",
        f"- **Email:** {profile.get('contact_email') or '—'}",
        f"- **Phone:** {profile.get('contact_phone') or '—'}",
    ]
    certs = profile.get("certifications") or []
    if certs:
        lines.append(f"- **Certifications:** {', '.join(certs)}")
    if profile.get("about"):
        lines += ["", profile["about"]]

    lines += ["", "---", "", "## Commercial Terms", ""]
    for label, key in [
        ("Min Order Value", "min_order_value"), ("Min Order Qty", "min_order_qty"),
        ("Lead Time", "lead_time"), ("Wholesale Available", "wholesale_available"),
        ("Trade Account Required", "trade_account_required"),
        ("Payment Terms", "payment_terms"), ("Delivery", "delivery_info"),
    ]:
        val = profile.get(key)
        if val is not None:
            display = "Yes" if val is True else "No" if val is False else val
            lines.append(f"- **{label}:** {display}")

    products = profile.get("products") or []
    if products:
        lines += ["", "---", "", "## Products", ""]
        for prod in products:
            lines.append(f"### {prod.get('name', 'Unknown product')}")
            for lbl, k in [("NNL SKU", "sku"), ("Supplier Ref", "site_ref"), ("Price", "price"), ("Stock", "in_stock")]:
                val = prod.get(k)
                if val is not None:
                    display = "In stock" if val is True else "Out of stock" if val is False else val
                    lines.append(f"- **{lbl}:** {display}")
            tiers = prod.get("price_tiers") or []
            if tiers:
                lines.append("- **Price Tiers:** " + " | ".join(f"{t.get('qty','?')} → {t.get('price','?')}" for t in tiers))
            if prod.get("description"):
                lines.append(f"- {prod['description']}")
            if prod.get("url"):
                lines.append(f"- [Product page]({prod['url']})")
            lines.append("")

    upstream = profile.get("potential_upstream_supplier")
    alts = profile.get("alternatives") or []
    if upstream or alts:
        lines += ["---", "", "## Intelligence", ""]
        if upstream:
            lines += [f"**Potential upstream supplier:** {upstream}", ""]
        if alts:
            lines += ["**Comparable alternatives:**"]
            for a in alts:
                url_part = f" — {a.get('url')}" if a.get("url") else ""
                note_part = f": {a.get('notes')}" if a.get("notes") else ""
                lines.append(f"- **{a.get('name','?')}**{url_part}{note_part}")
            lines.append("")

    flags = profile.get("risk_flags") or []
    if flags:
        lines += ["---", "", "## Risk Flags", ""] + [f"- ⚠️ {f}" for f in flags] + [""]

    if profile.get("confidence_score") or profile.get("notes"):
        lines += ["---", "", "## Assessment", ""]
        if profile.get("confidence_score"):
            lines.append(f"**Confidence:** {profile['confidence_score']}/10")
        if profile.get("notes"):
            lines += ["", profile["notes"]]

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

async def run_vendor_job(job_id: int, emit=None):
    """Load job from DB, scrape with Crawl4AI, extract with LLM, write profile to DB."""

    def log(msg):
        logger.info("[VENDOR JOB %d] %s", job_id, msg)
        if emit:
            emit("info", msg)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vendor_url, vendor_name, skus, category FROM supply.vendor_scrape_jobs WHERE id=%s",
                (job_id,),
            )
            row = cur.fetchone()
    if not row:
        logger.error("[VENDOR] Job %d not found", job_id)
        return

    vendor_url, vendor_name, skus_json, category = row
    skus = skus_json if isinstance(skus_json, list) else json.loads(skus_json or "[]")
    vendor_name = vendor_name or ""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE supply.vendor_scrape_jobs SET status='running' WHERE id=%s", (job_id,))

    try:
        log(f"Starting: {vendor_url}")
        scrape_result = await scrape_vendor(vendor_url, vendor_name, skus, emit=emit)
        log("Extracting with LLM...")

        profile = await extract_profile(scrape_result, vendor_url, vendor_name, skus)
        raw_report = _make_report(profile)

        try:
            import markdown as _md
            report_html = _md.markdown(raw_report, extensions=["extra", "toc"])
        except Exception:
            report_html = None

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO supply.vendor_profiles
                       (job_id, vendor_name, vendor_url, category,
                        company_type, uk_based, about, address, contact_email, contact_phone,
                        certifications, min_order_value, min_order_qty, lead_time,
                        wholesale_available, trade_account_required, payment_terms, delivery_info,
                        products, potential_upstream, alternatives, risk_flags, confidence_score,
                        raw_report, raw_report_html, pages_scraped)
                       VALUES (%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s)""",
                    (
                        job_id,
                        profile.get("vendor_name") or vendor_name or None,
                        vendor_url, category,
                        profile.get("company_type"), profile.get("uk_based"),
                        profile.get("about"), profile.get("address"),
                        profile.get("contact_email"), profile.get("contact_phone"),
                        json.dumps(profile.get("certifications") or []),
                        profile.get("min_order_value"), profile.get("min_order_qty"),
                        profile.get("lead_time"),
                        profile.get("wholesale_available"), profile.get("trade_account_required"),
                        profile.get("payment_terms"), profile.get("delivery_info"),
                        json.dumps(profile.get("products") or []),
                        profile.get("potential_upstream_supplier"),
                        json.dumps(profile.get("alternatives") or []),
                        json.dumps(profile.get("risk_flags") or []),
                        profile.get("confidence_score"),
                        raw_report, report_html,
                        json.dumps(profile.get("pages_scraped") or []),
                    ),
                )
                cur.execute(
                    "UPDATE supply.vendor_scrape_jobs SET status='done', completed_at=NOW() WHERE id=%s",
                    (job_id,),
                )
        log("Done — profile saved.")

    except Exception as e:
        logger.error("[VENDOR JOB %d] Failed: %s", job_id, e, exc_info=True)
        if emit:
            emit("error", str(e))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE supply.vendor_scrape_jobs SET status='error', error_msg=%s WHERE id=%s",
                    (str(e)[:500], job_id),
                )
