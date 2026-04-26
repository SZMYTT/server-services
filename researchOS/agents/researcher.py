"""
researchOS Research Agent — with checkpointing.

Each topic saves a JSON checkpoint after every step so a long run can be
resumed if the server restarts, the LLM times out, or anything else fails.

Checkpoint stages:
  queries     — search queries generated
  searched    — SearXNG results gathered
  scraped     — top URLs deep-scraped
  synthesised — report text written
  done        — saved to file + DB

Usage:
    from agents.researcher import research
    result = await research("what KPIs should a procurement manager track",
                            topic_id=1)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from systemOS.mcp.search import run_search
from systemOS.mcp.browser import scrape
from systemOS.llm import complete, complete_with_usage, log_llm_call

logger = logging.getLogger(__name__)

RESEARCHER_CONTEXT = """
You are a supply chain and procurement research assistant for Daniel,
an Inventory Manager at NNL — a UK candle, fragrance, and homeware brand
with 10+ physical retail shops and wholesale/online channels.

NNL context:
- Uses MRP Easy as their ERP/MRP system
- Manages production (manufacturing finished goods from raw materials)
- Manages procurement of raw materials and third-party finished goods
- Multiple physical shop locations across the UK (Aldeburgh, Cambridge, Chelsea,
  Columbia Road, Southwold, Woodbridge, and others)
- Weekly Monday shop replenishment cycle
- Has a custom Google Sheets + Apps Script inventory layer (being upgraded)
- Small team — Daniel handles procurement, inventory management, and production planning

When researching, prioritise:
- Practical, actionable advice over theory
- Tools and approaches that work for small-to-mid sized manufacturers/retailers
- UK-relevant context where applicable
- What can be done with limited IT resources or existing tools (Google Sheets, MRP Easy)
- AI and automation opportunities with realistic implementation effort
"""

DEFAULT_STRUCTURE = """
Structure the report as:
## Summary (3-5 bullet points of the most important takeaways)
## What Good Looks Like (industry best practices for this topic)
## Where Daniel Likely Is Now (honest assessment of typical gaps at this scale)
## Immediate Actions (3-5 things to implement this month, low effort)
## Medium-term Improvements (things to build toward, 1-6 months)
## Tools & Resources (specific tools, templates, software worth knowing about)
## AI & Automation Opportunities (what can be automated or AI-assisted for this topic)
"""


# ── Checkpointing ──────────────────────────────────────────────────────────────

def _checkpoint_dir() -> Path:
    d = Path(os.environ.get("RESEARCH_OUTPUT_DIR", "research")) / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(topic_id: int) -> Path:
    return _checkpoint_dir() / f"{topic_id}.json"


def _load_checkpoint(topic_id: int) -> dict | None:
    p = _checkpoint_path(topic_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _save_checkpoint(topic_id: int, data: dict):
    _checkpoint_path(topic_id).write_text(json.dumps(data, indent=2, default=str))


def _clear_checkpoint(topic_id: int):
    p = _checkpoint_path(topic_id)
    if p.exists():
        p.unlink()


# ── Step 1: generate queries ───────────────────────────────────────────────────

async def _generate_queries(topic: str, n: int = 4) -> list[str]:
    prompt = f"""I need to research this topic: "{topic}"

Generate exactly {n} specific, targeted search queries that will find the most useful
information. Focus on practical guides, case studies, tools, and best practices.
Return ONLY a JSON array of strings. No explanation, no markdown, just the array."""

    usage = await complete_with_usage(
        messages=[{"role": "user", "content": prompt}],
        system=RESEARCHER_CONTEXT,
        fast=True,
        max_tokens=400,
    )
    log_llm_call(usage, service="researchOS", call_type="queries", fast=True)
    text = usage["text"]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        queries = json.loads(text)
        return [q for q in queries if isinstance(q, str)][:n]
    except json.JSONDecodeError:
        logger.warning("[RESEARCHER] Couldn't parse queries JSON, using topic directly")
        return [topic]


# ── Step 2: gather sources ─────────────────────────────────────────────────────

async def _gather_sources(queries: list[str], results_per_query: int = 5) -> list[dict]:
    all_results = []
    for q in queries:
        results = await run_search(q, num_results=results_per_query)
        all_results.extend(results)
    seen, unique = set(), []
    for r in all_results:
        if r["url"] and r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    return unique


async def _scrape_top_sources(sources: list[dict], max_scrape: int = 3) -> list[dict]:
    from systemOS.mcp.browser import scrape_many
    urls = [s["url"] for s in sources[:max_scrape]]
    full_texts = await scrape_many(urls, max_chars=6000)
    enriched = []
    for i, source in enumerate(sources):
        s = dict(source)
        if i < max_scrape and full_texts[i]:
            s["full_content"] = full_texts[i]
        enriched.append(s)
    return enriched


# ── Step 3: synthesise ─────────────────────────────────────────────────────────

async def _synthesise(topic: str, sources: list[dict], sop_hint: Optional[str] = None,
                      max_tokens: int = 4000) -> str:
    context_parts = []
    for i, s in enumerate(sources[:12]):
        content = s.get("full_content") or s.get("content", "")
        if content:
            context_parts.append(
                f"**Source {i+1}: {s['title']}**\nURL: {s['url']}\n{content[:2000]}"
            )
    context = "\n\n---\n\n".join(context_parts) if context_parts else "No search results found."
    structure = sop_hint or DEFAULT_STRUCTURE

    prompt = f"""{structure}

Research topic: "{topic}"

Sources found:
{context}

Write a thorough, practical research report using the structure above.
Be specific — name real tools, give concrete numbers and benchmarks where possible.
Focus on what's actionable for Daniel's specific situation at NNL.
Use markdown formatting."""

    usage = await complete_with_usage(
        messages=[{"role": "user", "content": prompt}],
        system=RESEARCHER_CONTEXT,
        fast=False,
        max_tokens=max_tokens,
    )
    # topic_id not available here — caller logs it separately
    log_llm_call(usage, service="researchOS", call_type="synthesis", fast=False)
    return usage["text"]


# ── Main entry point ───────────────────────────────────────────────────────────

async def research(
    topic: str,
    category: str = "general",
    sop_hint: Optional[str] = None,
    topic_id: Optional[int] = None,
    depth: str = "standard",
    emit=None,
) -> dict:
    """
    Research a topic with checkpointing. Each step is saved so the job can
    resume after a crash, timeout, or LLM failure.
    """
    from systemOS.config.depth import get as get_depth
    cfg = get_depth(depth)

    def _emit(level: str, msg: str):
        logger.info("[RESEARCHER] %s", msg)
        if emit:
            emit(level, msg)

    _emit("info", f"Starting research [{cfg['label']}]: {topic[:70]}")
    logger.info("[RESEARCHER] Starting: %s (id=%s, depth=%s)", topic[:80], topic_id, depth)

    n_queries = cfg["n_queries"]
    n_results = cfg["n_results"]

    # Load existing checkpoint if this topic was partially processed
    cp = (_load_checkpoint(topic_id) if topic_id else None) or {}
    stage = cp.get("stage", "")
    if stage:
        _emit("info", f"Resuming from checkpoint stage: {stage}")

    # ── Step 1: queries ────────────────────────────────────────────
    if stage in ("", None):
        _emit("stage", "queries")
        queries = await _generate_queries(topic, n=n_queries)
        _emit("info", f"Generated {len(queries)} search queries")
        for q in queries:
            _emit("query", f"  › {q}")
        if topic_id:
            _save_checkpoint(topic_id, {"stage": "queries", "queries": queries})
    else:
        queries = cp["queries"]
        _emit("info", f"Resuming — using {len(queries)} saved queries")

    # ── Step 2: search ─────────────────────────────────────────────
    if stage in ("", None, "queries"):
        _emit("stage", "searching")
        sources = await _gather_sources(queries, results_per_query=n_results)
        _emit("info", f"Found {len(sources)} unique sources across {len(queries)} queries")
        if topic_id:
            _save_checkpoint(topic_id, {
                "stage": "searched",
                "queries": queries,
                "sources": sources,
            })
    else:
        sources = cp["sources"]
        _emit("info", f"Resuming — using {len(sources)} saved sources")

    # ── Step 3: scrape ─────────────────────────────────────────────
    if stage in ("", None, "queries", "searched"):
        _emit("stage", "scraping")
        _emit("info", f"Deep-scraping top {cfg['max_scrape']} pages [{cfg['label']}]...")
        sources = await _scrape_top_sources(sources, max_scrape=cfg["max_scrape"])
        scraped = sum(1 for s in sources if s.get("full_content"))
        _emit("info", f"Scraped {scraped} pages successfully")
        if topic_id:
            _save_checkpoint(topic_id, {
                "stage": "scraped",
                "queries": queries,
                "sources": [{"url": s["url"], "title": s["title"],
                             "content": s.get("content", ""),
                             "full_content": s.get("full_content", "")} for s in sources],
            })
    else:
        _emit("info", "Resuming — skipping scrape (checkpoint found)")

    # ── Step 4: synthesise ─────────────────────────────────────────
    if stage in ("", None, "queries", "searched", "scraped"):
        _emit("stage", "synthesising")
        _emit("info", f"Synthesising report (max {cfg['synthesis_tokens']} tokens, depth={cfg['label']})...")
        report = await _synthesise(topic, sources, sop_hint=sop_hint, max_tokens=cfg["synthesis_tokens"])
        _emit("info", f"Report generated — {len(report):,} characters")
        if topic_id:
            _save_checkpoint(topic_id, {
                "stage": "synthesised",
                "queries": queries,
                "sources": [{"url": s["url"], "title": s["title"]} for s in sources],
                "report": report,
            })
    else:
        report = cp["report"]
        _emit("info", "Resuming — using saved report")

    # ── Step 5: save ───────────────────────────────────────────────
    _emit("stage", "saving")
    # Organised output: research/{project_slug_or_category}/{YYYY-MM}/
    base_dir = Path(os.environ.get("RESEARCH_OUTPUT_DIR", "research"))
    month_dir = base_dir / (category or "general") / datetime.now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    safe_name = re.sub(r"[^\w\s-]", "", topic.lower()).strip().replace(" ", "_")[:60]
    output_file = month_dir / f"{timestamp}_{safe_name}.md"

    header = f"# {topic}\n\n*Generated: {datetime.now().strftime('%d %b %Y %H:%M')}*\n\n"
    sources_footer = "\n\n---\n## Sources\n" + "\n".join(
        f"- [{s['title'] or s['url']}]({s['url']})" for s in sources if s.get("url")
    )
    output_file.write_text(header + report + sources_footer)
    logger.info("[RESEARCHER] Saved: %s", output_file)
    _emit("info", f"Saved to file: {output_file.name}")

    # ── Step 6: DB write ───────────────────────────────────────────
    if topic_id:
        try:
            import markdown as _md
            report_html = _md.markdown(report, extensions=["extra", "toc"])
        except Exception:
            report_html = None

        try:
            from db import get_conn
            model_name = os.environ.get("OLLAMA_MODEL") or "claude-sonnet-4-6"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO supply.research_findings
                           (topic_id, report, report_html, model, sources, queries, output_file)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (
                            topic_id, report, report_html, model_name,
                            json.dumps([{"url": s["url"], "title": s["title"]} for s in sources]),
                            json.dumps(queries),
                            str(output_file),
                        ),
                    )
                    cur.execute(
                        "UPDATE supply.research_topics SET status='done', completed_at=NOW() WHERE id=%s",
                        (topic_id,),
                    )
            _clear_checkpoint(topic_id)
            logger.info("[RESEARCHER] DB saved and checkpoint cleared for topic %d", topic_id)
        except Exception as exc:
            logger.error("[RESEARCHER] DB write failed: %s", exc)

    return {
        "topic": topic,
        "report": report,
        "queries": queries,
        "sources": [{"url": s["url"], "title": s["title"]} for s in sources],
        "output_file": str(output_file),
    }


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "procurement KPIs for an inventory manager"
    result = asyncio.run(research(topic))
    print(f"\nReport saved to: {result['output_file']}\n")
    print(result["report"][:500], "...")
