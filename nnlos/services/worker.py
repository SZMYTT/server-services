"""
NNLOS background worker — runs the ingestion pipeline on a schedule.
Triggered by systemd (nnlos-worker.service) or run directly for testing.
"""

import logging
import os
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nnlos.worker")


def sync_job():
    from services.ingestion import run
    logger.info("Starting scheduled sync")
    results = run()
    for r in results:
        if r["status"] == "success":
            logger.info("  ✓ %-20s %d rows  (%s)", r["type"], r["rows"], r.get("file", ""))
        elif r["status"] == "skipped":
            logger.debug("  – %-20s no file", r["type"])
        else:
            logger.error("  ✗ %-20s %s", r["type"], r.get("error", "failed"))


if __name__ == "__main__":
    interval = int(os.environ.get("SYNC_INTERVAL_MINUTES", "15"))
    logger.info("NNLOS worker starting — sync every %d minutes", interval)

    # Run once immediately on startup
    sync_job()

    scheduler = BlockingScheduler()
    scheduler.add_job(sync_job, "interval", minutes=interval)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped")
