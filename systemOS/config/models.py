"""
Model routing configuration for systemOS.

Centralises the model catalogue and task→model mapping so any project
can ask "which Ollama model should I use for this?" without importing
the full async router.

Import from any project:
    from systemOS.config.models import get_model, MODELS

Usage:
    cfg = get_model("research")
    # → {"model": "llama3.3:70b", "ollama_url": "...", "timeout_secs": 900}

    cfg = get_model("research", module="document_analyser")
    # → {"model": "qwen2.5:14b", ...}  (module override applied)

    model_name = get_model("fast")["model"]
    # → "llama3.2:3b"
"""

import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://100.76.139.41:11434")

# ── Model catalogue ───────────────────────────────────────────
# All models run on MacBook Pro M1 Max (64GB unified memory via Tailscale).
# (model_name, timeout_seconds, description)

MODELS: dict[str, dict] = {
    "orchestrator": {
        "model":        "llama3.3:70b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "task decomposition, routing, complex reasoning",
    },
    "researcher": {
        "model":        "llama3.3:70b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "web research, synthesis, long context",
    },
    "coder": {
        "model":        "qwen2.5-coder:32b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "code generation, review, refactoring",
    },
    "content": {
        "model":        "mistral:22b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "social copy, ad copy, brand voice",
    },
    "finance": {
        "model":        "phi4:14b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "structured output, document analysis, financial reasoning",
    },
    "fast": {
        "model":        "llama3.2:3b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 120,
        "use":          "instant replies, classification, routing, comms",
    },
    "legal": {
        "model":        "gemma3:27b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "legal compliance, complex reasoning, regulated content",
    },
    "precise": {
        "model":        "qwen2.5:14b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "structured data extraction, precise parsing",
    },
    "vision": {
        "model":        "llava:13b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "image analysis, receipts, photos, screenshots",
    },
    "embed": {
        "model":        "nomic-embed-text",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 30,
        "use":          "semantic embeddings for vector memory / RAG",
    },

    # ── Expert Panel models ───────────────────────────────────
    # Pull on Mac: ollama pull gemma2:27b && ollama pull gemma2:9b
    # VRAM: ~18GB + ~7GB + ~7GB = ~32GB total, comfortable on 64GB M1 Max
    "architect": {
        "model":        "gemma2:27b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "Expert Panel Architect — expansive thinking, full solution generation",
    },
    "auditor": {
        "model":        "gemma2:9b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "Expert Panel Auditor — adversarial red-team, flaw detection",
    },
    "refiner": {
        "model":        "gemma2:9b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "Expert Panel Refiner — final polish, brand voice, format compliance",
    },
}

# ── Task-type → model key ─────────────────────────────────────
TASK_TYPE_MODEL: dict[str, str] = {
    "research":   "researcher",
    "content":    "content",
    "finance":    "finance",
    "comms":      "fast",
    "legal":      "legal",
    "website":    "content",
    "document":   "precise",
    "auction":    "researcher",
    "action":     "orchestrator",
    "code":       "coder",
    "vision":     "vision",
    "embed":      "embed",
    "vendor":     "researcher",
    "inventory":  "fast",
    "classify":   "fast",
    "architect":  "architect",
    "auditor":    "auditor",
    "refiner":    "refiner",
}

# ── Expert Panel configs ──────────────────────────────────────
# Which task types and risk levels trigger the Expert Panel flow.
EXPERT_PANEL_TASK_TYPES = {"research", "finance", "legal", "content", "code"}
EXPERT_PANEL_RISK_LEVELS = {"high", "financial", "critical"}

def should_use_expert_panel(task: dict) -> bool:
    """Return True if this task should be routed through the Expert Panel."""
    return (
        task.get("routing_type") == "expert_panel"
        or task.get("risk_level") in EXPERT_PANEL_RISK_LEVELS
    )

# ── Module-level overrides ────────────────────────────────────
# Module is more specific than task_type — override when needed.
MODULE_MODEL: dict[str, str] = {
    "auction_sourcing":  "researcher",
    "customer_comms":    "fast",
    "inventory":         "fast",
    "coder":             "coder",
    "finance":           "finance",
    "analytics":         "researcher",
    "document_analyser": "precise",
    "legal_compliance":  "legal",
    "website":           "content",
    "vendor_agent":      "researcher",
    "vendor_scraper":    "precise",
    "topic_research":    "researcher",
}


def get_model(task_type: str, module: str | None = None) -> dict:
    """
    Return the model config for a given task type and optional module.

    Returns a dict: {model, ollama_url, timeout_secs, use}
    Falls back to "researcher" if task_type is unrecognised.
    """
    key = MODULE_MODEL.get(module) or TASK_TYPE_MODEL.get(task_type) or "researcher"
    return MODELS[key]


def model_name(task_type: str, module: str | None = None) -> str:
    """Shorthand — just returns the model name string."""
    return get_model(task_type, module)["model"]
