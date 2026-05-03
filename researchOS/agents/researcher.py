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
from systemOS.services.sop_assembler import assemble_sop

logger = logging.getLogger(__name__)


def _get_research_sop(workspace: str = "nnl", sops_root=None) -> str:
    """Assemble the layered research SOP for the given workspace."""
    return assemble_sop(task_type="research", module="research", workspace=workspace,
                        sops_root=sops_root)


def _format_recall(results: list[dict]) -> str:
    """Format ChromaDB recall hits into a block for the synthesis prompt."""
    if not results:
        return ""
    parts = ["**PRIOR RESEARCH (recalled from Knowledge Ledger):**"]
    for r in results:
        header = f"Topic: {r['topic']}"
        if r.get("section"):
            header += f" — {r['section']}"
        parts.append(f"{header}\n{r['text'][:400]}")
    return "\n\n".join(parts)


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

async def _generate_queries(topic: str, n: int = 4, workspace: str = "nnl",
                           sops_root=None) -> tuple[list[str], dict]:
    prompt = f"""I need to research this topic: "{topic}"

Generate exactly {n} specific, targeted search queries that will find the most useful
information. Focus on practical guides, case studies, tools, and best practices.
Return ONLY a JSON array of strings. No explanation, no markdown, just the array."""

    usage = await complete_with_usage(
        messages=[{"role": "user", "content": prompt}],
        system=_get_research_sop(workspace, sops_root=sops_root),
        fast=True,
        max_tokens=400,
    )
    log_llm_call(usage, service="researchOS", call_type="queries", fast=True)
    text = usage["text"]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        queries = json.loads(text)
        return [q for q in queries if isinstance(q, str)][:n], usage
    except json.JSONDecodeError:
        logger.warning("[RESEARCHER] Couldn't parse queries JSON, using topic directly")
        return [topic], usage


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


# ── Step 3a: standard synthesis ────────────────────────────────────────────────

async def _synthesise(topic: str, sources: list[dict], sop_hint: Optional[str] = None,
                      max_tokens: int = 4000, workspace: str = "nnl",
                      sops_root=None, prior_context: str = "") -> tuple[str, dict]:
    context_parts = []
    for i, s in enumerate(sources[:12]):
        content = s.get("full_content") or s.get("content", "")
        if content:
            context_parts.append(
                f"**Source {i+1}: {s['title']}**\nURL: {s['url']}\n{content[:2000]}"
            )
    new_sources = "\n\n---\n\n".join(context_parts) if context_parts else "No search results found."
    sop = assemble_sop(task_type="research", module="research", workspace=workspace,
                       sops_root=sops_root)

    focus_block = f"\n\n**Research focus:** {sop_hint}" if sop_hint else ""

    # Combine recalled prior knowledge with freshly scraped sources.
    # Prior knowledge appears first so the LLM uses it as a baseline to extend,
    # not as content to repeat.
    if prior_context:
        knowledge_block = (
            f"{prior_context}\n\n"
            f"---\n\n**NEW SOURCES GATHERED TODAY:**\n{new_sources}"
        )
        no_repeat_instruction = (
            "\nDo not repeat information already present in the Prior Research section "
            "unless you are adding new depth, correcting an inaccuracy, or resolving a contradiction."
        )
    else:
        knowledge_block = new_sources
        no_repeat_instruction = ""

    prompt = f"""Research topic: "{topic}"{focus_block}

{knowledge_block}

Write a thorough, practical research report.
Be specific — name real tools, give concrete numbers and benchmarks where possible.
Focus on what's actionable for Daniel's specific situation at NNL.
Use markdown formatting.{no_repeat_instruction}"""

    usage = await complete_with_usage(
        messages=[{"role": "user", "content": prompt}],
        system=sop,
        fast=False,
        max_tokens=max_tokens,
    )
    # topic_id not available here — caller logs it separately
    log_llm_call(usage, service="researchOS", call_type="synthesis", fast=False)
    return usage["text"], usage


# ── Step 3b: expert synthesis (Architect → Auditor → Refiner) ─────────────────

async def _expert_synthesise(
    topic: str,
    sources: list[dict],
    max_tokens: int = 4000,
    workspace: str = "nnl",
    sops_root=None,
    prior_context: str = "",
) -> tuple[str, dict]:
    """
    Three-pass synthesis without any DB writes:
      1. Architect (full model) — writes the research report from sources
      2. Auditor   (fast model) — identifies gaps, unverified claims, coverage holes
      3. Refiner   (full model) — produces the final report addressing the critique

    Adds roughly 1 extra LLM call (the Auditor) relative to standard synthesis.
    Produces a noticeably better structured, more accurate report.
    """
    sop = assemble_sop("research", "research", workspace, sops_root=sops_root)

    # Step 1: Architect — write the initial full report
    draft, arch_usage = await _synthesise(
        topic, sources, max_tokens=max_tokens, workspace=workspace,
        sops_root=sops_root, prior_context=prior_context,
    )
    log_llm_call(arch_usage, service="researchOS", call_type="expert_architect", fast=False)

    # Step 2: Auditor — critique the draft independently
    audit_prompt = (
        f"Research topic: \"{topic}\"\n\n"
        f"Draft report:\n{draft}\n\n"
        "List the 3–5 most important issues: missing evidence, unverified claims, "
        "gaps in coverage, or structural weaknesses. Be specific and brief."
    )
    aud_usage = await complete_with_usage(
        messages=[{"role": "user", "content": audit_prompt}],
        system=sop + "\n\nYou are the Auditor. Find gaps and inaccuracies. Be concise.",
        fast=True,
        max_tokens=600,
    )
    log_llm_call(aud_usage, service="researchOS", call_type="expert_audit", fast=True)

    # Step 3: Refiner — incorporate fixes into the final report
    refine_prompt = (
        f"Research topic: \"{topic}\"\n\n"
        f"Draft:\n{draft}\n\n"
        f"Issues to address:\n{aud_usage['text']}\n\n"
        "Produce the improved final research report in markdown. "
        "Address every issue. Keep all correct content unchanged."
    )
    final_usage = await complete_with_usage(
        messages=[{"role": "user", "content": refine_prompt}],
        system=sop,
        fast=False,
        max_tokens=max_tokens,
    )
    log_llm_call(final_usage, service="researchOS", call_type="expert_refiner", fast=False)
    return final_usage["text"], final_usage


# ── Main entry point ───────────────────────────────────────────────────────────

async def research(
    topic: str,
    category: str = "general",
    sop_hint: Optional[str] = None,
    topic_id: Optional[int] = None,
    depth: str = "standard",
    workspace: str = "nnl",
    emit=None,
    sops_root=None,
    expert_panel: bool = False,
) -> dict:
    """
    Research a topic with checkpointing. Each step is saved so the job can
    resume after a crash, timeout, or LLM failure.
    """
    from systemOS.config.depth import get as get_depth
    cfg = get_depth(depth)

    from systemOS.services.token_tracker import TokenBudget
    budget = TokenBudget(label=f"research_{depth}")

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

    # ── Step 0: recall (Recursive RAG) ────────────────────────────
    # Restore from checkpoint if resuming; run recall only on a fresh start.
    prior_context: str = cp.get("prior_context", "")
    if not prior_context and stage in ("", None):
        try:
            from systemOS.services.shadow_storage import recall
            hits = await recall(topic, project_slug=category or "general", n=4)
            if hits:
                prior_context = _format_recall(hits)
                _emit("info", f"Recalled {len(hits)} relevant sections from past research")
            else:
                _emit("info", "No prior knowledge found — starting fresh")
        except Exception as exc:
            logger.warning("[RESEARCHER] Recall failed (non-fatal): %s", exc)

    # ── Step 1: queries ────────────────────────────────────────────
    if stage in ("", None):
        _emit("stage", "queries")
        queries, q_usage = await _generate_queries(topic, n=n_queries, workspace=workspace,
                                                    sops_root=sops_root)
        budget.track(q_usage, call="queries")
        _emit("info", f"Generated {len(queries)} search queries")
        for q in queries:
            _emit("query", f"  › {q}")
        if topic_id:
            _save_checkpoint(topic_id, {
                "stage": "queries",
                "queries": queries,
                "prior_context": prior_context,
            })
    else:
        queries = cp["queries"]
        _emit("info", f"Resuming — using {len(queries)} saved queries")

    # ── Step 2: search (with thin-source detection) ────────────────
    if stage in ("", None, "queries"):
        _emit("stage", "searching")
        sources = await _gather_sources(queries, results_per_query=n_results)
        _emit("info", f"Found {len(sources)} unique sources across {len(queries)} queries")

        # Thin-source detection: if average snippet is short, the queries likely
        # hit SEO landing pages. Run 2 expansion queries and merge the results.
        if sources:
            avg_len = sum(len(s.get("content", "")) for s in sources) / len(sources)
            if avg_len < 350:
                _emit("info", f"Sources thin (avg {int(avg_len)} chars) — expanding queries…")
                expansion, _ = await _generate_queries(
                    f"Deep technical specifics and primary sources for: {topic}",
                    n=2, workspace=workspace, sops_root=sops_root,
                )
                extra = await _gather_sources(expansion, results_per_query=n_results)
                seen = {s["url"] for s in sources}
                added = 0
                for s in extra:
                    if s["url"] not in seen:
                        sources.append(s)
                        seen.add(s["url"])
                        added += 1
                _emit("info", f"Expansion added {added} new sources (total: {len(sources)})")

        if topic_id:
            _save_checkpoint(topic_id, {
                "stage": "searched",
                "queries": queries,
                "sources": sources,
                "prior_context": prior_context,
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
                "prior_context": prior_context,
                "sources": [{"url": s["url"], "title": s["title"],
                             "content": s.get("content", ""),
                             "full_content": s.get("full_content", "")} for s in sources],
            })
    else:
        _emit("info", "Resuming — skipping scrape (checkpoint found)")

    # ── Step 4: synthesise ─────────────────────────────────────────
    if stage in ("", None, "queries", "searched", "scraped"):
        _emit("stage", "synthesising")
        if prior_context:
            _emit("info", "Injecting prior knowledge into synthesis context…")
        if expert_panel:
            _emit("info", "Expert Panel: Architect writing draft…")
            report, s_usage = await _expert_synthesise(
                topic, sources,
                max_tokens=cfg["synthesis_tokens"], workspace=workspace,
                sops_root=sops_root, prior_context=prior_context,
            )
            _emit("info", "Expert Panel complete — Auditor + Refiner applied")
        else:
            _emit("info", f"Synthesising report (max {cfg['synthesis_tokens']} tokens, depth={cfg['label']})...")
            report, s_usage = await _synthesise(
                topic, sources, sop_hint=sop_hint,
                max_tokens=cfg["synthesis_tokens"], workspace=workspace,
                sops_root=sops_root, prior_context=prior_context,
            )
        budget.track(s_usage, call="synthesis")
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

    # ── Step 7: shadow storage ─────────────────────────────────
    budget.log_summary()
    _emit("stage", "indexing")
    _emit("info", f"Indexing to ChromaDB + Knowledge Ledger... (total tokens: {budget.total:,})")
    try:
        from systemOS.services.shadow_storage import store_research_output
        from db import get_conn as _get_conn
        shadow = await store_research_output(
            topic=topic,
            report_text=report,
            topic_id=topic_id,
            project_slug=category or "general",
            category=category or "general",
            output_file=str(output_file),
            model=os.environ.get("OLLAMA_MODEL", ""),
            depth=depth,
            db_conn_fn=_get_conn,
        )
        _emit("info", f"Indexed {shadow['section_count']} sections to ChromaDB")
        if shadow.get("drive_url"):
            _emit("info", f"Report uploaded to Drive: {shadow['drive_url']}")
        # Write token total into research_index row
        if topic_id:
            try:
                budget.flush_to_column(_get_conn, "supply.research_index", "topic_id", topic_id)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[RESEARCHER] Shadow storage failed (non-fatal): %s", exc)
        shadow = {}

    # ── Step 8: push notification ──────────────────────────────
    try:
        from systemOS.mcp.notify import notify_done
        summary_preview = shadow.get("executive_summary", report[:200]).replace("\n", " ")[:120]
        await notify_done(
            f"{topic[:60]}\n{summary_preview}\n[{budget.total:,} tokens]",
            topic="researchos",
            title=f"Research complete [{cfg['label']}]",
        )
    except Exception:
        pass  # notification is best-effort

    return {
        "topic": topic,
        "report": report,
        "queries": queries,
        "sources": [{"url": s["url"], "title": s["title"]} for s in sources],
        "output_file": str(output_file),
        "executive_summary": shadow.get("executive_summary", ""),
        "drive_url": shadow.get("drive_url"),
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
