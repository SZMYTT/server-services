"""
Token usage tracking — accumulate, persist, and analyse LLM costs across tasks.

Every LLM call costs time and memory on the Mac. Tracking tokens lets you:
  - See which tasks / workspaces / agents are the most "expensive"
  - Compare compute cost vs output quality
  - Spot runaway prompts (e.g. an Architect call consuming 8000 tokens)

Import from any project:
    from systemOS.services.token_tracker import TokenBudget

Usage:
    budget = TokenBudget(task_id="abc123", label="vendor_scrape")

    # After every complete_ex call:
    result = await complete_ex(messages=[...])
    budget.track(result, call="synthesis")

    # At task end — writes total to tasks.tokens_used in PostgreSQL:
    budget.flush(db_conn_fn=get_conn)

    # Get a summary dict:
    print(budget.summary())
    # → {"total": 4821, "calls": 3, "by_call": {"queries": 312, "synthesis": 4509}, "model": "gemma4:26b"}

Analytics (query the DB):
    from systemOS.services.token_tracker import token_analytics
    stats = token_analytics(db_conn_fn=get_conn, days=30)
    # → {"total_tokens": 1_200_000, "by_task_type": {...}, "by_workspace": {...}, "top_tasks": [...]}
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class TokenBudget:
    """
    Accumulates token counts across multiple LLM calls for a single task.

    Create one at the start of a task run, call .track() after each complete_ex,
    then .flush() at the end to write the total to the DB.
    """
    task_id: str = ""
    label: str = ""                    # human label for logging ("vendor_scrape", etc.)
    _calls: list[dict] = field(default_factory=list, repr=False)
    _total: int = 0
    _start: float = field(default_factory=time.monotonic, repr=False)

    def track(self, result: dict, call: str = "") -> dict:
        """
        Record token usage from a complete_ex LLMResult.
        Returns the result unchanged so calls can be chained.

        Example:
            result = budget.track(await complete_ex(...), call="synthesis")
        """
        tokens = result.get("tokens", {})
        total = tokens.get("total", 0)
        self._total += total
        self._calls.append({
            "call":       call or f"call_{len(self._calls) + 1}",
            "prompt":     tokens.get("prompt", 0),
            "completion": tokens.get("completion", 0),
            "total":      total,
            "model":      result.get("model", "?"),
            "backend":    result.get("backend", "?"),
        })
        logger.debug(
            "[TOKENS] %s/%s — %d tokens (prompt=%d completion=%d)",
            self.label, call, total, tokens.get("prompt", 0), tokens.get("completion", 0),
        )
        return result

    @property
    def total(self) -> int:
        return self._total

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def summary(self) -> dict:
        """Return a human-readable summary dict."""
        by_call = {c["call"]: c["total"] for c in self._calls}
        models = list({c["model"] for c in self._calls})
        return {
            "total":      self._total,
            "calls":      len(self._calls),
            "by_call":    by_call,
            "models":     models,
            "elapsed_ms": self.elapsed_ms,
            "label":      self.label,
        }

    def log_summary(self):
        """Log a one-liner summary at INFO level."""
        s = self.summary()
        logger.info(
            "[TOKENS] %s — total=%d across %d calls in %dms | models=%s",
            self.label, s["total"], s["calls"], s["elapsed_ms"], ", ".join(s["models"]),
        )

    def flush(self, db_conn_fn: Callable | None = None) -> bool:
        """
        Write total tokens to the tasks table in PostgreSQL.
        Safe to call even if task_id is empty (no-op).
        Returns True if written successfully.
        """
        self.log_summary()
        if not self.task_id or not db_conn_fn:
            return False
        try:
            with db_conn_fn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE tasks SET tokens_used = %s WHERE id = %s",
                        (self._total, self.task_id),
                    )
            logger.debug("[TOKENS] Flushed %d tokens for task %s", self._total, self.task_id[:8])
            return True
        except Exception as e:
            logger.warning("[TOKENS] DB flush failed: %s", e)
            return False

    def flush_to_column(
        self,
        db_conn_fn: Callable,
        table: str,
        id_column: str,
        row_id,
        token_column: str = "tokens_used",
    ) -> bool:
        """
        Write total tokens to any table/column — for research_index, vendor_profiles, etc.

        Example:
            budget.flush_to_column(get_conn, "supply.research_index", "topic_id", 14)
        """
        if not db_conn_fn:
            return False
        try:
            with db_conn_fn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {table} SET {token_column} = %s WHERE {id_column} = %s",
                        (self._total, row_id),
                    )
            return True
        except Exception as e:
            logger.warning("[TOKENS] Column flush failed for %s.%s: %s", table, token_column, e)
            return False


def token_analytics(
    db_conn_fn: Callable,
    days: int = 30,
    workspace: str | None = None,
) -> dict:
    """
    Query aggregate token usage from the tasks table.

    Returns:
        {
            total_tokens:    int,
            total_tasks:     int,
            avg_per_task:    int,
            by_task_type:    {"research": 450000, "content": 120000, ...},
            by_workspace:    {"candles": 200000, "cars": 150000, ...},
            top_tasks: [
                {"id": "...", "task_type": "research", "workspace": "property",
                 "tokens_used": 8200, "created_at": "2026-04-26"},
                ...
            ]
        }
    """
    try:
        with db_conn_fn() as conn:
            with conn.cursor() as cur:

                # Total + average
                ws_filter = "AND workspace = %s" if workspace else ""
                params_base = [days] + ([workspace] if workspace else [])

                cur.execute(f"""
                    SELECT
                        COALESCE(SUM(tokens_used), 0) AS total,
                        COUNT(*) FILTER (WHERE tokens_used IS NOT NULL) AS tracked_tasks,
                        COALESCE(AVG(tokens_used) FILTER (WHERE tokens_used > 0), 0)::int AS avg
                    FROM tasks
                    WHERE created_at >= NOW() - INTERVAL '%s days'
                    {ws_filter}
                """, params_base)
                row = cur.fetchone()
                total_tokens, total_tasks, avg_per_task = row

                # By task_type
                cur.execute(f"""
                    SELECT task_type, COALESCE(SUM(tokens_used), 0) AS t
                    FROM tasks
                    WHERE created_at >= NOW() - INTERVAL '%s days' {ws_filter}
                    GROUP BY task_type ORDER BY t DESC
                """, params_base)
                by_task_type = {r[0]: r[1] for r in cur.fetchall() if r[0]}

                # By workspace
                cur.execute(f"""
                    SELECT workspace, COALESCE(SUM(tokens_used), 0) AS t
                    FROM tasks
                    WHERE created_at >= NOW() - INTERVAL '%s days' {ws_filter}
                    GROUP BY workspace ORDER BY t DESC
                """, params_base)
                by_workspace = {r[0]: r[1] for r in cur.fetchall() if r[0]}

                # Top 10 most expensive tasks
                cur.execute(f"""
                    SELECT id, task_type, module, workspace, tokens_used,
                           created_at::date AS date
                    FROM tasks
                    WHERE tokens_used IS NOT NULL
                      AND created_at >= NOW() - INTERVAL '%s days' {ws_filter}
                    ORDER BY tokens_used DESC LIMIT 10
                """, params_base)
                cols = [d[0] for d in cur.description]
                top_tasks = [dict(zip(cols, r)) for r in cur.fetchall()]

        return {
            "total_tokens":  int(total_tokens),
            "total_tasks":   int(total_tasks),
            "avg_per_task":  int(avg_per_task),
            "by_task_type":  by_task_type,
            "by_workspace":  by_workspace,
            "top_tasks":     top_tasks,
            "period_days":   days,
        }

    except Exception as e:
        logger.error("[TOKENS] Analytics query failed: %s", e)
        return {"error": str(e)}
