"""
LLM client abstraction — switches between Ollama (local) and Anthropic based on env.

Priority:
  1. If OLLAMA_URL is set → use Ollama (Llama 3.3 or whatever model is configured)
  2. Else if ANTHROPIC_API_KEY is set → use Anthropic
  3. Else raise clearly

Usage:
    from llm import complete
    text = await complete(messages=[{"role": "user", "content": "..."}], fast=True)

    fast=True  → use the cheaper/smaller model (query generation etc)
    fast=False → use the capable model (synthesis)

Token telemetry:
    Use complete_with_usage() to get {"text", "input_tokens", "output_tokens",
    "model", "backend", "duration_ms", "cost_usd"} instead of just the text.
    This data is logged to supply.llm_call_log automatically when topic_id/call_type
    are passed to log_llm_call().
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "")
# Allow comma-separated list of models to support dual-model parallel runs
_model_env = os.getenv("OLLAMA_MODEL", "llama3.3")
OLLAMA_MODELS = [m.strip() for m in _model_env.split(",")] if "," in _model_env else [_model_env]
_ollama_idx = 0

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_FAST = "claude-haiku-4-5-20251001"
ANTHROPIC_MODEL_FULL = "claude-sonnet-4-6"

# ── Pricing (USD per million tokens) ─────────────────────────────────────────
# Update these when Anthropic changes pricing.
_ANTHROPIC_PRICING = {
    ANTHROPIC_MODEL_FAST: {"input": 0.80, "output": 4.00},
    ANTHROPIC_MODEL_FULL: {"input": 3.00, "output": 15.00},
}
USD_TO_GBP = float(os.getenv("COST_USD_TO_GBP", "0.79"))


def _compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for a call. Returns 0.0 for Ollama (local)."""
    pricing = _ANTHROPIC_PRICING.get(model)
    if not pricing:
        return 0.0
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _backend() -> str:
    if OLLAMA_URL:
        return "ollama"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    raise RuntimeError(
        "No LLM backend configured. Set OLLAMA_URL (for local Llama) or ANTHROPIC_API_KEY."
    )


async def complete(
    messages: list[dict],
    system: str | None = None,
    fast: bool = False,
    max_tokens: int = 2000,
    model: str | None = None,
) -> str:
    """
    Send messages to the configured LLM. Returns the response text.

    Args:
        messages:   list of {"role": "user"|"assistant", "content": "..."}
        system:     optional system prompt prepended to messages
        fast:       use smaller/faster model (Haiku or same Ollama model)
        max_tokens: max response length
        model:      override the model for this call (Ollama only)
    """
    result = await complete_with_usage(
        messages=messages, system=system, fast=fast, max_tokens=max_tokens, model=model
    )
    return result["text"]


async def complete_with_usage(
    messages: list[dict],
    system: str | None = None,
    fast: bool = False,
    max_tokens: int = 2000,
    model: str | None = None,
) -> dict:
    """
    Like complete() but returns a dict with full usage metadata:
        {
          "text":          str,
          "input_tokens":  int,
          "output_tokens": int,
          "model":         str,
          "backend":       str,   # "anthropic" or "ollama"
          "duration_ms":   int,
          "cost_usd":      float,
          "cost_gbp":      float,
        }
    """
    backend = _backend()

    if system:
        messages = [{"role": "system", "content": system}] + messages

    t0 = time.monotonic()
    if backend == "ollama":
        result = await _ollama(messages, max_tokens, model_override=model)
    else:
        result = await _anthropic(messages, fast, max_tokens)

    result["duration_ms"] = int((time.monotonic() - t0) * 1000)
    result["backend"] = backend
    result["cost_usd"] = _compute_cost_usd(
        result["model"], result["input_tokens"], result["output_tokens"]
    )
    result["cost_gbp"] = result["cost_usd"] * USD_TO_GBP
    logger.info(
        "[LLM] %s — %d in / %d out tokens — %.2f ms — £%.4f",
        result["model"],
        result["input_tokens"],
        result["output_tokens"],
        result["duration_ms"],
        result["cost_gbp"],
    )
    return result


async def _ollama(messages: list[dict], max_tokens: int, model_override: str | None = None) -> dict:
    global _ollama_idx
    if model_override:
        model = model_override
    else:
        # Round-robin selection
        model = OLLAMA_MODELS[_ollama_idx % len(OLLAMA_MODELS)]
        _ollama_idx += 1

    logger.info("[LLM] ollama %s — %d messages", model, len(messages))
    # Use OpenAI-compatible endpoint — supported by all Ollama versions and more stable
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        return {
            "text":          text,
            "model":         model,
            "input_tokens":  usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }


async def _anthropic(messages: list[dict], fast: bool, max_tokens: int) -> dict:
    import anthropic

    model = ANTHROPIC_MODEL_FAST if fast else ANTHROPIC_MODEL_FULL
    logger.debug("[LLM] anthropic %s", model)

    # Anthropic takes system separately, not as a message role
    system_text = None
    filtered = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            filtered.append(m)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=filtered,
    )
    if system_text:
        kwargs["system"] = system_text

    msg = client.messages.create(**kwargs)
    return {
        "text":          msg.content[0].text.strip(),
        "model":         model,
        "input_tokens":  msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }


# ── Telemetry logging ──────────────────────────────────────────────────────────

def log_llm_call(
    usage: dict,
    service: str,
    call_type: str,
    topic_id: int | None = None,
    fast: bool = False,
) -> None:
    """
    Write a usage record to supply.llm_call_log.
    Call this after complete_with_usage() in any agent that wants cost tracking.
    Silently swallows DB errors so it never breaks the main flow.
    """
    try:
        from db import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO supply.llm_call_log
                        (service, topic_id, call_type, model, backend,
                         input_tokens, output_tokens, total_tokens,
                         cost_usd, cost_gbp, duration_ms, fast)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        service,
                        topic_id,
                        call_type,
                        usage["model"],
                        usage["backend"],
                        usage["input_tokens"],
                        usage["output_tokens"],
                        usage["input_tokens"] + usage["output_tokens"],
                        usage["cost_usd"],
                        usage["cost_gbp"],
                        usage["duration_ms"],
                        fast,
                    ),
                )
    except Exception as exc:
        logger.warning("[LLM] Failed to log call to DB: %s", exc)
