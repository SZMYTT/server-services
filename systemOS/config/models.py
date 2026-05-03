"""
Model routing configuration for systemOS.
All models run on MacBook Pro M1 Max 64GB via Tailscale (http://100.76.139.41:11434).

Active gemma4 stack:
  gemma4:31b-it-q8_0    31B  Q8   33.8GB  — highest quality, orchestration + research
  nnl-agent-gemma       26B  Q4   18.0GB  — NNL fine-tune, content + general agent
  nnl-planner           26B  Q4   18.0GB  — NNL planner fine-tune, mapmaker
  gemma4:26b            26B  Q4   18.0GB  — standard, finance / legal / precise
  gemma4-fast           26B  Q4   18.0GB  — fast NNL fine-tune, comms / routing
  gemma4:latest          8B  Q4    9.6GB  — smallest, adversarial / auditor
  nnl-coder             32B  Q4   19.9GB  — qwen2.5 coder fine-tune (not gemma4)

VRAM budget (peak, Expert Panel):
  architect (31B Q8 = 33.8GB) + auditor (8B = 9.6GB) parallel = 43.4GB  ✓
  refiner   (26B Q4 = 18.0GB) sequential after              = fine
"""

import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://100.76.139.41:11434")

MODELS: dict[str, dict] = {
    # ── gemma4 31B Q8 — best quality ─────────────────────────
    "orchestrator": {
        "model":        "gemma4:31b-it-q8_0",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "task decomposition, routing, complex reasoning",
    },
    "researcher": {
        "model":        "gemma4:31b-it-q8_0",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "web research, synthesis, long context",
    },
    "architect": {
        "model":        "gemma4:31b-it-q8_0",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 900,
        "use":          "Expert Panel Architect — expansive thinking, full solution generation",
    },

    # ── NNL gemma4 fine-tunes (26B Q4) ───────────────────────
    "content": {
        "model":        "nnl-agent-gemma:latest",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "social copy, ad copy, brand voice",
    },
    "mapmaker": {
        "model":        "gemma4:31b-it-q8_0",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "topic decomposition into volumes and chapters",
    },

    # ── gemma4 26B standard ───────────────────────────────────
    "finance": {
        "model":        "gemma4:26b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "structured output, document analysis, financial reasoning",
    },
    "legal": {
        "model":        "gemma4:26b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "legal compliance, complex reasoning, regulated content",
    },
    "precise": {
        "model":        "gemma4:26b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "structured data extraction, precise parsing",
    },
    "refiner": {
        "model":        "gemma4:26b",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "Expert Panel Refiner — final polish, brand voice, format compliance",
    },

    # ── gemma4-fast NNL fine-tune (26B Q4) ───────────────────
    "fast": {
        "model":        "gemma4-fast:latest",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 120,
        "use":          "instant replies, classification, routing, comms",
    },

    # ── gemma4 8B — lightweight ───────────────────────────────
    "auditor": {
        "model":        "gemma4:latest",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 300,
        "use":          "Expert Panel Auditor — adversarial red-team, flaw detection",
    },

    # ── Coder (qwen2.5 fine-tune, not gemma4) ────────────────
    "coder": {
        "model":        "nnl-coder:latest",
        "ollama_url":   OLLAMA_URL,
        "timeout_secs": 600,
        "use":          "code generation, review, refactoring",
    },

    # ── Pull when needed ──────────────────────────────────────
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
    "mapmaker":   "mapmaker",
}

# ── Expert Panel configs ──────────────────────────────────────
EXPERT_PANEL_TASK_TYPES = {"research", "finance", "legal", "content", "code"}
EXPERT_PANEL_RISK_LEVELS = {"high", "financial", "critical"}

def should_use_expert_panel(task: dict) -> bool:
    return (
        task.get("routing_type") == "expert_panel"
        or task.get("risk_level") in EXPERT_PANEL_RISK_LEVELS
    )

# ── Module-level overrides ────────────────────────────────────
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
