"""
Research depth presets — shared across all projects.

Import from any project:
    from systemOS.config.depth import get as get_depth, choices as depth_choices

Controls how long research/scraping runs, how much content is read per page,
how many sources are gathered, and how the LLM is instructed to behave.

Depth affects:
  - max_iterations  : max LLM tool-call rounds (agentic loops)
  - time_budget_s   : hard wall-clock cutoff in seconds
  - page_chars      : markdown chars kept per scraped page
  - max_scrape      : pages scraped in parallel (research agent)
  - n_queries       : search queries generated (research agent)
  - n_results       : SearXNG results per query (research agent)
  - synthesis_tokens: max tokens for the final synthesis LLM call
  - label           : human-readable label
  - est_minutes     : estimated wall time (shown in UI)
"""

DEPTH_CONFIG: dict[str, dict] = {
    "quick": {
        "label":            "Quick scan",
        "est_minutes":      5,
        "time_budget_s":    5 * 60,
        "max_iterations":   8,
        "page_chars":       3000,
        "max_scrape":       3,
        "n_queries":        3,
        "n_results":        3,
        "synthesis_tokens": 2000,
        "agent_instruction": "Be efficient. Cover homepage, one product page, and delivery terms. Call done quickly.",
    },
    "standard": {
        "label":            "Standard",
        "est_minutes":      15,
        "time_budget_s":    15 * 60,
        "max_iterations":   20,
        "page_chars":       5000,
        "max_scrape":       5,
        "n_queries":        4,
        "n_results":        5,
        "synthesis_tokens": 4000,
        "agent_instruction": "Cover homepage, about, delivery, trade terms, and all requested products. Do 2-3 web searches for alternatives.",
    },
    "deep": {
        "label":            "Deep dive",
        "est_minutes":      30,
        "time_budget_s":    30 * 60,
        "max_iterations":   40,
        "page_chars":       8000,
        "max_scrape":       8,
        "n_queries":        6,
        "n_results":        8,
        "synthesis_tokens": 6000,
        "agent_instruction": (
            "Be thorough. Explore the full site structure. Find every product variant. "
            "Check all pricing pages. Do multiple web searches for alternatives, upstream suppliers, "
            "and competitors. Explore trade/wholesale sections fully. Confidence should be 8+ before calling done."
        ),
    },
    "thorough": {
        "label":            "Thorough research",
        "est_minutes":      60,
        "time_budget_s":    60 * 60,
        "max_iterations":   70,
        "page_chars":       12000,
        "max_scrape":       12,
        "n_queries":        8,
        "n_results":        10,
        "synthesis_tokens": 8000,
        "agent_instruction": (
            "Do exhaustive research. Cover every relevant page on the site. "
            "Find all product variants, all pricing tiers, all delivery options. "
            "Identify the upstream supply chain. Find at least 5 alternative suppliers via web search. "
            "Cross-reference product specs across sources. Do not call done until confidence is 9+."
        ),
    },
    "titan": {
        "label":            "Titan (12h agentic)",
        "est_minutes":      120,
        "time_budget_s":    60 * 60 * 12,
        "max_iterations":   120,
        "page_chars":       20000,
        "max_scrape":       30,
        "n_queries":        15,
        "n_results":        10,
        "synthesis_tokens": 12000,
        "agent_instruction": (
            "Exhaustive research. Document every technical nuance, benchmark, vendor, and data point. "
            "Leave no sub-topic unexplored. Cite every claim. "
            "Target 10,000+ words of dense, structured output."
        ),
    },
}

DEFAULT_DEPTH = "standard"


def get(depth: str) -> dict:
    """Return depth config dict for the given key, falling back to standard."""
    return DEPTH_CONFIG.get(depth, DEPTH_CONFIG[DEFAULT_DEPTH])


def choices() -> list[tuple[str, str, int]]:
    """Return list of (key, label, est_minutes) for UI dropdowns."""
    return [(k, v["label"], v["est_minutes"]) for k, v in DEPTH_CONFIG.items()]
