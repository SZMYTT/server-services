# services/router.py
# PrismaOS task router.
# Maps a task dict → { model, ollama_url, timeout_secs, lane }
# so any agent can call the right inference host without knowing
# about the hardware layout.
#
# ALL inference runs on the MacBook Pro (M1 Max, 64GB).
# The gaming PC is development-only (VS Code / SSH). Never route to it.
#
# Model catalogue (from environment.yaml):
#   orchestrator / researcher  → llama3.3:70b   @ macbook-pro
#   coder                      → qwen2.5-coder:32b @ macbook-pro
#   content                    → mistral:22b    @ macbook-pro
#   finance / documents        → phi4:14b       @ macbook-pro
#   fast / comms / routing     → llama3.2:3b    @ macbook-pro
#
# Fallback on Mac unreachable: queue task and wait. Do NOT drop silently.

import asyncio
import logging
import os
from functools import partial

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("prisma.router")

# Global VRAM Management: Prevent OOM on heavy models
VRAM_SEMAPHORE = asyncio.Semaphore(2)

# ── Inference hosts ───────────────────────────────────────────

OLLAMA_MAC_URL = os.getenv("OLLAMA_MAC_URL", "http://macbook-pro:11434")

# ── Model catalogue ───────────────────────────────────────────

# (model_name, ollama_url, timeout_seconds)
MODELS = {
    # All models run on MacBook M1 Max (64GB unified memory)
    "orchestrator":  ("llama3.3:70b",         OLLAMA_MAC_URL, 900),
    "researcher":    ("llama3.3:70b",         OLLAMA_MAC_URL, 900),
    "coder":         ("qwen2.5-coder:32b",    OLLAMA_MAC_URL, 600),
    "content":       ("mistral:22b",          OLLAMA_MAC_URL, 600),
    "finance":       ("phi4:14b",             OLLAMA_MAC_URL, 300),
    "fast":          ("llama3.2:3b",          OLLAMA_MAC_URL, 120),
    # Extended catalogue (Phase 2.6)
    "legal":         ("gemma3:27b",           OLLAMA_MAC_URL, 600),
    "precise":       ("qwen2.5:14b",          OLLAMA_MAC_URL, 300),
    "vision":        ("llava:13b",            OLLAMA_MAC_URL, 300),
    "embed":         ("nomic-embed-text",     OLLAMA_MAC_URL,  30),
}

# ── Task-type → model key ─────────────────────────────────────
# Maps task_type and/or module to a model key above.

TASK_TYPE_MODEL = {
    "research":    "researcher",
    "content":     "content",
    "finance":     "finance",
    "comms":       "fast",
    "legal":       "finance",
    "website":     "content",
    "document":    "finance",
    "auction":     "researcher",
    "action":      "orchestrator",
}

MODULE_MODEL_OVERRIDE = {
    "auction_sourcing":   "researcher",
    "customer_comms":     "fast",
    "inventory":          "fast",
    "coder":              "coder",
    "finance":            "finance",
    "analytics":          "researcher",
    "document_analyser":  "precise",    # qwen2.5:14b better at structured data
    "legal_compliance":   "legal",      # gemma3:27b for legal reasoning
    "website":            "content",
    "content":            "content",
}


# ── Reachability cache ────────────────────────────────────────
# Avoid hammering hosts with health checks on every task.
# Cache result for 60 seconds.

_reachability_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 60.0  # seconds


async def _host_reachable(base_url: str) -> bool:
    """Check if an Ollama host responds to /api/tags within 3 seconds."""
    import time
    now = time.monotonic()
    cached = _reachability_cache.get(base_url)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            ok = resp.status_code == 200
    except Exception:
        ok = False

    _reachability_cache[base_url] = (ok, now)
    if not ok:
        logger.warning("[ROUTER] host unreachable: %s", base_url)
    return ok


# ── Public API ────────────────────────────────────────────────

async def route_task(
    task_type: str,
    module: str | None = None,
    queue_lane: str | None = None,
    risk_level: str | None = None,
) -> dict:
    """
    Return a routing dict for a task:

        {
            "model":        "llama3.3:70b",
            "ollama_url":   "http://macbook-pro:11434",
            "timeout_secs": 900,
            "lane":         "standard",
            "host":         "macbook-pro",
        }

    Falls back gracefully if the primary host is unreachable.
    """
    # Determine model key — module overrides task_type for specificity
    model_key = (
        MODULE_MODEL_OVERRIDE.get(module)
        or TASK_TYPE_MODEL.get(task_type)
        or "researcher"               # safe default
    )

    # Force fast lane for urgent / financial risk tasks
    if queue_lane == "urgent" or risk_level == "financial":
        if model_key not in ("fast", "fast_coder"):
            # Keep heavy model but use fast host as fallback only if mac down
            pass

    # Force fast model for fast lane
    if queue_lane == "fast":
        model_key = "fast"

    model_name, ollama_url, timeout = MODELS[model_key]

    # Reachability fallback
    if not await _host_reachable(ollama_url):
        fallback_key = _fallback(model_key)
        if fallback_key != model_key:
            logger.warning(
                "[ROUTER] %s unreachable, falling back: %s → %s",
                ollama_url, model_key, fallback_key,
            )
            model_key  = fallback_key
            model_name, ollama_url, timeout = MODELS[model_key]

    # Derive lane from model key if not supplied
    inferred_lane = (
        queue_lane
        or ("fast" if model_key in ("fast", "fast_coder") else "standard")
    )

    # Derive human-readable host name
    host = "macbook-pro" if OLLAMA_MAC_URL in ollama_url else "gaming-pc"

    result = {
        "model":        model_name,
        "ollama_url":   ollama_url,
        "timeout_secs": timeout,
        "lane":         inferred_lane,
        "host":         host,
        "model_key":    model_key,
    }

    logger.info(
        "[ROUTER] task_type=%s module=%s → model=%s host=%s lane=%s",
        task_type, module, model_name, host, inferred_lane,
    )
    return result


def _fallback(model_key: str) -> str:
    """
    All models are on the MacBook. If it is unreachable, there is no
    fallback host — the task stays queued until the Mac comes back.
    Return the same key so the caller can detect no change occurred.
    """
    return model_key


# ── Convenience helpers ───────────────────────────────────────

async def get_ollama_models(ollama_url: str) -> list[str]:
    """Return list of model names available on a given Ollama host."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        logger.warning("[ROUTER] get_ollama_models failed: %s", e)
    return []


async def check_all_hosts() -> dict[str, bool]:
    """Health-check all inference hosts. Used by web UI / monitoring."""
    result = await _host_reachable(OLLAMA_MAC_URL)
    return {"macbook-pro": result}
