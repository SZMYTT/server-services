# services/sop_assembler.py
# PrismaOS layered SOP assembler.
# Reads the three SOP layers from disk and stitches them into
# a single prompt string ready for injection into an agent call.
#
# Layer 1 — sops/system/core.md          (~500 tokens, always)
# Layer 2 — sops/modules/{module}.md     (~1500 tokens, per module)
# Layer 3 — sops/workspaces/{ws}/profile.md (~800 tokens, per workspace)
#
# Files are cached in memory and only re-read when the file on
# disk has changed (mtime check), so hot-reload works without restart.

import logging
import os
from pathlib import Path

logger = logging.getLogger("prisma.sop_assembler")

# ── Project root (one level up from this file) ────────────────

_ROOT = Path(__file__).parent.parent
_SOPS = _ROOT / "sops"

# ── File cache ────────────────────────────────────────────────
# { path: (mtime_float, content_str) }

_cache: dict[str, tuple[float, str]] = {}


def _read(path: Path) -> str | None:
    """
    Return file content, using the in-memory cache.
    Returns None if the file does not exist.
    """
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        logger.warning("[SOP] file not found: %s", path)
        return None

    if key in _cache and _cache[key][0] == mtime:
        return _cache[key][1]

    content = path.read_text(encoding="utf-8")
    _cache[key] = (mtime, content)
    logger.debug("[SOP] loaded (or reloaded): %s", path.name)
    return content


# ── Layer paths ───────────────────────────────────────────────

def _layer1_path() -> Path:
    return _SOPS / "system" / "core.md"


def _layer2_path(module: str) -> Path:
    return _SOPS / "modules" / f"{module}.md"


def _layer3_path(workspace: str) -> Path:
    return _SOPS / "workspaces" / workspace / "profile.md"


# ── Public API ────────────────────────────────────────────────

def assemble_sop(
    task_type: str,
    module: str,
    workspace: str,
) -> str:
    """
    Return the assembled SOP string for a task.

    Always includes Layer 1.
    Layer 2 falls back to task_type if no module-specific SOP exists.
    Layer 3 is omitted gracefully if the workspace profile is missing.

    Usage:
        sop = assemble_sop("research", "research", "property")
        # → pass sop as the system prompt to the LLM call
    """
    parts: list[str] = []

    # Layer 1 — system (always)
    layer1 = _read(_layer1_path())
    if layer1:
        parts.append(layer1)
    else:
        logger.error("[SOP] Layer 1 (core.md) is missing — using fallback")
        parts.append(
            "You are Prisma, a business AI assistant. Be factual, concise, "
            "and use British English. Cite all sources."
        )

    # Layer 2 — module (try module name, fall back to task_type)
    layer2 = _read(_layer2_path(module))
    if layer2 is None and module != task_type:
        layer2 = _read(_layer2_path(task_type))

    if layer2:
        parts.append(layer2)
    else:
        logger.warning(
            "[SOP] No Layer 2 found for module=%s task_type=%s — skipping",
            module, task_type,
        )

    # Layer 3 — workspace profile
    layer3 = _read(_layer3_path(workspace))
    if layer3:
        parts.append(layer3)
    else:
        logger.warning(
            "[SOP] No Layer 3 found for workspace=%s — skipping", workspace
        )

    assembled = "\n\n---\n\n".join(parts)

    logger.info(
        "[SOP] assembled task_type=%s module=%s workspace=%s layers=%d chars=%d",
        task_type, module, workspace, len(parts), len(assembled),
    )
    return assembled


def list_available_sops() -> dict:
    """
    Return a summary of which SOPs are available.
    Useful for health checks and the web UI.
    """
    modules = [
        p.stem for p in (_SOPS / "modules").glob("*.md")
    ] if (_SOPS / "modules").exists() else []

    workspaces = [
        p.parent.name
        for p in (_SOPS / "workspaces").glob("*/profile.md")
    ] if (_SOPS / "workspaces").exists() else []

    return {
        "system_core":  _layer1_path().exists(),
        "modules":      sorted(modules),
        "workspaces":   sorted(workspaces),
    }
