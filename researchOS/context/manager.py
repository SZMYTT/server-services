"""
Project context manager — discovers projects in the server-services directory,
lets the user select which ones to load, and assembles them into a system prompt.

Projects are discovered by looking for CLAUDE.md files one level up.

Usage:
    from context.manager import ContextManager

    cm = ContextManager()
    cm.interactive_select()          # CLI prompt to choose projects
    system_prompt = cm.build_prompt()
"""

import os
from pathlib import Path

# Root directory containing all projects
PROJECTS_ROOT = Path(__file__).parent.parent.parent


def discover_projects() -> dict[str, Path]:
    """Find all directories under PROJECTS_ROOT that contain a CLAUDE.md."""
    projects = {}
    for item in sorted(PROJECTS_ROOT.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            claude_md = item / "CLAUDE.md"
            if claude_md.exists():
                projects[item.name] = claude_md
    return projects


def load_context(claude_md_path: Path) -> str:
    """Read a CLAUDE.md and return its content."""
    return claude_md_path.read_text().strip()


class ContextManager:
    def __init__(self):
        self.available = discover_projects()
        self.active: set[str] = set()

    def interactive_select(self, preselect: list[str] | None = None):
        """
        CLI prompt to choose which projects to load into context.
        preselect: project names to pre-tick (e.g. ["researchOS"])
        """
        if not self.available:
            print("No projects with CLAUDE.md found.")
            return

        if preselect:
            self.active = {p for p in preselect if p in self.available}

        print("\n── Project context selector ──────────────────────────")
        print("Space to toggle, Enter to confirm.\n")

        names = list(self.available.keys())
        for i, name in enumerate(names, 1):
            marker = "[x]" if name in self.active else "[ ]"
            print(f"  {i}. {marker} {name}")

        print("\nEnter numbers to toggle (e.g. 1 3), or press Enter to keep current:")
        choice = input("> ").strip()

        if choice:
            try:
                indices = [int(x) - 1 for x in choice.split()]
                for idx in indices:
                    if 0 <= idx < len(names):
                        name = names[idx]
                        if name in self.active:
                            self.active.discard(name)
                        else:
                            self.active.add(name)
            except ValueError:
                pass

        print(f"\nLoaded: {', '.join(sorted(self.active)) or 'none'}\n")

    def activate(self, *project_names: str):
        """Activate specific projects by name."""
        for name in project_names:
            if name in self.available:
                self.active.add(name)

    def deactivate(self, *project_names: str):
        """Deactivate specific projects."""
        for name in project_names:
            self.active.discard(name)

    def build_prompt(self) -> str:
        """Assemble system prompt from all active project CLAUDE.md files."""
        if not self.active:
            return ""

        parts = []
        for name in sorted(self.active):
            path = self.available[name]
            content = load_context(path)
            parts.append(f"## Project: {name}\n\n{content}")

        return (
            "You have context for the following projects. "
            "Use this when answering questions or generating research.\n\n"
            + "\n\n---\n\n".join(parts)
        )

    def summary(self) -> str:
        if not self.active:
            return "No project context loaded."
        return f"Active context: {', '.join(sorted(self.active))}"

    def detect_project_switch(self, message: str) -> list[str]:
        """
        Heuristic: return project names mentioned in a message that aren't currently active.
        Used to suggest context switches.
        """
        suggestions = []
        lower = message.lower()
        for name in self.available:
            if name.lower() in lower and name not in self.active:
                suggestions.append(name)
        return suggestions
