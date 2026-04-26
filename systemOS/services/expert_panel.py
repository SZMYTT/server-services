"""
Expert Panel runner — three-model validation pipeline.

Flow:
  1. PARALLEL:  Architect (gemma2:27b) generates full solution
                Auditor   (gemma2:9b)  generates risk checklist independently
  2. CRITIQUE:  Auditor reviews Architect's output against its checklist
  3. REFINE:    Refiner takes Architect output + Auditor critique → final deliverable

This is triggered for high-risk tasks (risk_level: high/financial/critical) or
tasks explicitly marked routing_type: expert_panel.

Import from orchestrator:
    from services.expert_panel import expert_panel_runner

Timing (approximate on M1 Max 64GB):
    gemma2:27b: ~45–90s for a 2000-token response
    gemma2:9b:  ~15–30s per call
    Full panel: ~90–150s total (architect + parallel auditor baseline = ~90s,
                                 critique ~25s, refine ~25s)

Note on parallel execution:
    asyncio.gather starts both the Architect call and Auditor baseline concurrently.
    Ollama must have OLLAMA_NUM_PARALLEL >= 2 set on the Mac for true parallelism.
    Without it, calls queue — you still get the result, just not the time saving.
    Set on Mac: launchctl setenv OLLAMA_NUM_PARALLEL 2
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from systemOS.llm import complete_ex, LLMResult
from systemOS.config.models import MODELS
from systemOS.services.sop_assembler import assemble_sop
from systemOS.services.queue import set_task_status
from systemOS.services.token_tracker import TokenBudget

MAX_CORRECTION_PASSES = 2  # Architect re-runs if Auditor returns FAIL

# Limits concurrent heavy (27B/70B) calls to protect Mac VRAM.
# gemma2:27b ≈ 18GB, gemma2:9b ≈ 7GB × 2 = 32GB peak.
# Semaphore(2) means at most 2 architect/auditor calls run at once.
_HEAVY_SEMAPHORE = asyncio.Semaphore(2)

logger = logging.getLogger("systemos.expert_panel")


@dataclass
class PanelResult:
    architect_output: str = ""
    risk_checklist: str = ""
    auditor_critique: str = ""
    final_output: str = ""
    verdict: str = ""          # PASS / PASS WITH FIXES / FAIL / ESCALATION REQUIRED
    escalated: bool = False    # True if Refiner flagged PANEL ESCALATION REQUIRED
    duration_ms: int = 0
    tokens: dict = field(default_factory=dict)  # {architect, auditor_base, auditor_crit, refiner}


# ── Individual panel step runners ────────────────────────────

async def _run_architect(task: dict) -> tuple[str, LLMResult]:
    """Generate the full solution. Returns (text, full_result)."""
    sop = assemble_sop(
        task_type=task.get("task_type", "research"),
        module=task.get("module", "research"),
        workspace=task.get("workspace", ""),
        persona="architect",
    )
    model = MODELS["architect"]["model"]
    timeout = MODELS["architect"]["timeout_secs"]

    logger.info("[PANEL] Architect starting — model=%s", model)
    async with _HEAVY_SEMAPHORE:
        result = await complete_ex(
            messages=[{"role": "user", "content": task.get("input", "")}],
            system=sop,
            max_tokens=4000,
            model=model,
        )
    logger.info("[PANEL] Architect done — tokens=%d", result["tokens"]["total"])
    return result["text"], result


async def _run_auditor_baseline(task: dict) -> tuple[str, LLMResult]:
    """Generate a risk checklist BEFORE seeing the Architect's output."""
    sop = assemble_sop(
        task_type=task.get("task_type", "research"),
        module=task.get("module", "research"),
        workspace=task.get("workspace", ""),
        persona="auditor",
    )
    model = MODELS["auditor"]["model"]

    baseline_prompt = (
        f"Task: {task.get('input', '')}\n\n"
        "You have NOT yet seen any answer. Generate your RISK CHECKLIST only. "
        "List 5–10 specific risks you expect to see in any answer to this task."
    )

    logger.info("[PANEL] Auditor baseline starting — model=%s", model)
    async with _HEAVY_SEMAPHORE:
        result = await complete_ex(
            messages=[{"role": "user", "content": baseline_prompt}],
            system=sop,
            max_tokens=800,
            model=model,
        )
    logger.info("[PANEL] Auditor baseline done — tokens=%d", result["tokens"]["total"])
    return result["text"], result


async def _run_auditor_critique(
    architect_output: str,
    risk_checklist: str,
    task: dict,
) -> tuple[str, LLMResult]:
    """Critique the Architect's output against the risk checklist."""
    sop = assemble_sop(
        task_type=task.get("task_type", "research"),
        module=task.get("module", "research"),
        workspace=task.get("workspace", ""),
        persona="auditor",
    )
    model = MODELS["auditor"]["model"]

    critique_prompt = (
        f"Original task: {task.get('input', '')}\n\n"
        f"Your risk checklist:\n{risk_checklist}\n\n"
        f"Architect's output:\n{architect_output}\n\n"
        "Now critique the Architect's output against your checklist. "
        "Produce a structured critique with ISSUE blocks and a final VERDICT."
    )

    logger.info("[PANEL] Auditor critique starting")
    result = await complete_ex(
        messages=[{"role": "user", "content": critique_prompt}],
        system=sop,
        max_tokens=1500,
        model=model,
    )
    logger.info("[PANEL] Auditor critique done — tokens=%d", result["tokens"]["total"])
    return result["text"], result


async def _run_refiner(
    architect_output: str,
    auditor_critique: str,
    task: dict,
) -> tuple[str, LLMResult]:
    """Produce the final deliverable from Architect output + Auditor critique."""
    sop = assemble_sop(
        task_type=task.get("task_type", "research"),
        module=task.get("module", "research"),
        workspace=task.get("workspace", ""),
        persona="refiner",
    )
    model = MODELS["refiner"]["model"]

    refine_prompt = (
        f"Original task: {task.get('input', '')}\n\n"
        f"Architect's output:\n{architect_output}\n\n"
        f"Auditor's critique:\n{auditor_critique}\n\n"
        "Apply all CRITICAL and MAJOR fixes. Apply the workspace brand voice. "
        "Produce only the final deliverable — no preamble."
    )

    logger.info("[PANEL] Refiner starting")
    result = await complete_ex(
        messages=[{"role": "user", "content": refine_prompt}],
        system=sop,
        max_tokens=3000,
        model=model,
    )
    logger.info("[PANEL] Refiner done — tokens=%d", result["tokens"]["total"])
    return result["text"], result


async def _run_architect_correction(
    original_output: str,
    critique: str,
    task: dict,
) -> tuple[str, LLMResult]:
    """Re-run the Architect with its original output + Auditor critique to fix FAIL issues."""
    sop = assemble_sop(
        task_type=task.get("task_type", "research"),
        module=task.get("module", "research"),
        workspace=task.get("workspace", ""),
        persona="architect",
    )
    model = MODELS["architect"]["model"]

    correction_prompt = (
        f"Original task: {task.get('input', '')}\n\n"
        f"Your previous output:\n{original_output}\n\n"
        f"Auditor critique (verdict: FAIL):\n{critique}\n\n"
        "The Auditor found critical errors in your output. "
        "Produce a corrected version that fixes every CRITICAL and MAJOR issue listed. "
        "Keep all correct parts unchanged. Address only the flagged issues."
    )

    logger.info("[PANEL] Architect correction pass — model=%s", model)
    result = await complete_ex(
        messages=[{"role": "user", "content": correction_prompt}],
        system=sop,
        max_tokens=4000,
        model=model,
    )
    logger.info("[PANEL] Architect correction done — tokens=%d", result["tokens"]["total"])
    return result["text"], result


def _parse_verdict(critique: str) -> str:
    """Extract the VERDICT line from Auditor critique."""
    match = re.search(r"VERDICT:\s*(PASS WITH FIXES|PASS|FAIL)", critique, re.IGNORECASE)
    return match.group(1).upper() if match else "UNKNOWN"


def _is_escalated(final_output: str) -> bool:
    return "PANEL ESCALATION REQUIRED" in final_output


# ── Main panel orchestrator ───────────────────────────────────

async def expert_panel_runner(task: dict, routing: dict) -> PanelResult:
    """
    Run the full Expert Panel pipeline with self-correction loop.

    Flow:
      1. Architect + Auditor baseline run in parallel
      2. Auditor critiques Architect output
      3. If verdict == FAIL: Architect runs a correction pass (max MAX_CORRECTION_PASSES)
         Loop back to step 2 until PASS/PASS WITH FIXES or passes exhausted
      4. Refiner produces final deliverable
      5. Token totals written to tasks.tokens_used
    """
    task_id = task["id"]
    start = time.monotonic()
    panel = PanelResult()
    budget = TokenBudget(task_id=task_id, label=f"panel_{task.get('task_type', 'task')}")

    try:
        await set_task_status(task_id, "running", output="[Expert Panel] Starting…")

        # ── Step 1: Parallel — Architect + Auditor Baseline ──
        logger.info("[PANEL] Step 1: Architect + Auditor baseline (parallel)")
        (arch_text, arch_result), (risk_text, risk_result) = await asyncio.gather(
            _run_architect(task),
            _run_auditor_baseline(task),
        )
        panel.architect_output = arch_text
        panel.risk_checklist = risk_text
        budget.track(arch_result, call="architect")
        budget.track(risk_result, call="auditor_baseline")
        panel.tokens["architect"] = arch_result["tokens"]["total"]
        panel.tokens["auditor_base"] = risk_result["tokens"]["total"]

        await set_task_status(
            task_id, "running",
            output="[Expert Panel] Architect complete. Running critique…"
        )

        # ── Step 2+3: Auditor critique with correction loop ───
        correction_pass = 0
        arch_text_current = arch_text

        while True:
            logger.info(
                "[PANEL] Auditor critique (pass %d/%d)",
                correction_pass + 1, MAX_CORRECTION_PASSES + 1,
            )
            crit_text, crit_result = await _run_auditor_critique(
                arch_text_current, risk_text, task
            )
            budget.track(crit_result, call=f"auditor_critique_{correction_pass + 1}")
            panel.auditor_critique = crit_text
            panel.verdict = _parse_verdict(crit_text)
            panel.tokens[f"auditor_crit_{correction_pass}"] = crit_result["tokens"]["total"]

            logger.info(
                "[PANEL] Verdict: %s (correction pass %d/%d)",
                panel.verdict, correction_pass, MAX_CORRECTION_PASSES,
            )

            if panel.verdict != "FAIL" or correction_pass >= MAX_CORRECTION_PASSES:
                # PASS, PASS WITH FIXES, UNKNOWN — or exhausted correction budget
                if panel.verdict == "FAIL":
                    logger.warning(
                        "[PANEL] Still FAIL after %d correction passes — proceeding to Refiner",
                        MAX_CORRECTION_PASSES,
                    )
                break

            # FAIL with correction budget remaining — re-run Architect
            correction_pass += 1
            await set_task_status(
                task_id, "running",
                output=f"[Expert Panel] Auditor FAIL — Architect correction pass {correction_pass}…"
            )
            corrected_text, corrected_result = await _run_architect_correction(
                arch_text_current, crit_text, task
            )
            budget.track(corrected_result, call=f"architect_correction_{correction_pass}")
            panel.tokens[f"architect_correction_{correction_pass}"] = corrected_result["tokens"]["total"]
            arch_text_current = corrected_text
            logger.info("[PANEL] Architect correction complete — looping to re-audit")

        panel.architect_output = arch_text_current  # use the corrected version

        await set_task_status(
            task_id, "running",
            output=f"[Expert Panel] Verdict: {panel.verdict}. Refining…"
        )

        # ── Step 4: Refiner ────────────────────────────────────
        logger.info("[PANEL] Step 4: Refiner")
        final_text, final_result = await _run_refiner(arch_text_current, crit_text, task)
        panel.final_output = final_text
        panel.escalated = _is_escalated(final_text)
        budget.track(final_result, call="refiner")
        panel.tokens["refiner"] = final_result["tokens"]["total"]

        panel.duration_ms = int((time.monotonic() - start) * 1000)

        # ── Step 5: Persist token totals ──────────────────────
        budget.log_summary()

        if panel.escalated:
            logger.warning("[PANEL] Escalation required for task %s", task_id[:8])
            await set_task_status(task_id, "pending_approval", output=final_text)
        else:
            await set_task_status(task_id, "done", output=final_text)

        logger.info(
            "[PANEL] Complete — verdict=%s corrections=%d escalated=%s tokens=%d duration=%dms",
            panel.verdict, correction_pass, panel.escalated,
            budget.total, panel.duration_ms,
        )

    except Exception as e:
        panel.duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("[PANEL] Failed for task %s: %s", task_id[:8], e)
        await set_task_status(task_id, "failed", output=f"Expert Panel error: {e}")

    return panel
