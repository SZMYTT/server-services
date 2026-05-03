"""
Coder agent with self-correction loop.

Write code → lint with ruff → run pytest → if failed, feed error back → repeat.
This is what separates a one-shot LLM wrapper from an agentic tool.

Import from any project:
    from systemOS.agents.coder import code_task, CoderResult

Usage:
    from pathlib import Path
    result = await code_task(
        task="Add a function that validates a UK postcode using regex",
        project_root=Path("/home/szmyt/server-services/researchOS"),
        context="The function goes in researchOS/utils/validators.py",
        max_retries=3,
    )
    print(result.code)          # final code (passing tests)
    print(result.tests)         # the pytest file that was written
    print(result.iterations)    # how many LLM rounds it took
    print(result.passed)        # True if tests pass, False if max_retries hit
    result.print_summary()      # human-readable summary

The loop:
    1. LLM writes code + a pytest test file (inside a <thought> block first)
    2. ruff lints the code — failures fed back immediately
    3. pytest runs in the Docker sandbox
    4. If tests fail: error + traceback injected into next LLM call
    5. Repeat up to max_retries

The LLM is forced to write a <thought> block before any code:
    <thought>
    Files I need to read: ...
    Side effects to check: ...
    Implementation plan: ...
    </thought>
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from systemOS.llm import complete_ex, LLMResult
from systemOS.mcp.terminal import run_ruff, run_pytest, TerminalResult
from systemOS.mcp.mapper import map_as_system_block

logger = logging.getLogger(__name__)

MAX_RETRIES_DEFAULT = 3


@dataclass
class CoderResult:
    code: str
    tests: str
    passed: bool
    iterations: int
    lint_errors: list[str] = field(default_factory=list)
    test_outputs: list[str] = field(default_factory=list)
    tokens_total: int = 0
    model: str = ""

    def print_summary(self):
        status = "PASSED" if self.passed else f"FAILED (after {self.iterations} attempts)"
        print(f"\n{'='*60}")
        print(f"Coder result: {status}")
        print(f"Iterations: {self.iterations} | Tokens: {self.tokens_total} | Model: {self.model}")
        if self.lint_errors:
            print(f"Lint issues encountered: {len(self.lint_errors)}")
        if not self.passed and self.test_outputs:
            print(f"\nLast test output:\n{self.test_outputs[-1][:500]}")
        print("="*60)


_SYSTEM_PROMPT = """You are a precise Python engineer. You write clean, tested code.

MANDATORY: Before writing any code, output a <thought> block:
<thought>
Files I need to read or modify: [list them]
Potential side effects: [what could break elsewhere]
Implementation plan: [numbered steps]
</thought>

Then output exactly two fenced code blocks:

```python
# --- CODE ---
# The implementation
```

```python
# --- TESTS ---
import pytest
# pytest tests for the code above
# Each test must be a function starting with test_
# Tests must be self-contained (no external DB, no network)
```

Rules:
- No explanatory prose outside the <thought> block and code blocks
- Tests must pass with `pytest test_module.py`
- Code must pass `ruff check` (no unused imports, no undefined names)
- If you receive an error, fix ONLY the reported issue, do not rewrite everything
"""


def _extract_code_blocks(text: str) -> tuple[str, str]:
    """Extract the CODE and TESTS blocks from LLM output."""
    blocks = re.findall(r"```python\s*(.*?)```", text, re.DOTALL)
    if len(blocks) >= 2:
        return blocks[0].strip(), blocks[1].strip()
    if len(blocks) == 1:
        return blocks[0].strip(), ""
    # Fallback — return everything as code
    return text.strip(), ""


def _build_prompt(task: str, context: str, error_feedback: str, attempt: int) -> str:
    parts = [f"Task: {task}"]
    if context:
        parts.append(f"\nContext:\n{context}")
    if attempt > 0 and error_feedback:
        parts.append(
            f"\n\nAttempt {attempt + 1}. Previous attempt failed with this error:\n"
            f"```\n{error_feedback}\n```\n"
            f"Fix ONLY the reported issue. Keep the working parts unchanged."
        )
    return "\n".join(parts)


async def code_task(
    task: str,
    project_root: Path | None = None,
    context: str = "",
    max_retries: int = MAX_RETRIES_DEFAULT,
    model: str | None = None,
    skip_tests: bool = False,
) -> CoderResult:
    """
    Run the full code → lint → test → fix loop.

    Args:
        task:         Plain-English description of what to build.
        project_root: Path to the project — used to inject the project map into context.
        context:      Extra context (which file to modify, existing interfaces to match, etc.)
        max_retries:  Maximum LLM attempts before giving up.
        model:        Override Ollama model (default: qwen2.5-coder:32b from config.models).
        skip_tests:   If True, skip pytest (just lint + return). Use for snippets.
    """
    from systemOS.config.models import get_model

    if model is None:
        model = get_model("code")["model"]

    # Build system prompt: project map + AGENTS.md + ARCHITECTURE.md + coder SOP
    system = _SYSTEM_PROMPT
    if project_root and project_root.exists():
        parts = [map_as_system_block(project_root)]
        # Inject project-specific coding conventions if they exist
        for doc_name in ("AGENTS.md", "ARCHITECTURE.md", "CLAUDE.md"):
            doc_path = project_root / doc_name
            if doc_path.exists():
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                # Truncate large files to avoid blowing the context
                if len(content) > 3000:
                    content = content[:3000] + "\n... (truncated)"
                parts.append(f"<{doc_name}>\n{content}\n</{doc_name}>")
        
        # Explicitly read cross-repository standards if they exist
        for external_doc in (project_root.parent / "prismaOS" / "AGENTS.md", project_root.parent / "nnlos" / "ARCHITECTURE.md"):
            if external_doc.exists():
                content = external_doc.read_text(encoding="utf-8", errors="replace")
                parts.append(f"<{external_doc.name}>\n{content[:3000]}\n</{external_doc.name}>")

        parts.append(_SYSTEM_PROMPT)
        system = "\n\n".join(parts)

    result = CoderResult(code="", tests="", passed=False, iterations=0, model=model)
    error_feedback = ""

    for attempt in range(max_retries):
        result.iterations = attempt + 1
        prompt = _build_prompt(task, context, error_feedback, attempt)

        # ── LLM call ──────────────────────────────────────────
        logger.info("[CODER] attempt %d/%d — model=%s", attempt + 1, max_retries, model)
        llm: LLMResult = await complete_ex(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=4000,
            model=model,
        )
        result.tokens_total += llm["tokens"]["total"]
        result.code, result.tests = _extract_code_blocks(llm["text"])

        if not result.code:
            error_feedback = "No code block found in your response. Output exactly two ```python blocks."
            continue

        # ── Ruff lint ─────────────────────────────────────────
        lint: TerminalResult = await run_ruff(result.code)
        if not lint.ok:
            error_feedback = f"ruff linting failed:\n{lint.combined()}"
            result.lint_errors.append(error_feedback)
            logger.info("[CODER] lint failed — feeding back")
            continue

        # ── pytest ────────────────────────────────────────────
        if skip_tests or not result.tests:
            result.passed = True
            logger.info("[CODER] skipping tests — done in %d iteration(s)", result.iterations)
            break

        test: TerminalResult = await run_pytest(result.tests, source_code=result.code)
        result.test_outputs.append(test.combined())

        if test.ok:
            result.passed = True
            logger.info("[CODER] tests passed in %d iteration(s)", result.iterations)
            break

        # Tests failed — build feedback for next attempt
        error_feedback = test.error_summary()
        logger.info("[CODER] tests failed (attempt %d), retrying", attempt + 1)

    if not result.passed:
        logger.warning("[CODER] max retries (%d) reached without passing tests", max_retries)

    return result


async def quick_code(task: str, model: str | None = None) -> str:
    """
    One-shot code generation without tests. Returns the code string.
    Use for simple snippets where a full test loop is overkill.
    """
    result = await code_task(task, max_retries=1, skip_tests=True, model=model)
    return result.code
