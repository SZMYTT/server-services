"""
Research queue service — picks pending topics from DB, runs researcher agent,
marks them done. Can also be called directly to queue a one-off topic.

Usage:
    # Queue a topic:
    from services.research import queue_topic
    topic_id = queue_topic("procurement KPIs for inventory manager")

    # Process all pending (used by worker):
    from services.research import run_pending
    await run_pending()

    # CLI: python3 services/research.py "your topic here"
"""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from db import get_conn
from agents.researcher import research

logger = logging.getLogger(__name__)


def queue_topic(topic: str, category: str = "general", sop_hint: str | None = None) -> int:
    """Insert a topic into the research queue. Returns the new topic_id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO supply.research_topics (topic, category, sop_hint, status)
                   VALUES (%s, %s, %s, 'pending')
                   ON CONFLICT (topic) DO UPDATE SET status='pending', created_at=NOW()
                   RETURNING id""",
                (topic, category, sop_hint),
            )
            row = cur.fetchone()
            return row[0]


def get_pending_topics() -> list[dict]:
    """Return all topics with status='pending'."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, topic, category, sop_hint
                   FROM supply.research_topics
                   WHERE status = 'pending'
                   ORDER BY created_at""",
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


async def run_pending() -> list[dict]:
    """Process all pending research topics. Returns list of result dicts."""
    topics = get_pending_topics()
    if not topics:
        logger.info("[RESEARCH] No pending topics")
        return []

    results = []
    for t in topics:
        logger.info("[RESEARCH] Processing: %s", t["topic"][:80])
        try:
            result = await research(
                topic=t["topic"],
                category=t.get("category", "general"),
                sop_hint=t.get("sop_hint"),
                topic_id=t["id"],
            )
            results.append(result)
        except Exception as exc:
            logger.error("[RESEARCH] Failed topic %d: %s", t["id"], exc)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE supply.research_topics SET status='error' WHERE id=%s",
                        (t["id"],),
                    )

    return results


def seed_initial_topics():
    """Pre-populate research queue with NNL-relevant topics if queue is empty."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM supply.research_topics")
            count = cur.fetchone()[0]

    if count > 0:
        return

    topics = [
        ("procurement KPIs and metrics for inventory managers at small manufacturers", "procurement"),
        ("reorder point and safety stock calculation methods for seasonal products", "inventory"),
        ("vendor scorecard and supplier performance tracking best practices", "procurement"),
        ("demand forecasting methods for retail with seasonal peaks small business", "forecasting"),
        ("MRP vs kanban vs min-max replenishment for small manufacturer", "inventory"),
        ("Google Sheets automation for inventory management Apps Script", "tools"),
        ("AI tools for procurement and supply chain management SMEs 2024", "automation"),
        ("lead time variability management strategies for fragrance manufacturers", "procurement"),
        ("ABC XYZ analysis for inventory prioritisation retail manufacturer", "inventory"),
        ("cash flow impact of inventory decisions procurement strategy", "procurement"),
        ("shop replenishment optimisation for multi-location retail", "logistics"),
        ("purchase order automation and approval workflows small team", "procurement"),
    ]

    for topic, category in topics:
        queue_topic(topic, category=category)

    logger.info("[RESEARCH] Seeded %d initial topics", len(topics))


if __name__ == "__main__":
    import sys
    import logging as _logging
    _logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                         format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
        print(f"Queueing and researching: {topic}")
        topic_id = queue_topic(topic)
        print(f"Topic ID: {topic_id}")
        result = asyncio.run(research(topic=topic, topic_id=topic_id))
        print(f"\nReport saved: {result['output_file']}")
        print(result["report"][:600], "...")
    else:
        print("Processing pending topics...")
        results = asyncio.run(run_pending())
        print(f"Completed {len(results)} topics")
