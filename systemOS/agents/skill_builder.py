"""
Skill Builder — Dynamic tool acquisition from documentation.

Reads API docs or CLI documentation, extracts capabilities, writes a Python
MCP wrapper and a Layer 2 SOP, then registers the new skill so the system
knows it exists for future task routing.

Import from any project:
    from systemOS.agents.skill_builder import acquire_skill, SkillResult

Usage:
    # Teach the system to use a new shipping API
    result = await acquire_skill(
        source="https://api.royalmail.com/docs/v3/",
        tool_name="royal_mail",
        output_dir=Path("/home/szmyt/server-services/systemOS/mcp"),
        sop_dir=Path("/home/szmyt/server-services/systemOS/sops/modules"),
    )
    print(result.wrapper_path)   # systemOS/mcp/royal_mail.py
    print(result.sop_path)       # systemOS/sops/modules/royal_mail.md
    print(result.capabilities)   # ["track_shipment", "create_label", ...]

    # From a local PDF or text file
    result = await acquire_skill(
        source="/path/to/api_docs.pdf",
        tool_name="internal_erp",
    )

The generated wrapper is run through the sandbox to verify it at least
imports cleanly. The SOP is structured as Layer 2 (module-level) so it
can be injected by sop_assembler when the tool is used.

After acquisition, the tool is registered in:
    systemOS/config/tools_registry.json
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "tools_registry.json"

_CAPABILITY_EXTRACTOR_PROMPT = """You are analyzing API or CLI documentation to extract callable capabilities.

Read the documentation below and output ONLY a valid JSON object:
{{
  "tool_name": "{tool_name}",
  "description": "One sentence: what this tool does",
  "base_url": "base API URL or null",
  "auth_method": "api_key | oauth2 | basic | none | unknown",
  "capabilities": [
    {{
      "name": "python_function_name",
      "description": "What this function does",
      "http_method": "GET|POST|DELETE|null",
      "endpoint": "/path/to/endpoint or null",
      "params": [
        {{"name": "param_name", "type": "str|int|bool|dict", "required": true, "description": "..."}}
      ],
      "returns": "What the function returns"
    }}
  ]
}}

Rules:
- function names must be valid Python identifiers (snake_case)
- Include only the 5-10 most important/commonly-used capabilities
- If you cannot determine a value, use null
- No markdown, no explanation, start with {{

Documentation:
{docs}"""

_WRAPPER_GENERATION_PROMPT = """Write a Python MCP wrapper module for this tool.

Tool spec (JSON):
{spec}

Requirements:
1. Module docstring: tool name, description, import example, auth setup
2. Auth configured via environment variable (e.g. TOOLNAME_API_KEY)
3. One async function per capability in the spec
4. Each function has type hints, a one-line docstring, and returns a dict or str
5. Use httpx.AsyncClient for HTTP calls
6. Wrap all calls in try/except, log errors, return empty dict/string on failure
7. At the bottom: one simple test function def _test() that can be run standalone

Write ONLY the Python code. No explanation. No markdown fences."""

_SOP_GENERATION_PROMPT = """Write a Layer 2 SOP (Module SOP) for this tool integration.

Tool spec:
{spec}

This SOP will be injected into an LLM system prompt when an agent uses this tool.
Format:
# {tool_name} Integration SOP

## What this tool does
[one paragraph]

## When to use it
[bullet list of task types that should trigger this tool]

## Available functions
[for each capability: function name, what it does, key parameters]

## Auth setup
[how to configure credentials — env var names]

## Error handling
[what to do if the tool fails — fallbacks, what to tell the user]

## Output format
[what the functions return, how to present results to the user]

Keep it under 1000 words. Be specific."""


@dataclass
class SkillResult:
    tool_name: str
    capabilities: list[str] = field(default_factory=list)
    wrapper_path: str = ""
    sop_path: str = ""
    registry_entry: dict = field(default_factory=dict)
    sandbox_ok: bool = False
    error: str = ""
    duration_ms: int = 0


def _load_registry() -> dict:
    if _REGISTRY_PATH.exists():
        try:
            return json.loads(_REGISTRY_PATH.read_text())
        except Exception:
            pass
    return {"tools": {}}


def _save_registry(registry: dict):
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


async def _fetch_docs(source: str) -> str:
    """Fetch documentation from URL, file path, or raw text."""
    # URL
    if source.startswith("http://") or source.startswith("https://"):
        from systemOS.mcp.browser import scrape
        logger.info("[SKILL] Scraping docs from %s", source)
        text = await scrape(source, max_chars=12000)
        return text or ""

    # File path
    path = Path(source)
    if path.exists():
        if path.suffix == ".pdf":
            try:
                import pypdf2
                reader = pypdf2.PdfReader(str(path))
                return "\n".join(page.extract_text() or "" for page in reader.pages)[:12000]
            except Exception as e:
                logger.warning("[SKILL] PDF read failed: %s", e)
                return ""
        return path.read_text(encoding="utf-8", errors="replace")[:12000]

    # Treat as raw text
    return source[:12000]


async def _extract_capabilities(docs: str, tool_name: str) -> dict:
    """Call LLM to extract tool capabilities from documentation text."""
    from systemOS.llm import complete
    from systemOS.config.models import get_model

    prompt = _CAPABILITY_EXTRACTOR_PROMPT.format(tool_name=tool_name, docs=docs[:8000])
    model = get_model("precise")["model"]

    logger.info("[SKILL] Extracting capabilities for %s", tool_name)
    raw = await complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        model=model,
    )

    # Strip accidental markdown fences
    raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[SKILL] Capability extraction JSON failed: %s\n%s", e, raw[:300])
        return {"tool_name": tool_name, "description": "", "capabilities": []}


async def _generate_wrapper(spec: dict) -> str:
    """Generate the Python MCP wrapper code from the spec."""
    from systemOS.agents.coder import code_task
    from pathlib import Path as _Path

    task_description = (
        f"Write an async Python MCP wrapper for the {spec.get('tool_name', 'tool')} API.\n\n"
        f"Spec:\n{json.dumps(spec, indent=2)}"
    )

    # Use the full coder loop (lint + sandbox test)
    result = await code_task(
        task=task_description,
        max_retries=2,
        skip_tests=True,  # wrapper just needs to import cleanly, not have full tests
    )
    return result.code


async def _generate_sop(spec: dict, tool_name: str) -> str:
    """Generate the Layer 2 SOP markdown for this tool."""
    from systemOS.llm import complete

    prompt = _SOP_GENERATION_PROMPT.format(
        spec=json.dumps(spec, indent=2),
        tool_name=tool_name,
    )
    return await complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
    )


async def _verify_wrapper(wrapper_code: str) -> bool:
    """Run the wrapper through the sandbox — verify it at least imports cleanly."""
    from systemOS.mcp.terminal import run_python
    result = await run_python(
        f"# Syntax + import check\n{wrapper_code}\nprint('import ok')",
        timeout=30,
    )
    return result.ok


async def acquire_skill(
    source: str,
    tool_name: str | None = None,
    output_dir: Path | None = None,
    sop_dir: Path | None = None,
) -> SkillResult:
    """
    Full skill acquisition pipeline:
      1. Fetch docs (URL / file / raw text)
      2. Extract capabilities with LLM
      3. Generate Python MCP wrapper (via coder agent + sandbox)
      4. Generate Layer 2 SOP
      5. Save files + register in tools_registry.json

    Args:
        source:     URL, file path, or raw documentation text
        tool_name:  snake_case name for the tool (auto-detected if None)
        output_dir: where to save the wrapper (default: systemOS/mcp/)
        sop_dir:    where to save the SOP (default: systemOS/sops/modules/)
    """
    start = time.monotonic()
    output_dir = output_dir or Path(__file__).parent.parent / "mcp"
    sop_dir = sop_dir or Path(__file__).parent.parent / "sops" / "modules"

    result = SkillResult(tool_name=tool_name or "unknown_tool")

    try:
        # Step 1: Fetch docs
        logger.info("[SKILL] Fetching docs from: %s", source[:80])
        docs = await _fetch_docs(source)
        if not docs.strip():
            result.error = "Could not fetch documentation — empty content"
            return result

        # Step 2: Extract capabilities
        spec = await _extract_capabilities(docs, tool_name or "tool")
        if not spec.get("capabilities"):
            result.error = "No capabilities extracted from documentation"
            return result

        result.tool_name = spec.get("tool_name", tool_name or "tool")
        result.capabilities = [c["name"] for c in spec["capabilities"]]
        logger.info("[SKILL] Extracted %d capabilities: %s", len(result.capabilities), result.capabilities)

        # Step 3: Generate wrapper
        logger.info("[SKILL] Generating Python wrapper…")
        wrapper_code = await _generate_wrapper(spec)

        # Step 4: Sandbox verification
        result.sandbox_ok = await _verify_wrapper(wrapper_code)
        if not result.sandbox_ok:
            logger.warning("[SKILL] Wrapper failed sandbox check — saving anyway with warning")

        # Step 5: Generate SOP
        logger.info("[SKILL] Generating SOP…")
        sop_text = await _generate_sop(spec, result.tool_name)

        # Step 6: Save files
        output_dir.mkdir(parents=True, exist_ok=True)
        sop_dir.mkdir(parents=True, exist_ok=True)

        wrapper_path = output_dir / f"{result.tool_name}.py"
        sop_path = sop_dir / f"{result.tool_name}.md"

        wrapper_path.write_text(wrapper_code)
        sop_path.write_text(sop_text)
        result.wrapper_path = str(wrapper_path)
        result.sop_path = str(sop_path)

        # Step 7: Register
        registry = _load_registry()
        entry = {
            "tool_name":    result.tool_name,
            "description":  spec.get("description", ""),
            "wrapper":      str(wrapper_path),
            "sop":          str(sop_path),
            "capabilities": result.capabilities,
            "sandbox_ok":   result.sandbox_ok,
            "acquired_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source":       source[:200],
        }
        registry["tools"][result.tool_name] = entry
        _save_registry(registry)
        result.registry_entry = entry

        result.duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "[SKILL] Acquired '%s' — %d capabilities, sandbox=%s, %dms",
            result.tool_name, len(result.capabilities), result.sandbox_ok, result.duration_ms,
        )

    except Exception as e:
        result.error = str(e)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("[SKILL] acquire_skill failed: %s", e)

    return result


def list_skills() -> list[dict]:
    """Return all registered skills from tools_registry.json."""
    return list(_load_registry().get("tools", {}).values())


def get_skill(tool_name: str) -> dict | None:
    """Return a registered skill by name."""
    return _load_registry().get("tools", {}).get(tool_name)
