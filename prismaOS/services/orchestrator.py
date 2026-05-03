import asyncio
import logging
import sys
import os

# prismaOS/ and server-services/ both need to be on the path
_services_dir = os.path.dirname(os.path.abspath(__file__))   # .../prismaOS/services
_prisma_root  = os.path.dirname(_services_dir)                # .../prismaOS
_server_root  = os.path.dirname(_prisma_root)                 # .../server-services
sys.path.insert(0, _prisma_root)
sys.path.insert(0, _server_root)

from services.queue import get_next_approved_task, set_task_status, add_task, get_conn
from services.router import route_task
from agents.researcher_bridge import run_research_task
from agents.comms import run_comms_task
from agents.content import run_content_task
from agents.generic import run_generic_task
from agents.graph_indexer import run_graph_indexer_task
from agents.web_operator import run_web_operator_task
# Import the fully implemented agents from systemOS
from systemOS.agents.mapmaker import build_map
from systemOS.services.expert_panel import expert_panel_runner

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("prisma.orchestrator")

async def run_thorough_research(task: dict, routing: dict):
    """
    1. Calls a Mapmaker agent to generate a structural JSON curriculum.
    2. Uses asyncio.gather to spawn parallel Expert Panel tasks for each chapter.
    3. Aggregates results into a single master document before final synthesis.
    """
    task_id = task["id"]
    topic = task.get("input", "")
    logger.info(f"[ORCHESTRATOR] Running thorough research for task {task_id}")
    
    try:
        await set_task_status(task_id, "running", output="[Mapmaker] Deconstructing topic into volumes...")
        
        # 1. Mapmaker deconstructs the topic
        map_result = await build_map(topic)
        chapters = map_result.chapter_queries(priority_filter="high")
        if not chapters:
            chapters = [topic]  # Fallback to a single topic if extraction yields nothing
            
        await set_task_status(task_id, "running", output=f"[Mapmaker] Created {len(chapters)} high-priority chapters. Spawning Expert Panels...")
        
        # 2. Expert Panel processes chapters in parallel (VRAM Semaphore handled internally)
        panel_tasks = []
        for chapter_query in chapters:
            sub_task = task.copy()
            sub_task["input"] = f"Original Topic: {topic}\nChapter Focus: {chapter_query}"
            panel_tasks.append(expert_panel_runner(sub_task, routing))
            
        results = await asyncio.gather(*panel_tasks, return_exceptions=True)
        
        # 3. Final synthesis
        final_outputs = []
        total_tokens = 0
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error("[ORCHESTRATOR] Expert panel failed for chapter %d: %s", i, res)
                final_outputs.append(f"## Chapter {i+1}: Error\nFailed to generate content.")
            else:
                final_outputs.append(f"## Chapter {i+1}\n{res.final_output}")
                total_tokens += getattr(res, "tokens_used", 0)
                
        master_document = f"# Thorough Research Report: {topic}\n\n" + "\n\n".join(final_outputs)
        
        await set_task_status(
            task_id, "done", output=master_document,
            tokens_used=total_tokens, model=routing.get("model", "unknown")
        )
        logger.info(f"[ORCHESTRATOR] Thorough research complete for task {task_id}")
        
    except Exception as e:
        logger.error(f"[ORCHESTRATOR] Thorough research failed: {e}")
        await set_task_status(task_id, "failed", output=f"Thorough research error: {e}")

async def agent_runner(task: dict, routing: dict):
    """
    Background task wrapper that routes an executing task to the appropriate agent.
    """
    task_id = task["id"]
    task_type = task["task_type"]
    module = task["module"]
    
    logger.info(f"[ORCHESTRATOR] Starting task {task_id[:8]} of type {task_type}")
    
    # Task types that should NOT trigger the post-process graph indexer
    _NO_INDEX_TYPES = {"graph_indexer", "acquire_skill"}

    try:
        # Route to the appropriate specialist agent, or generic fallback
        if task_type == "research":
            if task.get("depth") == "thorough":
                await run_thorough_research(task, routing)
            else:
                await run_research_task(task, routing)
        elif task_type == "comms":
            await run_comms_task(task, routing)
        elif task_type == "content":
            await run_content_task(task, routing)
        elif task_type == "graph_indexer":
            await run_graph_indexer_task(task, routing)
            return  # Indexer never triggers itself
        elif task_type == "web_operation":
            from systemOS.mcp.browser import InteractiveBrowser
            logger.info("[ORCHESTRATOR] Initializing InteractiveBrowser for task %s", task_id)
            browser = InteractiveBrowser(headless=False)
            await browser.start()
            try:
                await run_web_operator_task(task, routing, browser)
            finally:
                await browser.close()
        elif task_type == "code":
            from systemOS.agents.coder import code_task
            from pathlib import Path
            # Resolve project root from module so AGENTS.md + file map are injected
            _PROJECT_ROOTS = {
                "prismaOS":   Path("/home/szmyt/server-services/prismaOS"),
                "systemOS":   Path("/home/szmyt/server-services/systemOS"),
                "researchOS": Path("/home/szmyt/server-services/researchOS"),
                "fitOS":      Path("/home/szmyt/server-services/fitOS"),
                "nnlos":      Path("/home/szmyt/server-services/nnlos"),
            }
            project_root = _PROJECT_ROOTS.get(task.get("module", ""), None)
            logger.info(
                "[ORCHESTRATOR] code task — project_root=%s model=%s",
                project_root, routing.get("model")
            )
            result = await code_task(
                task=task.get("input", ""),
                project_root=project_root,
                context=task.get("module", ""),
                max_retries=3,
                model=routing.get("model"),
            )
            status = "done" if result.passed else "failed"
            output = (
                result.code if result.passed
                else f"Tests failed after {result.iterations} attempts:\n"
                     + (result.test_outputs[-1] if result.test_outputs else "no output")
            )
            await set_task_status(
                task_id, status, output=output,
                tokens_used=result.tokens_total, model=result.model
            )
        elif task_type == "acquire_skill":
            from systemOS.agents.skill_builder import acquire_skill
            from pathlib import Path
            result = await acquire_skill(
                source=task.get("input", ""),
                tool_name=task.get("module") or None,
                output_dir=Path("/home/szmyt/server-services/systemOS/mcp"),
                sop_dir=Path("/home/szmyt/server-services/systemOS/sops/modules"),
            )
            if result.error:
                await set_task_status(task_id, "failed", output=f"Skill acquisition failed: {result.error}")
            else:
                await set_task_status(
                    task_id, "done",
                    output=(
                        f"Skill '{result.tool_name}' acquired successfully.\n"
                        f"Capabilities: {', '.join(result.capabilities)}\n"
                        f"Wrapper: {result.wrapper_path}\n"
                        f"SOP: {result.sop_path}\n"
                        f"Sandbox: {'✅ passed' if result.sandbox_ok else '⚠️ failed — check wrapper manually'}"
                    )
                )
        else:
            # Generic agent handles: legal, website, document, auction,
            # finance, and any future module types automatically.
            logger.info(
                "[ORCHESTRATOR] Routing %s to generic agent (module: %s)",
                task_type, module
            )
            await run_generic_task(task, routing)
            
        # --- POST-PROCESS TRIGGER: GRAPH INDEXER ---
        # Harvest data by passing the completed agent's output to the indexer sub-task
        output_text = None
        try:
            async with get_conn() as conn:
                # Handles both psycopg3 / aiopg styles and asyncpg dynamically 
                if hasattr(conn, 'cursor'):
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT output, status FROM tasks WHERE id = %s", (task_id,))
                        row = await cur.fetchone()
                        if row and row[1] == "done":  # row[1] is status, row[0] is output
                            output_text = row[0]
                else:
                    row = await conn.fetchrow("SELECT output, status FROM tasks WHERE id = $1", task_id)
                    if row and row["status"] == "done":
                        output_text = row["output"]
        except Exception as db_err:
            logger.error(f"[ORCHESTRATOR] Could not fetch task output for indexer: {db_err}")

        if output_text:
            logger.info(f"[ORCHESTRATOR] Spawning background graph_indexer for {task_id[:8]}")
            await add_task(
                workspace=task.get("workspace", "operator"),
                user="system",
                task_type="graph_indexer",
                risk_level="internal",
                module="system",
                input=output_text,
                trigger_type="system_chained"
            )

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
