# services/retry.py
# PrismaOS async retry decorator with exponential backoff.
# Apply to any LLM inference call to handle transient Ollama blips.
#
# Usage:
#   from services.retry import with_retry
#
#   @with_retry(max_attempts=3)
#   async def call_ollama(...):
#       ...

import asyncio
import logging
import functools
from typing import Callable, Any

logger = logging.getLogger("prisma.retry")


def with_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """
    Decorator: retry an async function up to max_attempts times.
    Delays: 1s → 5s → 30s (exponential, capped at 30s).
    On permanent failure, re-raises the last exception.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> Any:
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "[RETRY] %s failed permanently after %d attempts: %s",
                            fn.__name__, max_attempts, exc
                        )
                        raise
                    delay = min(base_delay * (5 ** (attempt - 1)), 30.0)
                    logger.warning(
                        "[RETRY] %s attempt %d/%d failed (%s). Retrying in %.0fs...",
                        fn.__name__, attempt, max_attempts, exc, delay
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # unreachable, satisfies type checkers
        return wrapper
    return decorator


async def call_ollama_with_retry(
    client,
    ollama_url: str,
    model: str,
    prompt: str,
    timeout: float = 600,
    format_json: bool = False,
    max_attempts: int = 3,
) -> str:
    """
    Convenience wrapper: call Ollama /api/generate with retries built in.
    Returns the response string or raises on permanent failure.
    """
    import httpx

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if format_json:
        payload["format"] = "json"

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(f"{ollama_url}/api/generate", json=payload)
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                return resp.json().get("response", "").strip()
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                logger.error(
                    "[RETRY] Ollama call failed permanently after %d attempts: %s",
                    max_attempts, exc
                )
                raise
            delay = min(1.0 * (5 ** (attempt - 1)), 30.0)
            logger.warning(
                "[RETRY] Ollama attempt %d/%d failed (%s). Retrying in %.0fs...",
                attempt, max_attempts, exc, delay
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore
