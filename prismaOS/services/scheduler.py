"""
scheduler.py
PrismaOS task scheduler.

Two job types run in parallel:
  1. DB-backed agent tasks   — rows in the `schedules` table; queued via add_task()
  2. Integration jobs        — hardcoded async functions (Etsy sync, Rightmove, etc.)
                               tracked via `integration_job_runs` DB table

Scheduling windows are read from environment.yaml so a single config drives
both the YAML manifest and the live scheduler.
"""

import asyncio
import logging
import os
import sys
import yaml
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.queue import add_task, _get_conn

try:
    from croniter import croniter
except ImportError:
    croniter = None

logger = logging.getLogger("prisma.scheduler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Environment.yaml loader ──────────────────────────────────────────────────

def load_env_yaml() -> dict:
    path = os.path.join(PROJECT_ROOT, "environment.yaml")
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to load environment.yaml: %s", exc)
        return {}


def workspace_window_to_cron(window_str: str) -> str:
    """
    Convert environment.yaml scheduling window to a cron expression.
    Examples:
      "monday 07:00"  → "0 7 * * 1"
      "daily 06:00"   → "0 6 * * *"
      "friday 08:00"  → "0 8 * * 5"
      "sunday 09:00"  → "0 9 * * 0"
    """
    day_map = {
        "monday": "1", "tuesday": "2", "wednesday": "3",
        "thursday": "4", "friday": "5", "saturday": "6", "sunday": "0",
        "daily": "*",
    }
    try:
        parts = window_str.lower().split()
        day = day_map.get(parts[0], "*")
        h, m = parts[1].split(":")
        return f"{int(m)} {int(h)} * * {day}"
    except Exception:
        return "0 7 * * *"  # safe fallback


# ── DB-backed schedules ───────────────────────────────────────────────────────

def get_due_schedules() -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, workspace, name, task_type, module, input,
                       cron_expression, next_run
                FROM schedules
                WHERE active = true
                  AND (next_run IS NULL OR next_run <= NOW())
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_due_schedules error: %s", exc)
        return []
    finally:
        conn.close()


def update_schedule_run(schedule_id: str, next_run: datetime):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE schedules SET last_run=NOW(), next_run=%s WHERE id=%s",
                (next_run, schedule_id),
            )
        conn.commit()
    except Exception as exc:
        logger.error("update_schedule_run error: %s", exc)
    finally:
        conn.close()


def calc_next_run(cron_expr: str, after: datetime) -> datetime:
    if croniter:
        try:
            return croniter(cron_expr, after).get_next(datetime)
        except Exception:
            pass
    return after + timedelta(days=1)


# ── Integration job tracking ──────────────────────────────────────────────────

def get_integration_last_run(job_name: str) -> datetime | None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_run FROM integration_job_runs WHERE job_name=%s",
                (job_name,),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        conn.close()


def set_integration_last_run(job_name: str):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO integration_job_runs (job_name, last_run)
                VALUES (%s, NOW())
                ON CONFLICT (job_name) DO UPDATE SET last_run = NOW()
                """,
                (job_name,),
            )
        conn.commit()
    except Exception as exc:
        logger.error("set_integration_last_run error: %s", exc)
    finally:
        conn.close()


def integration_is_due(job_name: str, cron_expr: str) -> bool:
    """Return True if the job hasn't run yet this cron window."""
    last = get_integration_last_run(job_name)
    if not last:
        return True
    now = datetime.now(timezone.utc)
    if croniter:
        try:
            # The previous scheduled time before now
            prev = croniter(cron_expr, now).get_prev(datetime)
            # Due if last_run is before the previous scheduled fire time
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return last < prev
        except Exception:
            pass
    # Fallback: due if last run was > 23 hours ago
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() > 82800


# ── Integration job definitions ───────────────────────────────────────────────

async def job_etsy_sync():
    """Pull open Etsy orders into candles_orders table."""
    try:
        from mcp.etsy import sync_orders_to_db
        result = await sync_orders_to_db()
        logger.info("[JOB] etsy_sync: %s", result)
    except Exception as exc:
        logger.error("[JOB] etsy_sync failed: %s", exc)


async def job_rightmove_sync(env: dict):
    """Scrape Rightmove for property listings and upsert into watchlist."""
    try:
        from mcp.browser import sync_rightmove_to_watchlist
        prop = env.get("workspaces", {}).get("property", {})
        postcode = prop.get("target_area", "")
        max_price = 150000
        budget = prop.get("budget_first_purchase", "")
        if isinstance(budget, str):
            # e.g. "under_150k_GBP" → 150000
            import re
            m = re.search(r"(\d+)k", budget)
            if m:
                max_price = int(m.group(1)) * 1000
        if not postcode or postcode == "local to Daniel initially, then anywhere viable":
            logger.info("[JOB] rightmove_sync: no postcode configured yet — skipping")
            return
        result = await sync_rightmove_to_watchlist(postcode, max_price)
        logger.info("[JOB] rightmove_sync: %s", result)
    except Exception as exc:
        logger.error("[JOB] rightmove_sync failed: %s", exc)


async def job_content_publish_check():
    """
    Check for scheduled content posts that are due and queue them for publishing.
    Covers candles_content, nursing_content, food_content.
    """
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for table, workspace in [
                ("candles_content",  "candles"),
                ("nursing_content",  "nursing_massage"),
                ("food_content",     "food_brand"),
            ]:
                cur.execute(
                    f"SELECT id, title, platform FROM {table} WHERE status='scheduled' AND publish_date <= CURRENT_DATE"
                )
                due = cur.fetchall()
                for post in due:
                    await add_task(
                        workspace=workspace,
                        user="scheduler",
                        task_type="action",
                        risk_level="public",
                        module="content",
                        input=f"Publish scheduled post: {post['title']} on {post['platform']} (id:{post['id']})",
                        trigger_type="scheduler",
                        queue_lane="standard",
                    )
                    logger.info("[JOB] content_publish: queued '%s' for %s", post["title"], workspace)
        conn.close()
    except Exception as exc:
        logger.error("[JOB] content_publish_check failed: %s", exc)


# ── Main scheduler loop ───────────────────────────────────────────────────────

async def scheduler_loop():
    logger.info("[SCHEDULER] Starting — DB-backed schedules + integration jobs")
    if not croniter:
        logger.warning("[SCHEDULER] croniter not installed — using 24h fallback")

    env = load_env_yaml()
    scheduling = env.get("scheduling", {})
    windows = scheduling.get("workspace_windows", {})

    # Integration job registry: {job_name: (cron_expr, coroutine_factory)}
    integration_jobs = {
        "etsy_sync":              (workspace_window_to_cron(windows.get("candles", "daily 06:00")),
                                   lambda: job_etsy_sync()),
        "rightmove_sync":         (workspace_window_to_cron(windows.get("property", "sunday 09:00")),
                                   lambda: job_rightmove_sync(env)),
        "content_publish_check":  ("0 7 * * *",
                                   lambda: job_content_publish_check()),
    }

    while True:
        now = datetime.now(timezone.utc)

        # ── 1. DB-backed agent tasks ────────────────────────────
        try:
            for sched in get_due_schedules():
                logger.info("[SCHEDULER] Triggering: %s (%s)", sched["name"], sched["workspace"])
                await add_task(
                    workspace=sched["workspace"],
                    user="scheduler",
                    task_type=sched["task_type"],
                    risk_level="internal",
                    module=sched["module"],
                    input=sched["input"] or f"Scheduled task: {sched['name']}",
                    trigger_type="scheduler",
                    queue_lane="batch",
                )
                next_dt = calc_next_run(sched["cron_expression"], now)
                update_schedule_run(sched["id"], next_dt)
        except Exception as exc:
            logger.error("[SCHEDULER] DB schedule error: %s", exc)

        # ── 2. Integration jobs ──────────────────────────────────
        for job_name, (cron_expr, factory) in integration_jobs.items():
            if integration_is_due(job_name, cron_expr):
                logger.info("[SCHEDULER] Running integration job: %s", job_name)
                try:
                    await factory()
                    set_integration_last_run(job_name)
                except Exception as exc:
                    logger.error("[SCHEDULER] %s failed: %s", job_name, exc)

        await asyncio.sleep(60)


if __name__ == "__main__":
    try:
        asyncio.run(scheduler_loop())
    except KeyboardInterrupt:
        logger.info("[SCHEDULER] Shutting down.")
