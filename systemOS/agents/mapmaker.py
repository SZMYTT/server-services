"""
Mapmaker agent — decomposes a broad topic into a structured research map.

Before running a deep research session, call the Mapmaker to get a structured
JSON breakdown of volumes and chapters. Each chapter becomes a focused research
topic that runs through the standard researcher pipeline.

Import from any project:
    from systemOS.agents.mapmaker import build_map, MapResult, expand_topic

Usage:
    # Decompose a broad topic
    result = await build_map("gym fitness and health")
    for volume in result.volumes:
        print(volume["title"])
        for chapter in volume["chapters"]:
            print(f"  → {chapter['title']} [{chapter['priority']}]")

    # Get just the flat list of chapter queries to queue
    queries = result.chapter_queries(priority_filter="high")
    # → ["beginner strength training periodisation...", ...]

    # Full expand_topic — queues all chapters as research topics in researchOS DB
    # (used by the web UI's "Deep Research" button)
    await expand_topic(
        topic="gym fitness and health",
        project_id=5,
        db_conn_fn=get_conn,
        depth="standard",
    )
"""

import json
import logging
import re
from dataclasses import dataclass, field

from systemOS.llm import complete
from systemOS.config.models import get_model

logger = logging.getLogger(__name__)

_MAPMAKER_SYSTEM = """You are the Mapmaker. Decompose research topics into structured JSON maps.
Return ONLY valid JSON. No explanation, no markdown fences, no preamble.
Follow the exact schema provided in your instructions."""


@dataclass
class MapResult:
    topic: str
    volumes: list[dict] = field(default_factory=list)
    total_chapters: int = 0
    raw: str = ""

    def chapter_queries(self, priority_filter: str | None = None) -> list[str]:
        """Return flat list of research_query strings, optionally filtered by priority."""
        queries = []
        for vol in self.volumes:
            for ch in vol.get("chapters", []):
                if priority_filter and ch.get("priority") != priority_filter:
                    continue
                queries.append(ch["research_query"])
        return queries

    def all_chapters(self) -> list[dict]:
        """Return flat list of all chapter dicts."""
        chapters = []
        for vol in self.volumes:
            for ch in vol.get("chapters", []):
                ch["volume"] = vol["title"]
                chapters.append(ch)
        return chapters

    def high_priority_first(self) -> list[dict]:
        """Return all chapters sorted: high → medium → low."""
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(self.all_chapters(), key=lambda c: order.get(c.get("priority", "low"), 2))


async def build_map(
    topic: str,
    model: str | None = None,
) -> MapResult:
    """
    Decompose a broad topic into a structured research map.
    Returns a MapResult with volumes and chapters.
    """
    if model is None:
        model = get_model("mapmaker")["model"]

    from systemOS.services.sop_assembler import assemble_sop
    sop = assemble_sop(task_type="research", module="mapmaker", workspace="")

    prompt = (
        f'Decompose this research topic into a structured map:\n\n"{topic}"\n\n'
        f'Return ONLY the JSON object as specified in your instructions. '
        f'No markdown, no preamble, start with {{ and end with }}.'
    )

    logger.info("[MAPMAKER] Decomposing topic: %s", topic[:80])
    raw = await complete(
        messages=[{"role": "user", "content": prompt}],
        system=sop,
        fast=False,
        model=model,
    )

    # Extract JSON object even if there is preamble or postamble
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        raw_clean = match.group(0)
    else:
        # Fallback to the old strip method just in case
        raw_clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()

    try:
        data = json.loads(raw_clean)
        result = MapResult(
            topic=data.get("topic", topic),
            volumes=data.get("volumes", []),
            total_chapters=data.get("total_chapters", 0),
            raw=raw_clean,
        )
        # Recalculate total if not provided
        if not result.total_chapters:
            result.total_chapters = sum(len(v.get("chapters", [])) for v in result.volumes)

        logger.info(
            "[MAPMAKER] Map built: %d volumes, %d chapters",
            len(result.volumes), result.total_chapters,
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("[MAPMAKER] JSON parse failed: %s\nRaw: %s", e, raw_clean[:300])
        # Fallback: treat the whole topic as one chapter
        return MapResult(
            topic=topic,
            volumes=[{
                "title": "Research",
                "description": topic,
                "chapters": [{"title": topic, "research_query": topic,
                              "priority": "high", "estimated_depth": "standard"}],
            }],
            total_chapters=1,
            raw=raw_clean,
        )


async def expand_topic(
    topic: str,
    project_id: int,
    db_conn_fn,
    depth: str = "standard",
    priority_filter: str | None = None,
    model: str | None = None,
) -> MapResult:
    """
    Build a map and queue all chapters as research topics in the DB.
    High-priority chapters run at their specified depth; others at 'quick'.

    Returns the MapResult so the caller can see what was queued.
    """
    result = await build_map(topic, model=model)
    chapters = result.high_priority_first()

    if priority_filter:
        chapters = [c for c in chapters if c.get("priority") == priority_filter]

    queued = 0
    with db_conn_fn() as conn:
        with conn.cursor() as cur:
            # Insert parent "map" topic as a placeholder
            cur.execute(
                """INSERT INTO supply.research_topics
                   (project_id, topic, category, sop_hint, status, depth)
                   VALUES (%s, %s, 'map', %s, 'map', %s)
                   ON CONFLICT (project_id, topic) DO NOTHING""",
                (project_id, f"[MAP] {topic}", f"Decomposed into {result.total_chapters} chapters", depth),
            )
            # Queue each chapter
            for ch in chapters:
                chapter_depth = ch.get("estimated_depth", depth)
                cur.execute(
                    """INSERT INTO supply.research_topics
                       (project_id, topic, category, sop_hint, status, depth)
                       VALUES (%s, %s, %s, %s, 'pending', %s)
                       ON CONFLICT (project_id, topic) DO NOTHING""",
                    (
                        project_id,
                        ch["research_query"],
                        ch.get("volume", "general"),
                        f"Chapter: {ch['title']}",
                        chapter_depth,
                    ),
                )
                queued += 1

    logger.info("[MAPMAKER] Queued %d chapters for project_id=%d", queued, project_id)
    return result
