"""
Shadow Storage — three-tier persistence for research output.

Prevents the LLM's context from being bloated by full report text.
The system "knows" research was done (Ledger) and can "recall" facts
(ChromaDB) without ever loading a full 5000-word report.

Tier 1 — Semantic Index (ChromaDB):
    Each report section is embedded and stored.
    Later, memory.search("supplier lead times UK") returns the 3 most
    relevant paragraphs across all past research — not the whole report.

Tier 2 — Knowledge Ledger (PostgreSQL supply.research_index):
    One row per completed report: topic, executive summary, section count,
    ChromaDB collection name, Drive URL (if uploaded), local file path.

Tier 3 — Long-Read (Google Drive, optional):
    Full report uploaded as Markdown. Human-readable only — the LLM
    never reads this file. Only runs if GOOGLE_SERVICE_ACCOUNT_FILE is set.

Import from any project:
    from systemOS.services.shadow_storage import store_research_output

Usage (called automatically by researchOS researcher.py after synthesis):
    result = await store_research_output(
        topic="gym fitness and health",
        report_text=full_report_markdown,
        topic_id=14,
        project_slug="general",
        category="general",
        output_file="/path/to/report.md",
        model="gemma4:26b",
        depth="standard",
        db_conn_fn=get_conn,   # pass the project's get_conn function
    )
    print(result["executive_summary"])
    print(result["drive_url"])         # None if Drive not configured
    print(result["section_count"])     # sections stored in ChromaDB
"""

import logging
import os
import re
from typing import Callable

logger = logging.getLogger(__name__)

CHROMA_COLLECTION_PREFIX = "researchos_"
DRIVE_FOLDER_ID = os.getenv("RESEARCH_DRIVE_FOLDER_ID", "")


def _split_sections(report_text: str) -> list[dict]:
    """
    Split a Markdown report into sections by ## headings.
    Returns list of {heading, content} dicts.
    """
    pattern = re.compile(r'^(#{1,3} .+)$', re.MULTILINE)
    splits = list(pattern.finditer(report_text))

    if not splits:
        return [{"heading": "Report", "content": report_text}]

    sections = []
    for i, match in enumerate(splits):
        heading = match.group(1).lstrip('#').strip()
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(report_text)
        content = report_text[start:end].strip()
        if content:
            sections.append({"heading": heading, "content": content})

    return sections


async def _generate_executive_summary(topic: str, report_text: str) -> str:
    """Generate a 3-5 sentence executive summary using the fast model."""
    try:
        from systemOS.llm import complete
        prompt = (
            f'Research topic: "{topic}"\n\n'
            f'Report (first 3000 chars):\n{report_text[:3000]}\n\n'
            'Write a 3-5 sentence executive summary of the most important findings. '
            'Be specific — include key numbers, tools, or actions mentioned. '
            'British English. No preamble, start directly with the summary.'
        )
        summary = await complete(
            messages=[{"role": "user", "content": prompt}],
            fast=True,
            max_tokens=300,
        )
        return summary.strip()
    except Exception as e:
        logger.warning("[SHADOW] Executive summary generation failed: %s", e)
        # Fallback: first 500 chars of the report
        return report_text[:500].strip() + "…"


async def _embed_sections(
    sections: list[dict],
    topic: str,
    topic_id: int | None,
    project_slug: str,
    collection: str,
) -> int:
    """Upsert each section into ChromaDB. Returns number of sections stored."""
    try:
        from systemOS.mcp.memory import upsert
        stored = 0
        for i, section in enumerate(sections):
            doc_id = f"t{topic_id or 0}_s{i}_{section['heading'][:30].replace(' ', '_')}"
            await upsert(
                collection=collection,
                doc_id=doc_id,
                text=f"{section['heading']}\n\n{section['content']}",
                metadata={
                    "topic": topic,
                    "topic_id": topic_id or 0,
                    "project": project_slug,
                    "section": section["heading"],
                    "section_index": i,
                },
            )
            stored += 1
        logger.info("[SHADOW] Embedded %d sections into ChromaDB collection=%s", stored, collection)
        return stored
    except Exception as e:
        logger.error("[SHADOW] ChromaDB embedding failed: %s", e)
        return 0


async def _upload_to_drive(
    topic: str,
    report_text: str,
    output_file: str,
) -> str | None:
    """Upload report to Google Drive. Returns view URL or None if not configured."""
    if not DRIVE_FOLDER_ID and not os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"):
        logger.debug("[SHADOW] Drive upload skipped — not configured")
        return None
    try:
        from systemOS.mcp.drive import create_file
        import re as _re
        safe = _re.sub(r"[^\w\s-]", "", topic.lower()).strip().replace(" ", "_")[:60]
        filename = f"research_{safe}.md"
        file_id = await create_file(
            name=filename,
            content=report_text,
            folder_id=DRIVE_FOLDER_ID or None,
            mime_type="text/markdown",
        )
        if file_id:
            url = f"https://drive.google.com/file/d/{file_id}/view"
            logger.info("[SHADOW] Uploaded to Drive: %s", url)
            return url
    except Exception as e:
        logger.warning("[SHADOW] Drive upload failed: %s", e)
    return None


def _write_to_ledger(
    db_conn_fn: Callable,
    topic: str,
    topic_id: int | None,
    project_slug: str,
    category: str,
    executive_summary: str,
    section_count: int,
    collection: str,
    drive_url: str | None,
    output_file: str,
    model: str,
    depth: str,
) -> None:
    """Write one row to supply.research_index."""
    try:
        with db_conn_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO supply.research_index
                       (topic_id, topic, project_slug, category, executive_summary,
                        section_count, chroma_collection, drive_url, output_file, model, depth)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (topic_id, topic, project_slug, category, executive_summary,
                     section_count, collection, drive_url, output_file, model, depth),
                )
        logger.info("[SHADOW] Knowledge Ledger updated for topic_id=%s", topic_id)
    except Exception as e:
        logger.error("[SHADOW] Ledger write failed: %s", e)


async def store_research_output(
    topic: str,
    report_text: str,
    topic_id: int | None = None,
    project_slug: str = "general",
    category: str = "general",
    output_file: str = "",
    model: str = "",
    depth: str = "standard",
    db_conn_fn: Callable | None = None,
) -> dict:
    """
    Run all three shadow storage tiers for a completed research report.

    Returns:
        {
            executive_summary: str,
            section_count: int,
            collection: str,
            drive_url: str | None,
        }
    """
    collection = f"{CHROMA_COLLECTION_PREFIX}{project_slug}"

    # Run all three tiers — failures in one don't block others
    sections = _split_sections(report_text)

    executive_summary, section_count, drive_url = await _gather_storage_results(
        topic, report_text, sections, topic_id, project_slug, collection, output_file
    )

    if db_conn_fn:
        _write_to_ledger(
            db_conn_fn=db_conn_fn,
            topic=topic,
            topic_id=topic_id,
            project_slug=project_slug,
            category=category,
            executive_summary=executive_summary,
            section_count=section_count,
            collection=collection,
            drive_url=drive_url,
            output_file=output_file,
            model=model,
            depth=depth,
        )

    return {
        "executive_summary": executive_summary,
        "section_count": section_count,
        "collection": collection,
        "drive_url": drive_url,
    }


async def _gather_storage_results(
    topic, report_text, sections, topic_id, project_slug, collection, output_file
):
    """Run summary generation, ChromaDB embedding, and Drive upload concurrently."""
    import asyncio
    summary_task = asyncio.create_task(_generate_executive_summary(topic, report_text))
    embed_task = asyncio.create_task(_embed_sections(sections, topic, topic_id, project_slug, collection))
    drive_task = asyncio.create_task(_upload_to_drive(topic, report_text, output_file))

    executive_summary, section_count, drive_url = await asyncio.gather(
        summary_task, embed_task, drive_task, return_exceptions=True
    )

    # Safely handle any tier that threw an exception
    if isinstance(executive_summary, Exception):
        executive_summary = report_text[:500]
    if isinstance(section_count, Exception):
        section_count = 0
    if isinstance(drive_url, Exception):
        drive_url = None

    return executive_summary, section_count, drive_url


async def recall(
    query: str,
    project_slug: str = "general",
    n: int = 5,
) -> list[dict]:
    """
    Semantic recall — find the most relevant past research sections for a query.
    Returns list of {section, topic, text, score} dicts.

    Usage in an agent:
        past = await recall("UK fragrance supplier lead times", project_slug="nnl-supply-chain")
        for r in past:
            print(r["topic"], r["section"], r["text"][:200])
    """
    from systemOS.mcp.memory import search
    collection = f"{CHROMA_COLLECTION_PREFIX}{project_slug}"
    results = await search(collection, query, n=n)
    return [
        {
            "topic": r["metadata"].get("topic", ""),
            "section": r["metadata"].get("section", ""),
            "text": r["text"],
            "score": r["score"],
        }
        for r in results
    ]
