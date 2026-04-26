"""
LLM client abstraction — switches between Ollama (local) and Anthropic based on env.

Shared module. Import from any project:
    from systemOS.llm import complete, complete_ex

Priority:
  1. If OLLAMA_URL is set → use Ollama (configurable model)
  2. Else if ANTHROPIC_API_KEY is set → use Anthropic
  3. Else raise clearly

Usage:
    from systemOS.llm import complete, complete_ex

    # Simple call — returns text string
    text = await complete(messages=[{"role": "user", "content": "..."}], fast=True)

    # Extended call — returns text + token usage + model info
    result = await complete_ex(messages=[...])
    print(result["text"])           # response string
    print(result["tokens"])         # {"prompt": 120, "completion": 340, "total": 460}
    print(result["model"])          # "llama3.3:70b"
    print(result["backend"])        # "ollama" | "anthropic"

    fast=True  → cheaper/smaller model (query generation, classification)
    fast=False → capable model (synthesis, extraction)
    model=...  → override model for this call (Ollama only)
"""

import logging
import os
from typing import TypedDict

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.3")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL_FAST = "claude-haiku-4-5-20251001"
ANTHROPIC_MODEL_FULL = "claude-sonnet-4-6"


class LLMResult(TypedDict):
    text: str
    tokens: dict      # {"prompt": int, "completion": int, "total": int}
    model: str
    backend: str      # "ollama" | "anthropic"


def _backend() -> str:
    if OLLAMA_URL:
        return "ollama"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    raise RuntimeError(
        "No LLM backend configured. Set OLLAMA_URL (for local Ollama) or ANTHROPIC_API_KEY."
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
        system:     optional system prompt (prepended as system role)
        fast:       use smaller/faster model (Haiku or same Ollama model)
        max_tokens: max response length
        model:      override the model for this call (Ollama only)
    """
    result = await complete_ex(messages=messages, system=system, fast=fast,
                               max_tokens=max_tokens, model=model)
    return result["text"]


async def complete_ex(
    messages: list[dict],
    system: str | None = None,
    fast: bool = False,
    max_tokens: int = 2000,
    model: str | None = None,
) -> LLMResult:
    """
    Like complete() but returns the full result including token counts and model info.
    Use this when you want to log costs or track usage.
    """
    backend = _backend()

    if system:
        messages = [{"role": "system", "content": system}] + messages

    if backend == "ollama":
        return await _ollama(messages, max_tokens, model_override=model)
    else:
        return await _anthropic(messages, fast, max_tokens)


async def _ollama(
    messages: list[dict],
    max_tokens: int,
    model_override: str | None = None,
) -> LLMResult:
    model = model_override or OLLAMA_MODEL
    logger.info("[LLM] ollama %s — %d messages", model, len(messages))
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
    tokens = {
        "prompt":     usage.get("prompt_tokens", 0),
        "completion": usage.get("completion_tokens", 0),
        "total":      usage.get("total_tokens", 0),
    }
    logger.debug("[LLM] ollama tokens — prompt=%d completion=%d", tokens["prompt"], tokens["completion"])
    return LLMResult(text=text, tokens=tokens, model=model, backend="ollama")


async def _anthropic(
    messages: list[dict],
    fast: bool,
    max_tokens: int,
) -> LLMResult:
    import anthropic

    model = ANTHROPIC_MODEL_FAST if fast else ANTHROPIC_MODEL_FULL
    logger.debug("[LLM] anthropic %s", model)

    system_text = None
    filtered = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            filtered.append(m)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    kwargs = dict(model=model, max_tokens=max_tokens, messages=filtered)
    if system_text:
        kwargs["system"] = system_text

    msg = client.messages.create(**kwargs)
    text = msg.content[0].text.strip()
    tokens = {
        "prompt":     msg.usage.input_tokens,
        "completion": msg.usage.output_tokens,
        "total":      msg.usage.input_tokens + msg.usage.output_tokens,
    }
    logger.debug("[LLM] anthropic tokens — input=%d output=%d", tokens["prompt"], tokens["completion"])
    return LLMResult(text=text, tokens=tokens, model=model, backend="anthropic")
