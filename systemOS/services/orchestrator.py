import asyncio
import logging
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.queue import get_next_approved_task, set_task_status
from services.router import route_task
from agents.researcher import run_research_task
from agents.comms import run_comms_task
from agents.content import run_content_task
from agents.generic import run_generic_task
from services.expert_panel import expert_panel_runner
from config.models import should_use_expert_panel

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("prisma.orchestrator")


async def agent_runner(task: dict, routing: dict):
    """
    Route an approved task to the appropriate agent or the Expert Panel.

    Expert Panel is triggered when:
      - task.risk_level is "high", "financial", or "critical"
      - task.routing_type is "expert_panel"

    Standard routing handles everything else by task_type.
    """
    task_id = task["id"]
    task_type = task["task_type"]
    module = task.get("module", "")

    # ── Expert Panel path ──────────────────────────────────────
    if should_use_expert_panel(task):
        logger.info(
            "[ORCHESTRATOR] Routing task %s to Expert Panel (risk=%s routing_type=%s)",
            task_id[:8], task.get("risk_level"), task.get("routing_type"),
        )
        await expert_panel_runner(task, routing)
        return

    # ── Standard agent path ────────────────────────────────────
    logger.info("[ORCHESTRATOR] Starting task %s — type=%s module=%s", task_id[:8], task_type, module)

    try:
        if task_type == "research":
            await run_research_task(task, routing)
        elif task_type == "comms":
            await run_comms_task(task, routing)
        elif task_type == "content":
            await run_content_task(task, routing)
        else:
            logger.info("[ORCHESTRATOR] Routing %s to generic agent (module: %s)", task_type, module)
            await run_generic_task(task, routing)
    except Exception as e:
        logger.error("[ORCHESTRATOR] Exception in runner for task %s: %s", task_id, e)
        await set_task_status(task_id, "failed", output=f"Error in runner: {e}")


async def orchestrator_loop():
    logger.info("[ORCHESTRATOR] Starting main loop")
    while True:
        try:
            task = await get_next_approved_task()
            if task:
                routing = await route_task(
                    task_type=task.get("task_type", ""),
                    module=task.get("module"),
                    queue_lane=task.get("queue_lane"),
                    risk_level=task.get("risk_level"),
                )
                asyncio.create_task(agent_runner(task, routing))
            else:
                await asyncio.sleep(2)
        except Exception as e:
            logger.error("[ORCHESTRATOR] Error in loop: %s", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(orchestrator_loop())
    except KeyboardInterrupt:
        logger.info("[ORCHESTRATOR] Shutting down…")
