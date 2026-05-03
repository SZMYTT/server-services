import asyncio
import logging
import time
import json

from services.sop_assembler import assemble_sop
from services.checkpointer import start_step, complete_step, fail_step
from services.queue import set_task_status, update_module_estimate
from mcp.search import run_search
from systemOS.llm import complete_with_usage, log_llm_call

logger = logging.getLogger("prisma.agents.researcher")

async def run_research_task(task: dict, routing: dict):
    """
    Executes a research task using the researcher pipeline.
    """
    task_id = task["id"]
    task_type = task["task_type"]
    module = task["module"]
    workspace = task["workspace"]
    user_input = task["input"]
    
    start_time = time.monotonic()
    
    try:
        # Step 1: Initialization & SOP Assembly
        step1_id = await start_step(task_id, 1, "initialization", {"action": "assembling SOP"})
        sop = assemble_sop(task_type, module, workspace)
        await complete_step(task_id, 1, {"status": "SOP assembled", "sop_length": len(sop)})
        
        # Step 2: Extract search queries
        step2_id = await start_step(task_id, 2, "plan_queries", {"input": user_input})
        queries_prompt = (
            f"{sop}\n\n"
            f"User Request: {user_input}\n\n"
            "Based on the research methodology, output ONLY a JSON array of 3-5 search queries to run. "
            "Do NOT include any markdown formatting, explanation, or other text. Just the raw JSON array."
        )
        
        queries = []
        try:
            usage = await complete_with_usage(
                messages=[{"role": "user", "content": queries_prompt}],
                fast=True,
                max_tokens=500,
            )
            log_llm_call(usage, service="prisma.researcher", call_type="query_gen")
            raw = usage["text"].strip()
            # Strip accidental markdown fences
            import re
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw, flags=re.MULTILINE).strip()
            parsed = json.loads(raw)
            queries = parsed if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("[RESEARCHER] Failed to parse queries (%s), falling back to raw input", e)
            queries = [user_input]

        
        # Step 3: Run Searches
        step3_id = await start_step(task_id, 3, "web_search", {"queries": queries})
        search_results = []
        for q in queries[:3]: # Limit to top 3 queries to avoid massive context
            if isinstance(q, str):
                res = await run_search(q, num_results=3)
                search_results.extend(res)
        
        # Deduplicate search results by URL
        unique_results = {r['url']: r for r in search_results if r['url']}.values()
        
        await complete_step(task_id, 3, {"results_found": len(unique_results)})
        
        # Step 4: Synthesize Report
        step4_id = await start_step(task_id, 4, "synthesize", {"results_count": len(unique_results)})
        
        context_str = "Search Results:\n\n"
        for i, res in enumerate(list(unique_results)[:7]): # Pass top 7 results
            context_str += f"Source {i+1}: {res['title']} ({res['url']})\n{res['content']}\n\n"
            
        final_prompt = (
            f"{sop}\n\n"
            f"{context_str}\n\n"
            f"User Request: {user_input}\n\n"
            "Write the final research report following the exact output format specified in the SOP."
        )
        
        try:
            usage = await complete_with_usage(
                messages=[{"role": "user", "content": final_prompt}],
                fast=False,
                max_tokens=4000,
            )
            log_llm_call(usage, service="prisma.researcher", call_type="synthesis")
            final_report = usage["text"]
            total_tokens_approx = usage["input_tokens"] + usage["output_tokens"]
        except Exception as e:
            raise Exception(f"Failed to generate final report: {e}") from e

                
        await complete_step(task_id, 4, {"status": "synthesis complete"})
        
        # Mark Task Done
        duration_ms = int((time.monotonic() - start_time) * 1000)
        duration_mins = duration_ms / 60000.0
        
        await set_task_status(
            task_id, 
            "done", 
            output=final_report.strip(), 
            duration_ms=duration_ms,
            tokens_used=total_tokens_approx,
            model=routing["model"],
            tools_called={"searxng": len(queries)}
        )
        
        await update_module_estimate(module, duration_mins)
        logger.info(f"[RESEARCHER] Task {task_id[:8]} completed in {duration_mins:.1f} mins")
        
    except Exception as e:
        logger.error(f"[RESEARCHER] Task {task_id} failed: {e}")
        await set_task_status(task_id, "failed", output=f"Error: {str(e)}")
