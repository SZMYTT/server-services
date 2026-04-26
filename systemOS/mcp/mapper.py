"""
Project map generator — produces a structural snapshot of a codebase
for injection into the LLM system prompt.

Local LLMs struggle when they don't know the file layout. This tool walks
a project directory, extracts each Python file's one-line purpose, and
builds a compact map that fits in the system layer without wasting tokens.

Import from any project:
    from systemOS.mcp.mapper import generate_map, map_for_prompt

Usage:
    # Generate and print a full project map
    from pathlib import Path
    print(generate_map(Path("/home/szmyt/server-services/researchOS")))

    # Get a compact string ready to inject into an LLM system prompt
    context = map_for_prompt(Path("/home/szmyt/server-services/researchOS"))

    # Cache to disk (only regenerates if files have changed)
    context = map_for_prompt(Path("."), cache=True)

The map has two sections:
    1. Directory tree (skips venv, __pycache__, .git, migrations)
    2. File index — path + one-line purpose extracted from docstring or first comment
"""

import ast
import hashlib
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories to skip entirely
SKIP_DIRS = {
    "venv", ".venv", "__pycache__", ".git", ".mypy_cache",
    ".ruff_cache", "node_modules", "dist", "build", ".eggs",
    "migrations", "playwright-browsers", "site-packages",
}

# File extensions to include in the file index
INDEX_EXTENSIONS = {".py", ".md", ".sql", ".yaml", ".yml", ".toml", ".env.example"}

MAX_MAP_TOKENS = 1500  # approximate — keep the map lean


def _skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _extract_purpose(filepath: Path) -> str:
    """Extract a one-line purpose from a Python file's module docstring or first comment."""
    if filepath.suffix != ".py":
        return ""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        if docstring:
            # First non-empty line of the docstring
            for line in docstring.splitlines():
                line = line.strip()
                if line:
                    return line[:100]
        # Fall back to first comment line
        for line in source.splitlines()[:5]:
            line = line.strip()
            if line.startswith("#") and len(line) > 2:
                return line[1:].strip()[:100]
    except Exception:
        pass
    return ""


def _tree_lines(root: Path, prefix: str = "", max_depth: int = 4, depth: int = 0) -> list[str]:
    if depth > max_depth:
        return []
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return []

    entries = [e for e in entries if not _skip_path(e.relative_to(root.parent)
                                                     if root.parent != root else e)]

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir() and entry.name not in SKIP_DIRS:
            extension = "    " if is_last else "│   "
            lines.extend(_tree_lines(entry, prefix + extension, max_depth, depth + 1))
    return lines


def generate_map(project_root: Path, max_depth: int = 4) -> str:
    """
    Generate a full project map string with directory tree + file index.
    """
    root = project_root.resolve()
    if not root.exists():
        return f"[mapper] path not found: {root}"

    lines = [f"# Project map: {root.name}", f"# Generated: {time.strftime('%Y-%m-%d %H:%M')}", ""]

    # ── Directory tree ─────────────────────────────────────────
    lines.append("## Structure")
    lines.append(f"{root.name}/")
    lines.extend(_tree_lines(root, max_depth=max_depth))
    lines.append("")

    # ── File index ─────────────────────────────────────────────
    lines.append("## Files")
    file_entries = []
    for filepath in sorted(root.rglob("*")):
        if filepath.is_file() and not _skip_path(filepath) and filepath.suffix in INDEX_EXTENSIONS:
            rel = filepath.relative_to(root)
            purpose = _extract_purpose(filepath)
            file_entries.append((str(rel), purpose))

    for rel, purpose in file_entries:
        if purpose:
            lines.append(f"  {rel}: {purpose}")
        else:
            lines.append(f"  {rel}")

    return "\n".join(lines)


def map_for_prompt(
    project_root: Path,
    cache: bool = True,
    max_depth: int = 3,
) -> str:
    """
    Return a compact project map suitable for injection into an LLM system prompt.

    If cache=True, writes to <project_root>/.project_map_cache.txt and only
    regenerates when the set of Python files has changed (checked via hash).
    """
    root = project_root.resolve()
    cache_file = root / ".project_map_cache.txt"
    hash_file = root / ".project_map_hash.txt"

    if cache:
        # Compute a lightweight hash of all Python file mtimes
        py_files = sorted(f for f in root.rglob("*.py") if not _skip_path(f))
        fingerprint = hashlib.md5(
            "".join(f"{f}:{os.path.getmtime(f)}" for f in py_files).encode()
        ).hexdigest()

        if cache_file.exists() and hash_file.exists():
            if hash_file.read_text().strip() == fingerprint:
                logger.debug("[MAPPER] returning cached map for %s", root.name)
                return cache_file.read_text()

    map_text = generate_map(root, max_depth=max_depth)

    # Trim to token budget — rough 4 chars/token estimate
    char_budget = MAX_MAP_TOKENS * 4
    if len(map_text) > char_budget:
        map_text = map_text[:char_budget] + "\n... (map truncated)"

    if cache:
        try:
            cache_file.write_text(map_text)
            hash_file.write_text(fingerprint)
        except Exception as e:
            logger.warning("[MAPPER] cache write failed: %s", e)

    return map_text


def map_as_system_block(project_root: Path) -> str:
    """
    Wrap the map in a clearly delimited block for system prompt injection.
    Paste this directly into the system= argument of complete().
    """
    map_text = map_for_prompt(project_root)
    return f"<project_map>\n{map_text}\n</project_map>"
