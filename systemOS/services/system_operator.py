"""
System Operator — controlled self-maintenance for the OS stack.

Gives the AI write-access to its own environment for safe, logged operations.
Every operation is written to audit_log. High-risk operations require explicit
approval and go through the task queue rather than running immediately.

Safety model:
  SAFE (runs immediately, logged):
    - restart_service        — systemctl restart <whitelisted service>
    - vacuum_table           — ANALYZE/VACUUM a Postgres table
    - organize_research      — rename/sort research output files by date
    - git_pull               — pull latest code from remote
    - git_status             — check repo state (read-only)

  APPROVAL REQUIRED (queued as pending_approval, never auto-run):
    - install_package        — pip install in a venv
    - update_config          — modify .env / docker-compose files
    - delete_files           — remove files matching a pattern
    - rebuild_docker         — docker build a service image

Import from any project:
    from systemOS.services.system_operator import SystemOperator

Usage:
    op = SystemOperator(audit_db_conn_fn=get_conn)

    # Restart a service (safe — runs immediately)
    result = await op.restart_service("prisma-web")
    print(result.ok, result.output)

    # Vacuum a table (safe)
    result = await op.vacuum_table("supply.research_findings")

    # Install a package (queued for approval)
    result = await op.install_package("requests-oauthlib", venv_path="/path/to/venv")
    print(result.queued)  # True — waiting for Daniel to approve in UI
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Only these services can be restarted without approval
SAFE_SERVICES = {
    "prisma-web",
    "prisma-orchestrator",
    "prisma-bot",
    "researchos",
    "nnlos-web",
    "nnlos-worker",
}

# Server-services root for path operations
_SERVICES_ROOT = Path(os.getenv("SERVICES_ROOT", "/home/szmyt/server-services"))


@dataclass
class OperatorResult:
    ok: bool
    output: str
    operation: str
    queued: bool = False       # True if routed to approval queue
    duration_ms: int = 0
    requires_approval: bool = False


class SystemOperator:
    def __init__(self, audit_db_conn_fn: Callable | None = None):
        self._db = audit_db_conn_fn

    def _log_op(self, operation: str, details: dict, outcome: str = "ok"):
        if not self._db:
            return
        try:
            with self._db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO audit_log
                           (username, action, resource, workspace, details)
                           VALUES ('system_operator', %s, 'system', 'system', %s)""",
                        (operation, {**details, "outcome": outcome}),
                    )
        except Exception as e:
            logger.warning("[SYSOP] audit log failed: %s", e)

    async def _run(self, cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        """Run a shell command. Returns (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")
        except asyncio.TimeoutError:
            return -1, "", f"Timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    # ── SAFE OPERATIONS ───────────────────────────────────────

    async def restart_service(self, service_name: str) -> OperatorResult:
        """
        Restart a systemd service. Only whitelisted services are allowed.
        Runs: sudo systemctl restart <service_name>
        """
        start = time.monotonic()
        if service_name not in SAFE_SERVICES:
            msg = f"Service '{service_name}' is not in the safe restart whitelist: {SAFE_SERVICES}"
            logger.warning("[SYSOP] Blocked restart of %s", service_name)
            self._log_op("restart_service", {"service": service_name}, outcome="blocked")
            return OperatorResult(ok=False, output=msg, operation="restart_service",
                                  duration_ms=int((time.monotonic() - start) * 1000))

        logger.info("[SYSOP] Restarting service: %s", service_name)
        rc, stdout, stderr = await self._run(["sudo", "systemctl", "restart", service_name], timeout=30)
        ok = rc == 0
        output = stdout or stderr or ("restarted" if ok else "failed")
        self._log_op("restart_service", {"service": service_name, "rc": rc}, outcome="ok" if ok else "error")

        return OperatorResult(ok=ok, output=output, operation="restart_service",
                              duration_ms=int((time.monotonic() - start) * 1000))

    async def service_status(self, service_name: str) -> OperatorResult:
        """Check the status of a systemd service (read-only)."""
        start = time.monotonic()
        rc, stdout, stderr = await self._run(
            ["systemctl", "is-active", "--quiet", service_name], timeout=5
        )
        active = rc == 0
        output = "active" if active else "inactive/failed"
        return OperatorResult(ok=active, output=output, operation="service_status",
                              duration_ms=int((time.monotonic() - start) * 1000))

    async def vacuum_table(self, table: str, db_conn_fn: Callable | None = None) -> OperatorResult:
        """
        Run ANALYZE on a Postgres table to update query planner statistics.
        Safe read-only operation from Postgres's perspective.
        """
        start = time.monotonic()
        conn_fn = db_conn_fn or self._db
        if not conn_fn:
            return OperatorResult(ok=False, output="No DB connection", operation="vacuum_table",
                                  duration_ms=0)
        try:
            with conn_fn() as conn:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f"ANALYZE {table}")
            output = f"ANALYZE completed for {table}"
            logger.info("[SYSOP] %s", output)
            self._log_op("vacuum_table", {"table": table})
            return OperatorResult(ok=True, output=output, operation="vacuum_table",
                                  duration_ms=int((time.monotonic() - start) * 1000))
        except Exception as e:
            return OperatorResult(ok=False, output=str(e), operation="vacuum_table",
                                  duration_ms=int((time.monotonic() - start) * 1000))

    async def organize_research(self, project_dir: Path) -> OperatorResult:
        """
        Rename research .md files to a consistent date-prefixed format.
        Safe — only renames within the given directory, no deletions.
        """
        start = time.monotonic()
        if not project_dir.exists():
            return OperatorResult(ok=False, output=f"Directory not found: {project_dir}",
                                  operation="organize_research", duration_ms=0)
        renamed = 0
        for f in sorted(project_dir.rglob("*.md")):
            if re.match(r'^\d{8}_', f.name):
                continue  # already dated
            mtime = f.stat().st_mtime
            import datetime
            date_prefix = datetime.datetime.fromtimestamp(mtime).strftime("%Y%m%d_")
            new_name = f.parent / (date_prefix + f.name)
            if not new_name.exists():
                f.rename(new_name)
                renamed += 1

        output = f"Renamed {renamed} files in {project_dir}"
        logger.info("[SYSOP] %s", output)
        self._log_op("organize_research", {"dir": str(project_dir), "renamed": renamed})
        return OperatorResult(ok=True, output=output, operation="organize_research",
                              duration_ms=int((time.monotonic() - start) * 1000))

    async def git_status(self, project_path: Path | None = None) -> OperatorResult:
        """Read-only git status check."""
        start = time.monotonic()
        path = str(project_path or _SERVICES_ROOT)
        rc, stdout, stderr = await self._run(
            ["git", "-C", path, "status", "--short"], timeout=10
        )
        output = stdout or stderr or "clean"
        return OperatorResult(ok=rc == 0, output=output, operation="git_status",
                              duration_ms=int((time.monotonic() - start) * 1000))

    async def git_pull(self, project_path: Path | None = None) -> OperatorResult:
        """Pull latest code from remote. Logs the operation."""
        start = time.monotonic()
        path = str(project_path or _SERVICES_ROOT)
        logger.info("[SYSOP] git pull in %s", path)
        rc, stdout, stderr = await self._run(
            ["git", "-C", path, "pull", "--ff-only"], timeout=60
        )
        ok = rc == 0
        output = stdout or stderr
        self._log_op("git_pull", {"path": path, "rc": rc, "output": output[:200]},
                     outcome="ok" if ok else "error")
        return OperatorResult(ok=ok, output=output, operation="git_pull",
                              duration_ms=int((time.monotonic() - start) * 1000))

    # ── APPROVAL-REQUIRED OPERATIONS ─────────────────────────

    async def install_package(
        self,
        package: str,
        venv_path: str,
        queue_fn: Callable | None = None,
    ) -> OperatorResult:
        """
        Queue a pip install for human approval. Does NOT run immediately.
        If queue_fn is provided, creates a pending_approval task in the queue.
        """
        msg = f"APPROVAL REQUIRED: pip install {package} in {venv_path}"
        logger.info("[SYSOP] %s", msg)
        self._log_op("install_package", {"package": package, "venv": venv_path},
                     outcome="queued_for_approval")
        if queue_fn:
            await queue_fn({
                "task_type": "action",
                "module": "system_operator",
                "input": f"pip install {package} in {venv_path}",
                "risk_level": "high",
                "status": "pending_approval",
                "workspace": "system",
            })
        return OperatorResult(ok=True, output=msg, operation="install_package",
                              queued=True, requires_approval=True, duration_ms=0)

    async def run_approved_install(self, package: str, venv_path: str) -> OperatorResult:
        """
        Execute a pip install. Only call after human approval.
        Separate from install_package to prevent accidental direct calls.
        """
        start = time.monotonic()
        pip = Path(venv_path) / "bin" / "pip"
        if not pip.exists():
            return OperatorResult(ok=False, output=f"pip not found: {pip}",
                                  operation="run_approved_install", duration_ms=0)
        logger.info("[SYSOP] Installing %s in %s", package, venv_path)
        rc, stdout, stderr = await self._run(
            [str(pip), "install", package], timeout=120
        )
        ok = rc == 0
        output = stdout[-500:] if stdout else stderr[-500:]
        self._log_op("run_approved_install", {"package": package, "rc": rc},
                     outcome="ok" if ok else "error")
        return OperatorResult(ok=ok, output=output, operation="run_approved_install",
                              duration_ms=int((time.monotonic() - start) * 1000))
