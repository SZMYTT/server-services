"""
Research bridge — delegates prismaOS research tasks to the researchOS pipeline.

prismaOS tasks call run_research_task() here instead of the thin local researcher.
The full researchOS pipeline runs: search → scrape → synthesise → checkpoint →
shadow storage (ChromaDB) → Drive upload → push notification.

The bridge translates the prismaOS task dict into a researchOS.agents.researcher.research()
call, then writes the final report back into the prismaOS tasks table.

Path setup: researchOS/ is added to sys.path here so its local imports (db.py)
resolve correctly when called from the prismaOS process.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

# Ensure researchOS/ is importable — its agents do `from db import get_conn`
_research_root = Path(__file__).parent.parent.parent / "researchOS"
if str(_research_root) not in sys.path:
    sys.path.insert(0, str(_research_root))

from services.queue import set_task_status, update_module_estimate

logger = logging.getLogger("prisma.researcher_bridge")

# The prismaOS sops/ directory — passed to the researchOS researcher so it
# picks up workspace-specific Layer 3 profiles (candles, property, cars, etc.)
_PRISMA_SOPS = Path(__file__).parent.parent / "sops"

# prismaOS workspace names that have a direct equivalent in systemOS/sops/workspaces/.
# Anything not listed here uses the prismaOS sops root so the workspace profile is found.
_SYSTEM_WORKSPACES = {"nnl", "operator"}


async def run_research_task(task: dict, routing: dict) -> None:
    """
    Delegate a prismaOS research task to the full researchOS pipeline.

    Gains over the old local researcher:
      - Checkpointing (resumes after crash/timeout at the last completed stage)
      - Deep-scraping (full page text via Crawl4AI, not just search snippets)
      - Shadow storage (ChromaDB vector index + Knowledge Ledger)
      - Token tracking and push notification on completion
    """
    from agents.researcher import research  # researchOS/agents/researcher.py

    task_id = task["id"]
    workspace = task.get("workspace", "operator")
    user_input = task.get("input", "")
    depth = task.get("depth", "standard")
    module = task.get("module", "research")

    # Use the prismaOS sops root for non-system workspaces so workspace profiles
    # (candles, property, cars, etc.) are found in Layer 3 of the assembled SOP.
    sops_root = None if workspace in _SYSTEM_WORKSPACES else _PRISMA_SOPS

    start = time.monotonic()
    await set_task_status(task_id, "running", output="[Research] Starting pipeline...")

    def _emit(level: str, msg: str) -> None:
        logger.info("[BRIDGE:%s] %s: %s", task_id[:8], level, msg)
        # Fire-and-forget status update so the web UI shows live progress.
        # We're inside an async context so create_task is safe here.
        if level in ("stage", "info"):
            try:
                asyncio.get_event_loop().create_task(
                    set_task_status(task_id, "running", output=f"[{level.upper()}] {msg}")
                )
            except RuntimeError:
                pass  # no running loop in test context

    try:
        result = await research(
            topic=user_input,
            category=workspace,
            depth=depth,
            workspace=workspace,
            emit=_emit,
            sops_root=sops_root,
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        await set_task_status(
            task_id,
            "done",
            output=result["report"],
            duration_ms=duration_ms,
            model=routing.get("model", ""),
        )
        await update_module_estimate(module, duration_ms / 60000.0)
        logger.info(
            "[BRIDGE] Task %s done in %.1f min — %d chars, %d sources",
            task_id[:8], duration_ms / 60000.0,
            len(result["report"]), len(result.get("sources", [])),
        )

    except Exception as exc:
        logger.error("[BRIDGE] Task %s failed: %s", task_id[:8], exc, exc_info=True)
        await set_task_status(task_id, "failed", output=f"Research pipeline error: {exc}")
