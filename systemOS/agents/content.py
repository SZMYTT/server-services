import asyncio
import logging
import time
import httpx
import json

from services.sop_assembler import assemble_sop
from services.checkpointer import start_step, complete_step, fail_step
from services.queue import set_task_status, update_module_estimate
from mcp.facebook import fetch_page_mentions, publish_post

logger = logging.getLogger("prisma.agents.content")

async def run_content_task(task: dict, routing: dict):
    task_id = task["id"]
    task_type = task["task_type"]
    module = task["module"]
    workspace = task["workspace"]
    user_input = task["input"]
    
    start_time = time.monotonic()
    
    try:
        step1_id = await start_step(task_id, 1, "initialization", {"action": "assembling SOP"})
        sop = assemble_sop(task_type, module, workspace)
        await complete_step(task_id, 1, {"status": "SOP assembled", "sop_length": len(sop)})
        
        # Action mode: if input dictates sending/publishing
        if "Publish approved post" in user_input or "Execute publish" in user_input:
            step2_id = await start_step(task_id, 2, "publish_post", {"input": user_input})
            await publish_post(workspace, user_input)
            
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await set_task_status(
                task_id, 
                "done", 
                output=f"Post successfully published to Facebook.\n\nInput explicitly processed: {user_input}", 
                duration_ms=duration_ms,
                model=routing["model"],
                tools_called={"facebook": 1}
            )
            return

        # Generation mode
        step2_id = await start_step(task_id, 2, "generate_content", {"input": user_input})
        
        prompt = (
            f"{sop}\n\n"
            f"User Request: {user_input}\n\n"
            "Please generate high quality social media or marketing content following the exact guidelines and brand voice."
        )
        
        async with httpx.AsyncClient(timeout=routing["timeout_secs"]) as client:
            res = await client.post(
                f"{routing['ollama_url']}/api/generate",
                json={
                    "model": routing["model"],
                    "prompt": prompt,
                    "stream": False
                }
            )
            if res.status_code == 200:
                draft = res.json().get("response", "Error generating content")
            else:
                raise Exception(f"Failed to generate content. Status: {res.status_code}")

        await complete_step(task_id, 2, {"status": "content generated"})
        
        final_output = f"**Generated Content Draft for {workspace}**:\n\n{draft}\n---\n"
        
        duration_ms = int((time.monotonic() - start_time) * 1000)
        
        await set_task_status(
            task_id, 
            "done", 
            output=final_output.strip(), 
            duration_ms=duration_ms,
            model=routing["model"]
        )
        
        duration_mins = duration_ms / 60000.0
        await update_module_estimate(module, duration_mins)
        
    except Exception as e:
        logger.error(f"[CONTENT] Task {task_id} failed: {e}")
        await set_task_status(task_id, "failed", output=f"Error: {str(e)}")
