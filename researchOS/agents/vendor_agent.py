"""
researchOS Vendor Intelligence Agent — Agentic Edition

The LLM drives the research. It decides what pages to scrape, what to search
for, when it has enough data, and when to look for alternatives on other suppliers.

Tools available to the LLM:
  scrape_page(url)          — get clean markdown from any URL
  search_site(query)        — search this supplier's site and return results
  search_web(query)         — SearXNG search (find alternatives, compare prices)
  get_links()               — get navigation links from the vendor homepage
  done(profile)             — finish: produce the final structured vendor profile

Loop: LLM → JSON action → tool executes → result appended → repeat
Depth presets control: max iterations, time budget, page char limit.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

from systemOS.config.depth import get as get_depth
from db import get_conn
from systemOS.mcp.search import run_search

logger = logging.getLogger(__name__)

VENDOR_MODEL = os.getenv("VENDOR_SCRAPER_MODEL") or os.getenv("OLLAMA_MODEL") or None
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT_BASE = """You are a procurement intelligence agent for NNL, a UK candle and fragrance brand.
Your job is to research supplier websites and build structured intelligence profiles.

You have these tools. Respond with ONLY a JSON object on every turn — no explanation outside JSON.

TOOLS:
1. scrape_page — get clean text from any URL
   {{"tool": "scrape_page", "args": {{"url": "https://..."}}, "reasoning": "why"}}

2. search_site — search this supplier's own site for a product or topic
   {{"tool": "search_site", "args": {{"query": "coco apricot wax 1kg"}}, "reasoning": "why"}}

3. search_web — search the web via SearXNG (use this to find alternative suppliers, compare products across suppliers, or find upstream manufacturers)
   {{"tool": "search_web", "args": {{"query": "coco apricot wax wholesale UK supplier"}}, "reasoning": "why"}}

4. get_links — get navigation links from the vendor's homepage
   {{"tool": "get_links", "args": {{}}, "reasoning": "why"}}

5. done — you have enough information, produce the final profile
   {{"tool": "done", "profile": {{ ...see schema below... }}}}

PROFILE SCHEMA (use null for unknown, do not guess):
{{
  "vendor_name": "string",
  "company_type": "manufacturer|distributor|wholesaler|dropshipper|retailer|unknown",
  "uk_based": true/false/null,
  "about": "2-3 sentence company summary",
  "address": "string or null",
  "contact_email": "string or null",
  "contact_phone": "string or null",
  "certifications": [],
  "min_order_value": "e.g. £50 or null",
  "min_order_qty": "e.g. 1kg or null",
  "lead_time": "e.g. 3-5 working days or null",
  "wholesale_available": true/false/null,
  "trade_account_required": true/false/null,
  "payment_terms": "string or null",
  "delivery_info": "string or null",
  "products": [
    {{
      "query": "the product search term we were looking for",
      "name": "product name on site",
      "url": "product page URL or null",
      "price": "listed price or null",
      "price_tiers": [{{"qty": "1kg", "price": "£4.50"}}],
      "in_stock": true/false/null,
      "description": "spec relevant to procurement",
      "alternatives_found": [{{"name": "...", "url": "...", "price": "...", "supplier": "..."}}]
    }}
  ],
  "potential_upstream_supplier": "e.g. Firmenich or null",
  "web_alternatives": [
    {{"company": "...", "url": "...", "product": "...", "price": "...", "notes": "..."}}
  ],
  "risk_flags": [],
  "confidence_score": 7,
  "notes": "procurement observations"
}}

STRATEGY GUIDANCE:
- Start with get_links to understand site structure, then scrape homepage
- Search for each product the user wants to find
- Use search_web to find alternative suppliers once you know what products are available
- Check delivery/trade/wholesale pages for commercial terms
- Look for price tier tables (bulk discounts) on product pages
- A confidence score of 8+ means you found pricing, lead times, and MOQ

SESSION INSTRUCTIONS:
{agent_instruction}
Time budget: {time_budget_min} minutes. You have approximately {max_iterations} tool calls.
Call done before the budget runs out — a partial profile is better than none."""


def _build_system_prompt(depth_cfg: dict) -> str:
    return SYSTEM_PROMPT_BASE.format(
        agent_instruction=depth_cfg["agent_instruction"],
        time_budget_min=depth_cfg["est_minutes"],
        max_iterations=depth_cfg["max_iterations"],
    )


# ── LLM call ──────────────────────────────────────────────────────────────────

async def _llm_call(messages: list[dict]) -> str:
    """Call Ollama or Anthropic, return raw text response."""
    if OLLAMA_URL:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/v1/chat/completions",
                json={
                    "model": VENDOR_MODEL,
                    "messages": messages,
                    "stream": False,
                    "temperature": 0.1,  # low temp for structured extraction
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    if ANTHROPIC_API_KEY:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        system_text = None
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            else:
                filtered.append(m)
        kwargs = dict(model="claude-sonnet-4-6", max_tokens=2000,
                      messages=filtered, temperature=0.1)
        if system_text:
            kwargs["system"] = system_text
        msg = client.messages.create(**kwargs)
        return msg.content[0].text.strip()

    raise RuntimeError("No LLM backend configured")


def _parse_action(text: str) -> dict:
    """Extract JSON action from LLM response."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON in LLM response: {text[:200]}")
    return json.loads(match.group())


# ── Tools ─────────────────────────────────────────────────────────────────────

async def _tool_scrape_page(crawler: AsyncWebCrawler, url: str, max_chars: int = 5000) -> str:
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=20000)
        result = await crawler.arun(url, config=config)
        if result.success and result.markdown:
            return result.markdown[:max_chars]
        return f"[Failed to scrape {url}]"
    except Exception as e:
        return f"[Error scraping {url}: {e}]"


async def _tool_search_site(crawler: AsyncWebCrawler, base_url: str, effective_base: str,
                            query: str, page_chars: int = 5000) -> str:
    parsed = urlparse(effective_base)
    netloc = parsed.netloc
    search_urls = [
        f"{parsed.scheme}://{netloc}/search?q={query}",
        f"{parsed.scheme}://{netloc}/search?query={query}",
        f"{parsed.scheme}://{netloc}/?s={query}",
        f"{parsed.scheme}://{netloc}/catalogsearch/result/?q={query}",
    ]
    for url in search_urls:
        try:
            config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=15000,
                                      wait_until="networkidle")
            result = await crawler.arun(url, config=config)
            if result.success and result.markdown and len(result.markdown.strip()) > 100:
                links = result.links.get("internal", [])
                product_links = [
                    f"- [{l.get('text','?')}]({l.get('href','')})"
                    for l in links[:20]
                    if l.get('text') and l.get('href')
                ]
                link_section = "\n\nLinks on results page:\n" + "\n".join(product_links) if product_links else ""
                return result.markdown[:page_chars] + link_section
        except Exception:
            continue
    return f"[No search results found for '{query}' on {netloc}]"


async def _tool_search_web(query: str) -> str:
    results = await run_search(query, num_results=8)
    if not results:
        return f"[No web results for '{query}']"
    lines = [f"Web search results for: {query}\n"]
    for r in results:
        lines.append(f"**{r['title']}**\n{r['url']}\n{r.get('content','')[:300]}\n")
    return "\n".join(lines)


async def _tool_get_links(crawler: AsyncWebCrawler, vendor_url: str, effective_base: str) -> str:
    try:
        config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=15000)
        result = await crawler.arun(vendor_url, config=config)
        if not result.success:
            return "[Failed to get links]"
        internal = result.links.get("internal", [])
        netloc = urlparse(effective_base).netloc
        lines = [f"Navigation links for {netloc}:\n"]
        seen = set()
        for link in internal:
            href = link.get("href", "")
            text = link.get("text", "").strip()
            if href and text and href not in seen and len(text) < 80:
                seen.add(href)
                lines.append(f"- [{text}]({href})")
            if len(lines) > 50:
                break
        return "\n".join(lines)
    except Exception as e:
        return f"[Error getting links: {e}]"


# ── Report builder ─────────────────────────────────────────────────────────────

def _build_report(profile: dict) -> str:
    name = profile.get("vendor_name") or "Unknown vendor"
    lines = [
        f"# Vendor Intelligence: {name}",
        f"*Scraped: {datetime.now().strftime('%d %b %Y %H:%M')} — Agentic mode*",
        "", "---", "", "## Overview", "",
        f"- **Type:** {profile.get('company_type', '—')}",
        f"- **UK Based:** {'✅ Yes' if profile.get('uk_based') else '❌ No' if profile.get('uk_based') is False else '—'}",
        f"- **Address:** {profile.get('address') or '—'}",
        f"- **Email:** {profile.get('contact_email') or '—'}",
        f"- **Phone:** {profile.get('contact_phone') or '—'}",
    ]
    if profile.get("certifications"):
        lines.append(f"- **Certifications:** {', '.join(profile['certifications'])}")
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
        lines += ["", "---", "", "## Products Found", ""]
        for prod in products:
            lines.append(f"### {prod.get('name') or prod.get('query', 'Unknown')}")
            lines.append(f"*Searched for: {prod.get('query', '—')}*")
            for lbl, k in [("Price", "price"), ("Stock", "in_stock")]:
                val = prod.get(k)
                if val is not None:
                    lines.append(f"- **{lbl}:** {'In stock' if val is True else 'Out of stock' if val is False else val}")
            tiers = prod.get("price_tiers") or []
            if tiers:
                lines.append("- **Price Tiers:** " + " | ".join(f"{t.get('qty','?')} → {t.get('price','?')}" for t in tiers))
            if prod.get("description"):
                lines.append(f"- {prod['description']}")
            if prod.get("url"):
                lines.append(f"- [Product page]({prod['url']})")
            alts = prod.get("alternatives_found") or []
            if alts:
                lines += ["- **Alternatives found on web:**"]
                for a in alts:
                    lines.append(f"  - {a.get('name','?')} @ {a.get('supplier','?')} — {a.get('price','?')}: {a.get('url','')}")
            lines.append("")

    web_alts = profile.get("web_alternatives") or []
    if web_alts:
        lines += ["---", "", "## Alternative Suppliers Found", ""]
        for a in web_alts:
            lines.append(f"### {a.get('company','?')}")
            if a.get("url"): lines.append(f"- **URL:** {a['url']}")
            if a.get("product"): lines.append(f"- **Product:** {a['product']}")
            if a.get("price"): lines.append(f"- **Price:** {a['price']}")
            if a.get("notes"): lines.append(f"- {a['notes']}")
            lines.append("")

    upstream = profile.get("potential_upstream_supplier")
    if upstream:
        lines += ["---", "", f"## Upstream Supplier", "", f"**{upstream}**", ""]

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


# ── Main agentic loop ─────────────────────────────────────────────────────────

async def run_vendor_agent(job_id: int, emit=None):
    """
    Agentic vendor intelligence job.
    The LLM drives — it calls tools, decides what to look at, builds the profile.
    Depth preset controls max iterations, time budget, and page char limit.
    """

    def log(msg: str):
        logger.info("[AGENT %d] %s", job_id, msg)
        if emit:
            emit("info", msg)

    # Load job (including depth)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vendor_url, vendor_name, skus, category, depth FROM supply.vendor_scrape_jobs WHERE id=%s",
                (job_id,),
            )
            row = cur.fetchone()
    if not row:
        logger.error("[AGENT] Job %d not found", job_id)
        return

    vendor_url, vendor_name, skus_json, category, depth = row
    products = skus_json if isinstance(skus_json, list) else json.loads(skus_json or "[]")
    vendor_name = vendor_name or ""
    cfg = get_depth(depth or "standard")

    max_iterations = cfg["max_iterations"]
    page_chars     = cfg["page_chars"]
    time_budget    = cfg["time_budget_s"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE supply.vendor_scrape_jobs SET status='running' WHERE id=%s", (job_id,))

    try:
        effective_base = vendor_url
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                r = await client.get(vendor_url)
                effective_base = str(r.url)
        except Exception:
            pass

        log(f"Depth: {cfg['label']} ({cfg['est_minutes']} min budget, {max_iterations} max iterations)")
        log(f"Vendor: {vendor_url} → {urlparse(effective_base).netloc}")
        log(f"Products: {products or 'general intelligence'} | Model: {VENDOR_MODEL}")

        products_str = ", ".join(products) if products else "none — do general company intelligence"
        initial_message = (
            f"Research this supplier for NNL:\n"
            f"Vendor: {vendor_name or vendor_url}\n"
            f"URL: {vendor_url}\n"
            f"Products to find: {products_str}\n\n"
            f"You have {cfg['est_minutes']} minutes and {max_iterations} tool calls. Use them wisely."
        )

        conversation = [
            {"role": "system", "content": _build_system_prompt(cfg)},
            {"role": "user", "content": initial_message},
        ]

        profile = None
        iterations = 0
        tool_calls_log = []
        start_time = time.time()

        async with AsyncWebCrawler() as crawler:
            while iterations < max_iterations:
                # ── Time budget check ─────────────────────────────────────────
                elapsed = time.time() - start_time
                remaining = time_budget - elapsed
                if remaining <= 60:  # less than 1 minute left
                    log(f"⏱ Time budget almost exhausted ({elapsed/60:.1f}/{cfg['est_minutes']} min) — forcing done")
                    break

                iterations += 1
                elapsed_str = f"{elapsed/60:.1f}min"
                log(f"[{iterations}/{max_iterations}] [{elapsed_str}] Calling LLM...")

                raw = await _llm_call(conversation)
                conversation.append({"role": "assistant", "content": raw})

                try:
                    action = _parse_action(raw)
                except (ValueError, json.JSONDecodeError) as e:
                    log(f"  Parse error: {e} — retrying")
                    conversation.append({
                        "role": "user",
                        "content": "Your response was not valid JSON. Respond with ONLY a JSON object."
                    })
                    continue

                tool = action.get("tool")
                args = action.get("args", {})
                reasoning = action.get("reasoning", "")

                if reasoning:
                    log(f"  → {tool}: {reasoning[:120]}")
                else:
                    log(f"  → {tool}")

                # ── done ──────────────────────────────────────────────────────
                if tool == "done":
                    profile = action.get("profile", {})
                    log(f"  ✓ Agent finished in {elapsed/60:.1f} min, {iterations} calls")
                    break

                # ── execute tool ──────────────────────────────────────────────
                if tool == "scrape_page":
                    url = args.get("url", "")
                    log(f"    Scraping: {url}")
                    result_text = await _tool_scrape_page(crawler, url, max_chars=page_chars)
                    tool_calls_log.append(f"scrape_page({url})")

                elif tool == "search_site":
                    query = args.get("query", "")
                    log(f"    Site search: {query}")
                    result_text = await _tool_search_site(
                        crawler, vendor_url, effective_base, query, page_chars=page_chars
                    )
                    tool_calls_log.append(f"search_site({query})")

                elif tool == "search_web":
                    query = args.get("query", "")
                    log(f"    Web search: {query}")
                    result_text = await _tool_search_web(query)
                    tool_calls_log.append(f"search_web({query})")

                elif tool == "get_links":
                    log("    Getting navigation links")
                    result_text = await _tool_get_links(crawler, vendor_url, effective_base)
                    tool_calls_log.append("get_links()")

                else:
                    result_text = f"[Unknown tool: {tool}]"

                log(f"    ← {result_text[:120].replace(chr(10), ' ')}…")

                # Inject time remaining so LLM knows when to wrap up
                remaining_after = time_budget - (time.time() - start_time)
                time_note = f"\n\n[{remaining_after/60:.1f} min remaining, {max_iterations - iterations} calls left]"
                conversation.append({
                    "role": "user",
                    "content": f"Tool result ({tool}):\n\n{result_text}{time_note}"
                })

        # ── Force done if budget/iterations exhausted ─────────────────────────
        if profile is None:
            elapsed = time.time() - start_time
            log(f"Budget reached ({elapsed/60:.1f} min, {iterations} calls) — synthesising from gathered data")
            conversation.append({
                "role": "user",
                "content": (
                    "Time or iteration budget reached. "
                    "Call done NOW with your best profile based on what you have gathered. "
                    "Use null for anything you didn't find. Do not make any more tool calls."
                )
            })
            raw = await _llm_call(conversation)
            try:
                action = _parse_action(raw)
                profile = action.get("profile", {})
            except Exception:
                profile = {}

        if not profile:
            profile = {}

        raw_report = _build_report(profile)

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
                        json.dumps((profile.get("web_alternatives") or []) + (profile.get("alternatives") or [])),
                        json.dumps(profile.get("risk_flags") or []),
                        profile.get("confidence_score"),
                        raw_report, report_html,
                        json.dumps(tool_calls_log),
                    ),
                )
                cur.execute(
                    "UPDATE supply.vendor_scrape_jobs SET status='done', completed_at=NOW() WHERE id=%s",
                    (job_id,),
                )

        log(f"Done. {iterations} tool calls. Confidence: {profile.get('confidence_score', '?')}/10")

    except Exception as e:
        logger.error("[AGENT %d] Failed: %s", job_id, e, exc_info=True)
        if emit:
            emit("error", str(e))
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE supply.vendor_scrape_jobs SET status='error', error_msg=%s WHERE id=%s",
                    (str(e)[:500], job_id),
                )
