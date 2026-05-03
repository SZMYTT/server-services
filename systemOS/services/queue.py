# services/queue.py
# PrismaOS task queue — PostgreSQL-backed.
# All public functions are async; DB calls run in a thread-pool
# executor so we never block the Discord / FastAPI event loop.
#
# Priority scoring (higher = runs sooner):
#   queue weight            urgent=1000, fast=200, standard=50, batch=10
#   workspace fairness      +5 per minute since that workspace last ran
#   wait time boost         +1 per minute task has been queued
#   shorter task bonus      +20 if estimated module duration < 5 min
#
# Status lifecycle:
#   queued → pending_approval → approved → running →
#       done | failed | pending_publish → done | declined

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("prisma.queue")

# ── Queue lane weights ────────────────────────────────────────

LANE_WEIGHTS = {
    "urgent":   1000,
    "fast":      200,
    "standard":   50,
    "batch":      10,
}

# Task types that map to fast lane (≤ 2 min expected)
FAST_LANE_TYPES = {"comms", "inventory", "classification", "stock_alert"}

# ── DB connection ─────────────────────────────────────────────

def _get_conn():
    """Open a fresh psycopg2 connection using .env credentials."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        dbname=os.getenv("POSTGRES_DB", "systemos"),
        user=os.getenv("POSTGRES_USER", "daniel"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
    )


async def _run_in_executor(func, *args, **kwargs):
    """Run a sync DB function on the default thread-pool executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


# ── Priority calculation ──────────────────────────────────────

def _compute_priority(
    task_type: str,
    module: str,
    queue_lane: str,
    workspace: str,
    conn,
) -> int:
    """
    Calculate a numeric priority score for a new task.
    Runs synchronously inside the thread-pool executor.
    """
    score = LANE_WEIGHTS.get(queue_lane, 50)

    # Workspace fairness: minutes since this workspace last completed a task
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(completed_at))) / 60
            FROM tasks
            WHERE workspace = %s AND status = 'done'
            """,
            (workspace,),
        )
        row = cur.fetchone()
        if row and row[0]:
            score += min(int(row[0]) * 5, 100)  # cap fairness boost at 100

    # Shorter task bonus
    with conn.cursor() as cur:
        cur.execute(
            "SELECT estimated_mins FROM module_estimates WHERE module = %s",
            (module,),
        )
        row = cur.fetchone()
        if row and row[0] is not None and row[0] < 5:
            score += 20

    return score


def _assign_lane(task_type: str, risk_level: str) -> str:
    """
    Assign a queue lane from task attributes.
    urgency / financial risk → urgent
    fast types               → fast
    everything else          → standard
    batch is set explicitly by scheduler only.
    """
    if risk_level == "financial":
        return "urgent"
    if task_type in FAST_LANE_TYPES:
        return "fast"
    return "standard"


# ── Sync DB helpers (run inside executor) ─────────────────────

def _db_add_task(
    workspace: str,
    user_name: str,
    task_type: str,
    risk_level: str,
    module: str,
    input_text: str,
    trigger_type: str,
    queue_lane: Optional[str],
    model: Optional[str],
    parent_task_id: Optional[str] = None,
    root_task_id: Optional[str] = None,
    depth: str = "standard",
) -> dict:
    task_id = str(uuid.uuid4())
    lane = queue_lane or _assign_lane(task_type, risk_level)

    conn = _get_conn()
    try:
        priority = _compute_priority(task_type, module, lane, workspace, conn)

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO tasks (
                    id, workspace, user_name, trigger_type, task_type,
                    risk_level, module, model, input, status,
                    queue_lane, priority_score, parent_task_id, root_task_id, depth
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 'queued',
                    %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    task_id, workspace, user_name, trigger_type, task_type,
                    risk_level, module, model, input_text,
                    lane, priority, parent_task_id, root_task_id, depth,
                ),
            )
            row = dict(cur.fetchone())
        conn.commit()
        logger.info(
            "[QUEUE] add_task id=%s workspace=%s type=%s lane=%s priority=%s",
            task_id[:8], workspace, task_type, lane, priority,
        )
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_get_task(task_id: str) -> Optional[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def _db_get_task_status(workspace: Optional[str], user_name: str) -> list[dict]:
    """Return active (non-terminal) tasks for a user, newest first."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            params = [user_name]
            workspace_clause = ""
            if workspace:
                workspace_clause = "AND workspace = %s"
                params.append(workspace)

            cur.execute(
                f"""
                SELECT id, workspace, task_type, status, queue_lane,
                       priority_score, created_at
                FROM tasks
                WHERE user_name = %s
                  {workspace_clause}
                  AND status NOT IN ('done', 'failed', 'declined')
                ORDER BY created_at DESC
                LIMIT 20
                """,
                params,
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _db_get_full_queue(workspaces: list[str] = None) -> list[dict]:
    """Return all pending / active tasks ordered by priority, then age. Optionally filtered by workspaces."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            query = """
                SELECT id, workspace, user_name, task_type, risk_level,
                       status, queue_lane, priority_score, created_at, input, output
                FROM tasks
                WHERE status IN (
                    'queued', 'pending_approval', 'approved', 'running',
                    'pending_publish'
                )
            """
            params = []
            if workspaces and "all_workspaces" not in workspaces:
                query += " AND workspace = ANY(%s) "
                params.append((workspaces,))
                
            query += " ORDER BY priority_score DESC, created_at ASC LIMIT 50 "
            cur.execute(query, tuple(params))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _db_approve_task(task_id: str, approved_by: str) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET status = 'approved',
                    approval_by = %s,
                    approval_at = NOW()
                WHERE id = %s
                  AND status IN ('queued', 'pending_approval')
                """,
                (approved_by, task_id),
            )
            updated = cur.rowcount
        conn.commit()
        if updated:
            logger.info("[QUEUE] approve_task id=%s by=%s", task_id[:8], approved_by)
        else:
            logger.warning(
                "[QUEUE] approve_task id=%s — not found or wrong status", task_id[:8]
            )
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_decline_task(task_id: str, reason: str) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET status = 'declined',
                    decline_reason = %s
                WHERE id = %s
                  AND status IN ('queued', 'pending_approval')
                """,
                (reason, task_id),
            )
            updated = cur.rowcount
        conn.commit()
        if updated:
            logger.info("[QUEUE] decline_task id=%s reason=%r", task_id[:8], reason)
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_publish_task(task_id: str) -> bool:
    """Move a completed content task from pending_publish → done."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tasks
                SET status = 'done',
                    completed_at = NOW()
                WHERE id = %s
                  AND status = 'pending_publish'
                """,
                (task_id,),
            )
            updated = cur.rowcount
        conn.commit()
        if updated:
            logger.info("[QUEUE] publish_task id=%s — marked done", task_id[:8])
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_set_status(task_id: str, status: str, **extra_fields) -> bool:
    """
    Generic status updater used by the runner / orchestrator.
    Handles: running, done, failed, pending_approval, pending_publish.
    Accepted extra_fields: output, tokens_used, duration_ms, tools_called, model.
    """
    allowed = {
        "output", "tokens_used", "duration_ms",
        "tools_called", "model",
    }
    updates = {"status": status}
    for k, v in extra_fields.items():
        if k in allowed:
            updates[k] = v

    if status == "running":
        # No completed_at yet
        pass
    elif status in ("done", "failed", "declined"):
        updates["completed_at"] = datetime.now(timezone.utc)

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [task_id]

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = %s RETURNING parent_task_id",
                values,
            )
            row = cur.fetchone()
            updated = cur.rowcount

            if updated and row and row[0] and status in ("done", "failed", "declined"):
                parent_id = row[0]
                cur.execute(
                    """
                    SELECT COUNT(*) FROM tasks 
                    WHERE parent_task_id = %s 
                      AND status NOT IN ('done', 'failed', 'declined')
                    """,
                    (parent_id,)
                )
                pending_count = cur.fetchone()[0]
                if pending_count == 0:
                    cur.execute(
                        """
                        UPDATE tasks 
                        SET status = 'approved' 
                        WHERE id = %s AND status = 'awaiting_children'
                        """,
                        (parent_id,)
                    )
                    if cur.rowcount > 0:
                        logger.info("[QUEUE] Parent task %s all children complete, moved to approved", str(parent_id)[:8])

        conn.commit()
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_update_module_estimate(module: str, actual_mins: float):
    """Rolling average of module duration; called after a task completes."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO module_estimates (module, estimated_mins, sample_count)
                VALUES (%s, %s, 1)
                ON CONFLICT (module) DO UPDATE
                SET estimated_mins = (
                        module_estimates.estimated_mins * module_estimates.sample_count
                        + EXCLUDED.estimated_mins
                    ) / (module_estimates.sample_count + 1),
                    sample_count = module_estimates.sample_count + 1,
                    updated_at = NOW()
                """,
                (module, actual_mins),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Public async API ──────────────────────────────────────────

async def add_task(
    workspace: str,
    user: str,
    task_type: str,
    risk_level: str,
    module: str,
    input: str,
    trigger_type: str,
    queue_lane: Optional[str] = None,
    model: Optional[str] = None,
    parent_task_id: Optional[str] = None,
    root_task_id: Optional[str] = None,
    depth: str = "standard",
) -> str:
    """
    Enqueue a new task. Returns the task UUID string.

    The task is inserted with status='queued'. The bot then calls
    approve_task() once Daniel clicks Approve; the runner picks it up.
    """
    row = await _run_in_executor(
        _db_add_task,
        workspace, user, task_type, risk_level,
        module, input, trigger_type, queue_lane, model,
        parent_task_id, root_task_id, depth,
    )
    return row["id"]


async def get_task(task_id: str) -> Optional[dict]:
    """Return the full task row as a dict, or None if not found."""
    return await _run_in_executor(_db_get_task, task_id)


async def get_task_status(
    workspace: Optional[str],
    user: str,
) -> list[dict]:
    """
    Return a user's active (non-terminal) tasks for /status.
    Filtered to workspace if provided.
    """
    return await _run_in_executor(_db_get_task_status, workspace, user)


async def get_full_queue(workspaces: list[str] = None) -> list[dict]:
    """Return all pending/active tasks across the system, optionally filtered by workspace."""
    return await _run_in_executor(_db_get_full_queue, workspaces)


async def approve_task(task_id: str, approved_by: str = "daniel") -> bool:
    """
    Mark a task approved. Returns True if the row was updated.
    The runner will pick it up on its next poll.
    """
    return await _run_in_executor(_db_approve_task, task_id, approved_by)


async def decline_task(task_id: str, reason: str) -> bool:
    """Decline a task with a human-readable reason shown to the requester."""
    return await _run_in_executor(_db_decline_task, task_id, reason)


async def publish_task(task_id: str) -> bool:
    """
    Approve a completed content task for public posting.
    Moves status from pending_publish → done.
    """
    return await _run_in_executor(_db_publish_task, task_id)


async def set_task_status(
    task_id: str,
    status: str,
    **extra_fields,
) -> bool:
    """
    Update task status and optional fields.
    Called by the runner / agent layer:
      - set_task_status(id, 'running')
      - set_task_status(id, 'done', output=..., tokens_used=..., duration_ms=...)
      - set_task_status(id, 'failed', output='Error: ...')
    """
    return await _run_in_executor(_db_set_status, task_id, status, **extra_fields)


async def update_module_estimate(module: str, actual_mins: float) -> None:
    """Update rolling average duration for a module. Call after task completion."""
    await _run_in_executor(_db_update_module_estimate, module, actual_mins)


async def get_next_approved_task() -> Optional[dict]:
    """
    Pop the highest-priority approved task for the runner.
    Atomically marks it 'running' to prevent double-pickup.
    Returns the full task row or None if queue is empty.
    """
    def _pop(task_id_placeholder=None):
        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'running'
                    WHERE id = (
                        SELECT id FROM tasks
                        WHERE status = 'approved'
                        ORDER BY priority_score DESC, created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING *
                    """,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return await _run_in_executor(_pop)
