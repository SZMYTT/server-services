"""
Preference Tracker — mines declined/corrected tasks to learn Daniel's preferences.

Every time a task is declined or a correction is submitted, the system captures
the reason. Periodically (or on-demand), a Refiner agent reviews the last N
interactions, identifies recurring patterns, and writes them as "Preference Rules"
into the workspace's Layer 3 SOP profile.

These rules are automatically injected into future prompts — the system stops
making the same mistakes and starts anticipating the style expected.

Import from any project:
    from systemOS.services.preference_tracker import (
        capture_feedback,
        run_preference_digest,
        get_workspace_preferences,
    )

Usage:
    # Called from the Discord bot or web UI when Daniel declines a task:
    capture_feedback(
        task_id="abc123",
        workspace="candles",
        feedback="Too formal — Alice's brand is warm, not corporate",
        action="declined",
        db_conn_fn=get_conn,
    )

    # Run the weekly digest (also schedulable):
    summary = await run_preference_digest(
        workspace="candles",
        db_conn_fn=get_conn,
        sops_root=Path("systemOS/sops"),
    )
    print(summary)  # "Added 3 new preference rules for workspace: candles"

    # Read current preferences for a workspace:
    prefs = get_workspace_preferences("candles", sops_root=Path("systemOS/sops"))

The generated preferences are saved to:
    sops/workspaces/{workspace}/preferences.md
And are picked up automatically by sop_assembler as part of Layer 3.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_MIN_FEEDBACK_FOR_DIGEST = 3   # don't run digest until at least N feedback entries exist
_DEFAULT_LOOKBACK = 20          # analyse last N declined/corrected tasks


def capture_feedback(
    task_id: str,
    workspace: str,
    feedback: str,
    action: str = "declined",
    db_conn_fn: Callable | None = None,
) -> bool:
    """
    Log a feedback event to audit_log and update tasks.decline_reason.
    Call this from the Discord bot or web UI whenever Daniel declines or corrects.

    Args:
        task_id:    UUID of the task
        workspace:  workspace slug (e.g. "candles", "cars")
        feedback:   Daniel's reason or correction text
        action:     "declined" | "corrected" | "feedback"
        db_conn_fn: DB connection function from the calling project
    """
    if not db_conn_fn:
        logger.warning("[PREFS] capture_feedback called without db_conn_fn")
        return False
    try:
        with db_conn_fn() as conn:
            with conn.cursor() as cur:
                # Update the task's decline_reason
                if action == "declined":
                    cur.execute(
                        "UPDATE tasks SET status='declined', decline_reason=%s WHERE id=%s",
                        (feedback, task_id),
                    )
                # Log to audit_log for pattern analysis
                cur.execute(
                    """INSERT INTO audit_log (username, action, resource, resource_id, workspace, details)
                       VALUES ('daniel', %s, 'task', %s, %s, %s)""",
                    (action, task_id, workspace, {"feedback": feedback, "action": action}),
                )
        logger.info("[PREFS] Captured %s feedback for task %s workspace=%s", action, task_id[:8], workspace)
        return True
    except Exception as e:
        logger.error("[PREFS] capture_feedback failed: %s", e)
        return False


def _load_recent_feedback(
    workspace: str,
    last_n: int,
    db_conn_fn: Callable,
) -> list[dict]:
    """Pull the last N declined/corrected tasks + their feedback from the DB."""
    try:
        with db_conn_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT t.input, t.output, t.task_type, t.module,
                              t.decline_reason, al.details->>'feedback' AS audit_feedback,
                              t.created_at
                       FROM tasks t
                       LEFT JOIN audit_log al ON al.resource_id = t.id::text
                                             AND al.action IN ('declined','corrected','feedback')
                       WHERE t.workspace = %s
                         AND (t.status = 'declined'
                              OR al.action IN ('corrected', 'feedback'))
                       ORDER BY t.created_at DESC
                       LIMIT %s""",
                    (workspace, last_n),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.error("[PREFS] Failed to load feedback: %s", e)
        return []


async def _analyse_patterns(workspace: str, feedback_items: list[dict]) -> str:
    """Use LLM to identify recurring preference patterns from feedback history."""
    from systemOS.llm import complete

    # Format feedback for analysis
    formatted = []
    for i, item in enumerate(feedback_items[:_DEFAULT_LOOKBACK], 1):
        reason = item.get("decline_reason") or item.get("audit_feedback") or "no reason given"
        task_type = item.get("task_type", "?")
        formatted.append(f"{i}. [{task_type}] Feedback: {reason}")

    feedback_text = "\n".join(formatted)

    prompt = (
        f"Workspace: {workspace}\n\n"
        f"Daniel's recent feedback and corrections:\n{feedback_text}\n\n"
        "Identify 3-7 recurring patterns from this feedback. "
        "Express each pattern as a clear, actionable rule that an AI assistant should follow.\n\n"
        "Format as a markdown bullet list. Each rule should be:\n"
        "- Specific (not vague)\n"
        "- Actionable (tells the AI what to DO or AVOID)\n"
        "- Grounded in the feedback (explain which pattern it addresses)\n\n"
        "Example format:\n"
        "- **Tone:** Always use conversational UK English. Avoid corporate phrases like "
        '"leverage" or "synergy". (Repeated in 4 of 8 corrections)\n\n'
        "Output ONLY the bullet list. No preamble."
    )

    logger.info("[PREFS] Analysing %d feedback items for workspace=%s", len(feedback_items), workspace)
    return await complete(
        messages=[{"role": "user", "content": prompt}],
        fast=True,
        max_tokens=800,
    )


def _write_preferences(
    workspace: str,
    new_rules: str,
    sops_root: Path,
) -> Path:
    """Write or update the preferences.md file for a workspace."""
    prefs_dir = sops_root / "workspaces" / workspace
    prefs_dir.mkdir(parents=True, exist_ok=True)
    prefs_file = prefs_dir / "preferences.md"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"# {workspace.title()} — Preference Rules\n"
        f"*Auto-generated by preference_tracker. Last updated: {timestamp}*\n\n"
        "These rules are derived from Daniel's feedback on declined and corrected tasks.\n"
        "Injected automatically as part of Layer 3 (workspace profile).\n\n"
        "---\n\n"
    )

    if prefs_file.exists():
        # Replace the rules section but keep the header
        existing = prefs_file.read_text()
        # Find the marker and replace everything after it
        marker = "## Current Preference Rules\n"
        if marker in existing:
            base = existing[:existing.index(marker)]
        else:
            base = header
    else:
        base = header

    content = (
        base
        + "## Current Preference Rules\n\n"
        + new_rules.strip()
        + f"\n\n---\n*Updated: {timestamp}*\n"
    )
    prefs_file.write_text(content)
    logger.info("[PREFS] Written preferences for workspace=%s to %s", workspace, prefs_file)
    return prefs_file


async def run_preference_digest(
    workspace: str,
    db_conn_fn: Callable,
    sops_root: Path,
    last_n: int = _DEFAULT_LOOKBACK,
) -> str:
    """
    Full pipeline: load feedback → analyse patterns → update workspace preferences SOP.
    Run this weekly or on-demand after accumulating feedback.

    Returns a one-line summary of what was updated.
    """
    feedback_items = _load_recent_feedback(workspace, last_n, db_conn_fn)

    if len(feedback_items) < _MIN_FEEDBACK_FOR_DIGEST:
        msg = (f"Not enough feedback yet for workspace={workspace} "
               f"({len(feedback_items)}/{_MIN_FEEDBACK_FOR_DIGEST} minimum)")
        logger.info("[PREFS] %s", msg)
        return msg

    new_rules = await _analyse_patterns(workspace, feedback_items)
    prefs_file = _write_preferences(workspace, new_rules, sops_root)

    return (f"Updated preference rules for workspace '{workspace}' "
            f"based on {len(feedback_items)} feedback items → {prefs_file.name}")


def get_workspace_preferences(workspace: str, sops_root: Path) -> str:
    """Read the current preference rules for a workspace. Returns empty string if none."""
    prefs_file = sops_root / "workspaces" / workspace / "preferences.md"
    if prefs_file.exists():
        return prefs_file.read_text()
    return ""


async def digest_all_workspaces(
    db_conn_fn: Callable,
    sops_root: Path,
) -> dict[str, str]:
    """
    Run preference digest for all workspaces that have feedback.
    Returns {workspace: summary} dict.
    Designed to run as a weekly scheduled task.
    """
    # Get distinct workspaces with declined/corrected tasks
    try:
        with db_conn_fn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT workspace FROM tasks
                       WHERE status = 'declined' AND workspace IS NOT NULL
                       UNION
                       SELECT DISTINCT workspace FROM audit_log
                       WHERE action IN ('corrected', 'feedback') AND workspace IS NOT NULL"""
                )
                workspaces = [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.error("[PREFS] Failed to list workspaces: %s", e)
        return {}

    results = {}
    for ws in workspaces:
        results[ws] = await run_preference_digest(ws, db_conn_fn, sops_root)
        logger.info("[PREFS] %s: %s", ws, results[ws])

    return results
