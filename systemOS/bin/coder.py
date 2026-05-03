"""
systemOS coding agent — CLI runner.

Invoke from VS Code terminal or any shell:

    # Full task with self-correction loop
    python -m systemOS.bin.coder --task "Add postcode validator to utils/validators.py" --project prismaOS

    # Quick one-shot snippet (lint only, no tests)
    python -m systemOS.bin.coder --task "Parse ISO 8601 date string" --quick

    # Acquire a new tool from its API docs
    python -m systemOS.bin.coder --acquire-skill https://api.example.com/docs/ --tool-name my_tool

    # Pipe a task from stdin
    echo "Write a function to clean HTML from a string" | python -m systemOS.bin.coder --stdin
"""

import argparse
import asyncio
import sys
import os
from pathlib import Path

# Bootstrap path so systemOS is importable from any working directory
_HERE = Path(__file__).parent.parent.parent  # server-services/
sys.path.insert(0, str(_HERE))

_PROJECT_ROOTS = {
    "prismaOS":   _HERE / "prismaOS",
    "systemOS":   _HERE / "systemOS",
    "researchOS": _HERE / "researchOS",
    "fitOS":      _HERE / "fitOS",
    "nnlos":      _HERE / "nnlos",
    "vendorOS":   _HERE / "vendorOS",
}

_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_AMBER = "\033[93m"
_RED   = "\033[91m"
_CYAN  = "\033[96m"
_DIM   = "\033[2m"
_RESET = "\033[0m"


def _hdr(text: str) -> str:
    return f"\n{_BOLD}{_CYAN}{'─' * 60}{_RESET}\n{_BOLD}{text}{_RESET}\n{'─' * 60}"


def _ok(text: str) -> str:
    return f"{_GREEN}✓ {text}{_RESET}"


def _warn(text: str) -> str:
    return f"{_AMBER}⚠ {text}{_RESET}"


def _err(text: str) -> str:
    return f"{_RED}✗ {text}{_RESET}"


async def run_code_task(args: argparse.Namespace) -> int:
    """Run the full code → lint → test → fix loop."""
    from systemOS.agents.coder import code_task, quick_code

    task = args.task
    if not task and args.stdin:
        task = sys.stdin.read().strip()
    if not task:
        print(_err("No task provided. Use --task or --stdin."))
        return 1

    project_root: Path | None = None
    if args.project:
        project_root = _PROJECT_ROOTS.get(args.project)
        if not project_root:
            print(_warn(f"Unknown project '{args.project}'. No AGENTS.md will be injected."))
        elif not project_root.exists():
            print(_warn(f"Project root {project_root} not found. No AGENTS.md injected."))
            project_root = None

    print(_hdr("CodingOS — Coder Agent"))
    print(f"  Task:    {task}")
    if project_root:
        print(f"  Project: {args.project} ({project_root})")
    print(f"  Mode:    {'quick (lint only)' if args.quick else f'full loop (max {args.retries} attempts)'}")
    print()

    if args.quick:
        code = await quick_code(task, model=args.model or None)
        print(_hdr("Generated Code"))
        print(code)
        return 0

    result = await code_task(
        task=task,
        project_root=project_root,
        context=args.context or "",
        max_retries=args.retries,
        model=args.model or None,
        skip_tests=args.skip_tests,
    )

    # ── Summary ──────────────────────────────────────────────
    print(_hdr("Result"))
    if result.passed:
        print(_ok(f"Tests passed in {result.iterations} iteration(s)"))
    else:
        print(_warn(f"Max retries ({args.retries}) reached without passing tests"))

    print(f"  Iterations : {result.iterations}")
    print(f"  Tokens used: {result.tokens_total:,}")
    print(f"  Model      : {result.model}")
    if result.lint_errors:
        print(f"  Lint errors: {len(result.lint_errors)} round(s)")

    print(_hdr("Code"))
    print(result.code)

    if result.tests:
        print(_hdr("Tests"))
        print(result.tests)

    if not result.passed and result.test_outputs:
        print(_hdr("Last Test Output"))
        print(result.test_outputs[-1][:1000])

    # ── Write to file if requested ────────────────────────────
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.code, encoding="utf-8")
        print(f"\n{_ok(f'Code written to {out_path}')}")

    if args.output_tests and result.tests:
        test_path = Path(args.output_tests)
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(result.tests, encoding="utf-8")
        print(_ok(f"Tests written to {test_path}"))

    return 0 if result.passed else 2


async def run_acquire_skill(args: argparse.Namespace) -> int:
    """Acquire a new tool from its API docs."""
    from systemOS.agents.skill_builder import acquire_skill

    source = args.acquire_skill
    tool_name = args.tool_name or None

    print(_hdr("CodingOS — Skill Acquisition"))
    print(f"  Source:    {source}")
    print(f"  Tool name: {tool_name or '(auto-detect from docs)'}")
    print()

    result = await acquire_skill(
        source=source,
        tool_name=tool_name,
        output_dir=_HERE / "systemOS" / "mcp",
        sop_dir=_HERE / "systemOS" / "sops" / "modules",
    )

    if result.error:
        print(_err(f"Skill acquisition failed: {result.error}"))
        return 1

    print(_ok(f"Skill '{result.tool_name}' acquired in {result.duration_ms}ms"))
    print(f"  Capabilities : {', '.join(result.capabilities)}")
    print(f"  Wrapper      : {result.wrapper_path}")
    print(f"  SOP          : {result.sop_path}")
    print(f"  Sandbox check: {'✅ passed' if result.sandbox_ok else '⚠️  failed — review wrapper manually'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m systemOS.bin.coder",
        description="CodingOS — AI coding agent CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--acquire-skill", metavar="SOURCE",
                      help="URL or file path of API docs to learn a new tool")

    p.add_argument("--task",       "-t", help="Coding task in plain English")
    p.add_argument("--stdin",      "-s", action="store_true", help="Read task from stdin")
    p.add_argument("--project",    "-p", choices=list(_PROJECT_ROOTS), help="Target project (injects AGENTS.md)")
    p.add_argument("--context",    "-c", help="Additional context (which file, interfaces to match, etc.)")
    p.add_argument("--retries",    "-r", type=int, default=3, help="Max LLM attempts (default: 3)")
    p.add_argument("--model",      "-m", help="Override Ollama model for this run")
    p.add_argument("--quick",      "-q", action="store_true", help="One-shot snippet (lint only, no tests)")
    p.add_argument("--skip-tests", action="store_true", help="Skip pytest (lint pass is enough)")
    p.add_argument("--output",     "-o", help="Write generated code to this file path")
    p.add_argument("--output-tests", help="Write generated tests to this file path")
    p.add_argument("--tool-name",  help="Snake_case name for acquired skill (used with --acquire-skill)")

    return p


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.acquire_skill:
        return await run_acquire_skill(args)

    if not args.task and not args.stdin:
        parser.print_help()
        return 0

    return await run_code_task(args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
