import asyncio
import logging
import time
import httpx

from services.sop_assembler import assemble_sop
from services.checkpointer import start_step, complete_step, fail_step
from services.queue import set_task_status, update_module_estimate
from mcp.etsy import fetch_unread_messages as etsy_fetch, send_reply as etsy_send
from mcp.gmail import fetch_unread_emails as gmail_fetch, send_email as gmail_send

logger = logging.getLogger("prisma.agents.comms")

async def run_comms_task(task: dict, routing: dict):
    task_id = task["id"]
    task_type = task["task_type"]
    module = task["module"]
    workspace = task["workspace"]
    user_input = task["input"]
    
    start_time = time.monotonic()
    
    try:
        # Step 1: Initialization & SOP
        step1_id = await start_step(task_id, 1, "initialization", {"action": "assembling SOP"})
        sop = assemble_sop(task_type, module, workspace)
        await complete_step(task_id, 1, {"status": "SOP assembled", "sop_length": len(sop)})
        
        # Determine if this is fetch & draft, or an action to send
        if "Send generated reply" in user_input or "Send custom reply" in user_input:
            # Action mode
            step2_id = await start_step(task_id, 2, "send_reply", {"input": user_input})
            if workspace == "candles":
                await etsy_send("msg_stub", user_input)
                platform = "Etsy"
            else:
                await gmail_send("customer@stub.com", "Re: Your message", user_input)
                platform = "Gmail"
            
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await set_task_status(
                task_id, 
                "done", 
                output=f"Reply successfully sent to {platform}.\n\nInput explicitly processed: {user_input}", 
                duration_ms=duration_ms,
                model=routing["model"]
            )
            return

        # Fetch mode
        step2_id = await start_step(task_id, 2, "fetch_messages", {})
        if workspace == "candles":
            messages = await etsy_fetch()
            platform = "Etsy"
        else:
            messages = await gmail_fetch(workspace)
            # Map gmail keys to generic keys for the prompt
            for m in messages:
                m['sender'] = m['sender']
                m['body'] = m['body']
            platform = "Gmail"
            
        await complete_step(task_id, 2, {"messages_found": len(messages)})
        
        if not messages:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await set_task_status(
                task_id, "done", output="No unread messages found.", duration_ms=duration_ms
            )
            return
            
        # Step 3: Draft replies
        step3_id = await start_step(task_id, 3, "draft_replies", {"count": len(messages)})
        
        drafts = []
        for msg in messages:
            prompt = (
                f"{sop}\n\n"
                f"Customer Message:\n"
                f"From: {msg['sender']}\n"
                f"Subject: {msg['subject']}\n"
                f"Body: {msg['body']}\n\n"
                "Please draft a polite and helpful reply using our brand voice."
            )
            
            # Generate draft
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
                    draft = res.json().get("response", "Error generating draft")
                    drafts.append(f"**From {msg['sender']}**: {msg['body']}\n\n**Draft Reply**:\n{draft}\n---\n")
                else:
                    drafts.append(f"Failed to generate draft for {msg['sender']}")

        await complete_step(task_id, 3, {"drafts_created": len(drafts)})
        
        final_output = "\n".join(drafts)
        
        duration_ms = int((time.monotonic() - start_time) * 1000)
        
        await set_task_status(
            task_id, 
            "done", 
            output=final_output.strip(), 
            duration_ms=duration_ms,
            model=routing["model"],
            tools_called={"etsy": 1}
        )
        
        duration_mins = duration_ms / 60000.0
        await update_module_estimate(module, duration_mins)
        
    except Exception as e:
        logger.error(f"[COMMS] Task {task_id} failed: {e}")
        await set_task_status(task_id, "failed", output=f"Error: {str(e)}")
