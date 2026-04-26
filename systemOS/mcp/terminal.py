"""
Sandboxed command execution for agentic code loops.

Runs commands inside a Docker container — LLM-generated code never touches
the host directly. The container is ephemeral (--rm), gets a fresh /workspace
on each call, and is killed after the timeout.

Import from any project:
    from systemOS.mcp.terminal import run_command, run_python, run_pytest, run_ruff

Usage:
    # Run arbitrary shell command
    result = await run_command("ls -la", cwd="/home/szmyt/server-services/researchOS")

    # Run a Python snippet and get output
    result = await run_python("print(1 + 1)")
    print(result.stdout)    # "2"

    # Run pytest against a test file
    result = await run_pytest("/tmp/test_mymodule.py")
    if result.ok:
        print("tests passed")
    else:
        print(result.stderr)  # feed this back to the LLM

    # Lint a file with ruff
    result = await run_ruff("/tmp/mymodule.py")

All functions return a TerminalResult with:
    .ok          bool   — True if returncode == 0
    .stdout      str
    .stderr      str
    .returncode  int
    .duration_ms int
"""

import asyncio
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Docker image used for all sandboxed execution.
# Must have Python 3.12 + pytest + ruff pre-installed for fast startup.
# Build once with: docker build -t systemos-sandbox systemOS/docker/sandbox/
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "systemos-sandbox")
SANDBOX_FALLBACK_IMAGE = "python:3.12-slim"  # used if systemos-sandbox not found

DEFAULT_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT_SECS", "60"))
MAX_OUTPUT_CHARS = 8000  # truncate runaway output


@dataclass
class TerminalResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    duration_ms: int

    def combined(self) -> str:
        """stdout + stderr combined, suitable for feeding back to an LLM."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts)

    def error_summary(self) -> str:
        """Compact error for LLM prompt injection. Truncated."""
        if self.ok:
            return ""
        out = self.combined()
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return out


async def _docker_available() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


async def _image_exists(image: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False


async def _pick_image() -> str:
    if await _image_exists(SANDBOX_IMAGE):
        return SANDBOX_IMAGE
    logger.warning("[TERMINAL] %s not found, using fallback %s", SANDBOX_IMAGE, SANDBOX_FALLBACK_IMAGE)
    return SANDBOX_FALLBACK_IMAGE


async def run_command(
    command: str,
    cwd: str | None = None,
    extra_files: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    image: str | None = None,
) -> TerminalResult:
    """
    Run a shell command inside a Docker sandbox.

    Args:
        command:      Shell command string, e.g. "pytest test_mymodule.py -v"
        cwd:          Host directory to mount read-only at /src inside the container.
                      Generated/temp files go in /workspace (writable, ephemeral).
        extra_files:  Dict of {filename: content} written into /workspace before running.
        timeout:      Seconds before the container is killed.
        image:        Override the Docker image.
    """
    if not await _docker_available():
        return TerminalResult(ok=False, stdout="", stderr="Docker not available on this host.",
                              returncode=-1, duration_ms=0)

    img = image or await _pick_image()
    run_id = uuid.uuid4().hex[:8]
    start = time.monotonic()

    with tempfile.TemporaryDirectory(prefix=f"systemos_sandbox_{run_id}_") as tmpdir:
        tmp = Path(tmpdir)

        # Write extra files into the temp workspace
        if extra_files:
            for fname, content in extra_files.items():
                (tmp / fname).write_text(content)

        # Build docker run command
        docker_cmd = [
            "docker", "run", "--rm",
            "--name", f"systemos-sandbox-{run_id}",
            "--network", "none",          # no network access from sandbox
            "--memory", "512m",           # cap RAM
            "--cpus", "1.0",              # cap CPU
            "-v", f"{tmpdir}:/workspace",
        ]

        if cwd:
            docker_cmd += ["-v", f"{cwd}:/src:ro"]
            docker_cmd += ["-w", "/workspace"]
        else:
            docker_cmd += ["-w", "/workspace"]

        docker_cmd += [img, "bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                duration = int((time.monotonic() - start) * 1000)
                logger.warning("[TERMINAL] sandbox %s timed out after %ds", run_id, timeout)
                return TerminalResult(ok=False, stdout="", stderr=f"Timed out after {timeout}s",
                                      returncode=-1, duration_ms=duration)

            duration = int((time.monotonic() - start) * 1000)
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            rc = proc.returncode

            logger.info("[TERMINAL] sandbox %s | rc=%d | %dms", run_id, rc, duration)
            return TerminalResult(ok=rc == 0, stdout=stdout, stderr=stderr,
                                  returncode=rc, duration_ms=duration)

        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.error("[TERMINAL] sandbox error: %s", e)
            return TerminalResult(ok=False, stdout="", stderr=str(e),
                                  returncode=-1, duration_ms=duration)


async def run_python(
    code: str,
    timeout: int = DEFAULT_TIMEOUT,
    extra_packages: list[str] | None = None,
) -> TerminalResult:
    """
    Execute a Python code string in the sandbox. Returns stdout/stderr.
    Optionally pip-install packages first (adds to startup time).
    """
    pip_cmd = ""
    if extra_packages:
        pkgs = " ".join(extra_packages)
        pip_cmd = f"pip install -q {pkgs} && "

    return await run_command(
        command=f"{pip_cmd}python3 script.py",
        extra_files={"script.py": code},
        timeout=timeout,
    )


async def run_pytest(
    test_code: str,
    source_code: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> TerminalResult:
    """
    Run pytest against test_code (a string containing the test file content).
    Optionally also write source_code as module.py alongside the test.

    The sandbox image must have pytest installed.
    Falls back to installing it via pip if using the generic image.
    """
    files: dict[str, str] = {"test_module.py": test_code}
    if source_code:
        files["module.py"] = source_code

    img = await _pick_image()
    pip_prefix = "" if img == SANDBOX_IMAGE else "pip install -q pytest && "

    return await run_command(
        command=f"{pip_prefix}python3 -m pytest test_module.py -v --tb=short 2>&1",
        extra_files=files,
        timeout=timeout,
    )


async def run_ruff(
    code: str,
    filename: str = "module.py",
    timeout: int = 30,
    fix: bool = False,
) -> TerminalResult:
    """
    Lint a Python code string with ruff.
    Returns ok=True if no issues found, stderr contains the linting output.
    Set fix=True to auto-apply safe fixes (returns the fixed code in stdout).
    """
    img = await _pick_image()
    pip_prefix = "" if img == SANDBOX_IMAGE else "pip install -q ruff && "
    fix_flag = "--fix" if fix else ""

    result = await run_command(
        command=f"{pip_prefix}ruff check {fix_flag} {filename} 2>&1"
                + (f" && cat {filename}" if fix else ""),
        extra_files={filename: code},
        timeout=timeout,
    )
    return result


async def run_in_project(
    command: str,
    project_path: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> TerminalResult:
    """
    Run a command with the project directory mounted read-only at /src.
    Use this to run tests against the actual project code.
    e.g. run_in_project("pytest tests/ -v", "/home/szmyt/server-services/researchOS")
    """
    return await run_command(
        command=f"cd /src && {command}",
        cwd=project_path,
        timeout=timeout,
    )
