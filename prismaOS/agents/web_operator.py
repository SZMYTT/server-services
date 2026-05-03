import logging
import json
import base64
import re
from services.queue import set_task_status

from systemOS.llm import complete_ex 

logger = logging.getLogger("prisma.agents.web_operator")

_VISION_PROMPT = """You are Prisma's Web Operator Vision Agent.
Analyze this screenshot and the current objective: "{objective}".
Find the next element to interact with to progress towards the objective.

Respond with ONLY a valid JSON object. No markdown, no explanations.
Format exactly like this:
{{
    "action": "click" | "type" | "done" | "hitl_required",
    "selector": "CSS selector or text coordinates",
    "text": "Text to type (if action is 'type', otherwise empty)",
    "reason": "Brief 1-sentence explanation of your decision"
}}

Rules:
- If the next action is a sensitive purchase, a destructive action (like 'Delete'), or sending an external message, choose "hitl_required" to pause for Human-in-the-Loop approval.
- If the objective is complete, choose "done".
"""

async def run_web_operator_task(task: dict, routing: dict, browser):
    """Executes a vision-verified web browsing loop to achieve an objective."""
    task_id = task["id"]
    objective = task.get("input", "")
    target_url = task.get("url") # Expected in task payload

    if not objective:
        logger.warning(f"[WEB_OPERATOR] Task {task_id} has no objective. Skipping.")
        await set_task_status(task_id, "failed", output="No objective provided.")
        return

    try:
        logger.info(f"[WEB_OPERATOR] Starting vision loop for task {task_id}")
        
        if target_url:
            await browser.navigate(target_url)

        # Route to a multi-modal model like llava:13b running on MacBook Pro
        model = routing.get("model", "llava:13b")
        
        for step in range(1, 11): # Cap at 10 steps to prevent runaway loops
            logger.info(f"[WEB_OPERATOR] Step {step}: Capturing screenshot")
            screenshot_bytes = await browser.capture_screenshot()
            b64_img = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            messages = [{
                "role": "user", 
                "content": f"Objective: {objective}\nWhat is the next immediate action I should take?",
                "images": [b64_img]
            }]
            
            llm_result = await complete_ex(
                messages=messages,
                system=_VISION_PROMPT.format(objective=objective),
                model=model,
                max_tokens=500
            )
            
            output_text = llm_result.get("text", "{}")
            output_text = re.sub(r"```json\s*", "", output_text)
            output_text = re.sub(r"```", "", output_text).strip()
            
            decision = json.loads(output_text)
            action = decision.get("action")
            selector = decision.get("selector")
            reason = decision.get("reason")
            
            logger.info(f"[WEB_OPERATOR] Decision: {action} on {selector} ({reason})")

            if action == "done":
                await set_task_status(task_id, "done", output=f"Objective Achieved: {reason}")
                return
            elif action == "hitl_required":
                logger.info(f"[WEB_OPERATOR] Sensitive action reached. Requesting HITL.")
                await set_task_status(task_id, "pending_approval", output=f"HITL REQUIRED: {reason}. Check visual logs.")
                return
            elif action == "click" and selector:
                await browser.click(selector)
            elif action == "type" and selector:
                await browser.type_text(decision.get("text", ""), selector)
                
        await set_task_status(task_id, "failed", output="Failed to achieve objective within 10 interaction steps.")
    except Exception as e:
        logger.error(f"[WEB_OPERATOR] Error in task {task_id}: {e}")
        await set_task_status(task_id, "failed", output=str(e))