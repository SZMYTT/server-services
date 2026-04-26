# agents/generic.py
# PrismaOS Universal Generic Agent.
#
# Handles any task type that doesn't need custom tool calls:
#   legal, website, document, auction, finance, and future modules.
#
# Flow: assemble 3-layer SOP → call Ollama with retry → return clean output.

import time
import logging

from services.sop_assembler import assemble_sop
from services.checkpointer import start_step, complete_step, fail_step
from services.queue import set_task_status, update_module_estimate
from services.retry import call_ollama_with_retry

logger = logging.getLogger("prisma.agents.generic")


async def run_generic_task(task: dict, routing: dict):
    """
    Universal agent. Works for any task type that follows the pattern:
        SOP assembly → LLM inference → return formatted text.
    """
    task_id    = task["id"]
    task_type  = task["task_type"]
    module     = task.get("module") or task_type
    workspace  = task["workspace"]
    user_input = task["input"]
    start_time = time.monotonic()

    try:
        # ── Step 1: Assemble SOP ──────────────────────────────────
        step1_id = await start_step(task_id, 1, "assemble_sop", {"module": module, "workspace": workspace})
        sop = assemble_sop(task_type, module, workspace)
        await complete_step(task_id, 1, {"sop_length": len(sop)})

        # ── Step 2: LLM Inference ─────────────────────────────────
        step2_id = await start_step(task_id, 2, "llm_inference", {
            "model":   routing["model"],
            "host":    routing["host"],
        })

        prompt = (
            f"{sop}\n\n"
            f"User Request: {user_input}\n\n"
            "Follow the methodology and output format above exactly. "
            "Be thorough, professional, and specific to the workspace context."
        )

        response = await call_ollama_with_retry(
            client=None,  # call_ollama_with_retry manages its own client
            ollama_url=routing["ollama_url"],
            model=routing["model"],
            prompt=prompt,
            timeout=routing["timeout_secs"],
        )

        await complete_step(task_id, 2, {"response_length": len(response)})

        # ── Finalise ──────────────────────────────────────────────
        duration_ms = int((time.monotonic() - start_time) * 1000)

        output = (
            f"**{task_type.replace('_', ' ').title()} — {workspace}**\n\n"
            f"{response}"
        )

        await set_task_status(
            task_id,
            "done",
            output=output.strip(),
            duration_ms=duration_ms,
            model=routing["model"],
        )
        await update_module_estimate(module, duration_ms / 60000.0)

        logger.info(
            "[GENERIC] Task %s (%s/%s) completed in %.1fs",
            task_id[:8], task_type, module, duration_ms / 1000
        )

    except Exception as exc:
        logger.error("[GENERIC] Task %s failed: %s", task_id[:8], exc)
        await set_task_status(task_id, "failed", output=f"Error: {exc}")
