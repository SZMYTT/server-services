import asyncio
import logging
import sys
import os

# Add parent directory to path so it can be run as a script correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.queue import get_next_approved_task, set_task_status
from services.router import route_task
from agents.researcher import run_research_task
from agents.comms import run_comms_task
from agents.content import run_content_task
from agents.generic import run_generic_task

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("prisma.orchestrator")

async def agent_runner(task: dict, routing: dict):
    """
    Background task wrapper that routes an executing task to the appropriate agent.
    """
    task_id = task["id"]
    task_type = task["task_type"]
    module = task["module"]
    
    logger.info(f"[ORCHESTRATOR] Starting task {task_id[:8]} of type {task_type}")
    
    try:
        # Route to the appropriate specialist agent, or generic fallback
        if task_type == "research":
            await run_research_task(task, routing)
        elif task_type == "comms":
            await run_comms_task(task, routing)
        elif task_type == "content":
            await run_content_task(task, routing)
        else:
            # Generic agent handles: legal, website, document, auction,
            # finance, and any future module types automatically.
            logger.info(
                "[ORCHESTRATOR] Routing %s to generic agent (module: %s)",
                task_type, module
            )
            await run_generic_task(task, routing)
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Exception in runner for task {task_id}: {e}")
        await set_task_status(task_id, "failed", output=f"Error in runner: {e}")

async def orchestrator_loop():
    logger.info("[ORCHESTRATOR] Starting main loop")
    while True:
        try:
            task = await get_next_approved_task()
            if task:
                # get_next_approved_task atomically sets status to 'running'
                
                # Fetch routing
                routing = await route_task(
                    task_type=task.get("task_type", ""),
                    module=task.get("module"),
                    queue_lane=task.get("queue_lane"),
                    risk_level=task.get("risk_level")
                )
                
                # Fire and forget instead of blocking the polling loop
                asyncio.create_task(agent_runner(task, routing))
            else:
                # No tasks found, wait and try again
                await asyncio.sleep(2)
                
        except Exception as e:
            logger.error(f"[ORCHESTRATOR] Error in loop: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(orchestrator_loop())
    except KeyboardInterrupt:
        logger.info("[ORCHESTRATOR] Shutting down...")
