# services/checkpointer.py
# PrismaOS step-level checkpointer.
# Writes to the task_steps table as an agent progresses through its work.
# Mirrors the queue.py pattern: async wrappers over sync psycopg2 calls.
#
# Usage by an agent:
#
#   step_id = await start_step(task_id, 1, "web_search", {"queries": [...]})
#   ...do the work...
#   await complete_step(task_id, 1, {"results": [...]})
#
#   step_id = await start_step(task_id, 2, "summarise", {"sources": [...]})
#   ...do the work...
#   await complete_step(task_id, 2, {"summary": "..."})
#
# If a step fails:
#   await fail_step(task_id, 1, "SearXNG returned no results")

import asyncio
import json
import logging
import os
from functools import partial
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("prisma.checkpointer")


# ── DB connection ─────────────────────────────────────────────

def _get_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5433)),
        dbname=os.getenv("POSTGRES_DB", "systemos"),
        user=os.getenv("POSTGRES_USER", "daniel"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=5,
    )


async def _run(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


# ── Helpers ───────────────────────────────────────────────────

def _to_jsonb(value: Any) -> Optional[str]:
    """Convert a Python value to a JSON string for JSONB columns, or None."""
    if value is None:
        return None
    return json.dumps(value, default=str)


# ── Sync DB functions ─────────────────────────────────────────

def _db_start_step(
    task_id: str,
    step_number: int,
    step_name: str,
    input_data: Any = None,
) -> str:
    """Insert a new step row and mark it started. Returns the step UUID."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_number, step_name, status,
                    input, started_at
                ) VALUES (
                    %s, %s, %s, 'running',
                    %s, NOW()
                )
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (task_id, step_number, step_name, _to_jsonb(input_data)),
            )
            row = cur.fetchone()
        conn.commit()
        step_id = str(row["id"]) if row else "conflict"
        logger.info(
            "[CKPT] start  task=%s step=%d name=%s",
            task_id[:8], step_number, step_name,
        )
        return step_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_complete_step(
    task_id: str,
    step_number: int,
    output_data: Any = None,
) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_steps
                SET status = 'done',
                    output = %s,
                    done_at = NOW()
                WHERE task_id = %s AND step_number = %s
                """,
                (_to_jsonb(output_data), task_id, step_number),
            )
            updated = cur.rowcount
        conn.commit()
        logger.info(
            "[CKPT] done   task=%s step=%d", task_id[:8], step_number
        )
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_fail_step(
    task_id: str,
    step_number: int,
    error_message: str,
) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE task_steps
                SET status = 'failed',
                    error  = %s,
                    done_at = NOW()
                WHERE task_id = %s AND step_number = %s
                """,
                (error_message, task_id, step_number),
            )
            updated = cur.rowcount
        conn.commit()
        logger.warning(
            "[CKPT] failed task=%s step=%d error=%r",
            task_id[:8], step_number, error_message,
        )
        return bool(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _db_get_steps(task_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, step_number, step_name, status,
                       input, output, error, started_at, done_at
                FROM task_steps
                WHERE task_id = %s
                ORDER BY step_number ASC
                """,
                (task_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _db_get_last_completed_step(task_id: str) -> Optional[dict]:
    """Return the highest completed step — used for resuming after failure."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT step_number, step_name, output
                FROM task_steps
                WHERE task_id = %s AND status = 'done'
                ORDER BY step_number DESC
                LIMIT 1
                """,
                (task_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


# ── Public async API ──────────────────────────────────────────

async def start_step(
    task_id: str,
    step_number: int,
    step_name: str,
    input_data: Any = None,
) -> str:
    """
    Record that a step has started. Returns the step UUID.

    step_name should be a short slug:
      web_search, scrape, summarise, write, validate, post
    input_data can be any JSON-serialisable value.
    """
    return await _run(_db_start_step, task_id, step_number, step_name, input_data)


async def complete_step(
    task_id: str,
    step_number: int,
    output_data: Any = None,
) -> bool:
    """Mark a step as done with its output. Returns True if the row was updated."""
    return await _run(_db_complete_step, task_id, step_number, output_data)


async def fail_step(
    task_id: str,
    step_number: int,
    error_message: str,
) -> bool:
    """Mark a step as failed with an error message."""
    return await _run(_db_fail_step, task_id, step_number, error_message)


async def get_steps(task_id: str) -> list[dict]:
    """Return all step records for a task in order."""
    return await _run(_db_get_steps, task_id)


async def get_last_completed_step(task_id: str) -> Optional[dict]:
    """
    Return the highest completed step for a task.
    Use this to resume a task that was interrupted after partial progress.
    Returns None if no steps have completed yet.
    """
    return await _run(_db_get_last_completed_step, task_id)
