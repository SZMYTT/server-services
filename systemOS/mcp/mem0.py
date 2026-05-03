"""
mem0 agent memory — persistent conversation memory and fact extraction.

This is the agent-memory layer (conversation history, user preferences, agent observations).
For document/finding storage, use systemOS.mcp.memory (ChromaDB-based).

Two layers working together:
  memory.py  → ChromaDB  → stores research findings, vendor profiles, document chunks
  mem0.py    → Qdrant + Neo4j → stores agent memories, conversation facts, user preferences

Import from any project:
    from systemOS.mcp.mem0 import remember, recall, recall_as_context, remember_conversation, wipe

Setup (.env):
    QDRANT_HOST=localhost
    QDRANT_PORT=6333
    NEO4J_URI=bolt://localhost:7687
    NEO4J_USER=neo4j
    NEO4J_PASSWORD=password
    OLLAMA_URL=http://100.76.139.41:11434
    MEMORY_EMBED_MODEL=nomic-embed-text   # pull: ollama pull nomic-embed-text
    MEMORY_EMBED_DIMS=768

Install:
    pip install mem0ai qdrant-client

Usage:
    # Store a fact after a task
    await remember(
        "Daniel prefers research reports in bullet-point format, max 500 words",
        user_id="daniel", agent_id="researcher",
    )

    # Store a full conversation turn (mem0 extracts key facts automatically)
    await remember_conversation([
        {"role": "user",      "content": "Research Carvansons Ltd pricing"},
        {"role": "assistant", "content": "Carvansons offers tiered pricing: 1L=£12, 5L=£9/L..."},
    ], user_id="daniel", agent_id="researcher")

    # Recall before starting a task — inject into LLM system prompt
    context = await recall_as_context("Carvansons pricing", user_id="daniel")
    # → "Relevant past context:\\n  1. Carvansons 5L price is £9/L (relevance: 0.94)\\n..."

    system_prompt = f\"\"\"{base_sop}

{context}\"\"\"

    # Delete all memories for a user
    await wipe(user_id="daniel")
"""

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Config from env ────────────────────────────────────────────
_QDRANT_HOST    = os.getenv("QDRANT_HOST", "localhost")
_QDRANT_PORT    = int(os.getenv("QDRANT_PORT", "6333"))
_NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
_NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
_OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://100.76.139.41:11434")
_EMBED_MODEL    = os.getenv("MEMORY_EMBED_MODEL", "nomic-embed-text")
_LLM_MODEL      = os.getenv("OLLAMA_MODEL", "gemma4:26b")
_COLLECTION     = os.getenv("MEM0_COLLECTION", "systemos_agents")
_EMBED_DIMS     = int(os.getenv("MEMORY_EMBED_DIMS", "768"))  # nomic-embed-text = 768

_mem0_instance: Any = None


def _build_config() -> dict:
    cfg: dict = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": _COLLECTION,
                "host": _QDRANT_HOST,
                "port": _QDRANT_PORT,
                "embedding_model_dims": _EMBED_DIMS,
            },
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": _LLM_MODEL,
                "ollama_base_url": _OLLAMA_URL,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": _EMBED_MODEL,
                "ollama_base_url": _OLLAMA_URL,
            },
        },
    }
    # Add Neo4j graph store only if credentials are set
    if _NEO4J_URI and _NEO4J_PASSWORD != "password":
        cfg["graph_store"] = {
            "provider": "neo4j",
            "config": {
                "url":      _NEO4J_URI,
                "username": _NEO4J_USER,
                "password": _NEO4J_PASSWORD,
            },
        }
    return cfg


def _get_mem0():
    """Lazy-initialise the mem0 Memory instance."""
    global _mem0_instance
    if _mem0_instance is not None:
        return _mem0_instance

    try:
        from mem0 import Memory
    except ImportError:
        raise ImportError(
            "[MEM0] mem0ai not installed — run: pip install mem0ai qdrant-client"
        )

    try:
        _mem0_instance = Memory.from_config(_build_config())
        logger.info(
            "[MEM0] Initialised — qdrant=%s:%d collection=%s embed=%s",
            _QDRANT_HOST, _QDRANT_PORT, _COLLECTION, _EMBED_MODEL,
        )
    except Exception as e:
        logger.error("[MEM0] Initialisation failed: %s", e)
        raise

    return _mem0_instance


def _extract_results(raw: Any) -> list[dict]:
    """Normalise mem0 result formats across versions."""
    if isinstance(raw, dict):
        return raw.get("results", [])
    if isinstance(raw, list):
        return raw
    return []


async def remember(
    content: str,
    user_id: str = "system",
    agent_id: str | None = None,
    metadata: dict | None = None,
) -> list[dict]:
    """
    Store a text fact/observation. mem0 extracts key facts and deduplicates.

    Returns list of memory entries created/updated.
    """
    def _sync():
        m = _get_mem0()
        kw: dict = {"user_id": user_id}
        if agent_id:
            kw["agent_id"] = agent_id
        if metadata:
            kw["metadata"] = metadata
        return m.add(content, **kw)

    try:
        raw = await asyncio.to_thread(_sync)
        entries = _extract_results(raw)
        logger.info("[MEM0] stored %d fact(s) for user=%s agent=%s", len(entries), user_id, agent_id)
        return entries
    except Exception as e:
        logger.error("[MEM0] remember() failed: %s", e)
        return []


async def remember_conversation(
    messages: list[dict],
    user_id: str = "system",
    agent_id: str | None = None,
) -> list[dict]:
    """
    Store a full conversation [{role, content}, ...]. mem0 extracts facts automatically.
    Use this at the end of every agent task to build institutional memory.
    """
    def _sync():
        m = _get_mem0()
        kw: dict = {"user_id": user_id}
        if agent_id:
            kw["agent_id"] = agent_id
        return m.add(messages, **kw)

    try:
        raw = await asyncio.to_thread(_sync)
        entries = _extract_results(raw)
        logger.info(
            "[MEM0] stored conversation (%d turns) → %d facts for user=%s",
            len(messages), len(entries), user_id,
        )
        return entries
    except Exception as e:
        logger.error("[MEM0] remember_conversation() failed: %s", e)
        return []


async def recall(
    query: str,
    user_id: str = "system",
    agent_id: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search agent memory by semantic similarity.

    Returns: [{"memory": "...", "score": 0.94, "id": "...", "metadata": {...}}, ...]
    Higher score = more relevant.
    """
    def _sync():
        m = _get_mem0()
        kw: dict = {"user_id": user_id, "limit": limit}
        if agent_id:
            kw["agent_id"] = agent_id
        return m.search(query, **kw)

    try:
        raw = await asyncio.to_thread(_sync)
        memories = _extract_results(raw)
        logger.debug("[MEM0] recall('%s') → %d results", query[:50], len(memories))
        return memories
    except Exception as e:
        logger.error("[MEM0] recall() failed: %s", e)
        return []


async def recall_as_context(
    query: str,
    user_id: str = "system",
    agent_id: str | None = None,
    limit: int = 8,
    prefix: str = "Relevant memory from past sessions:\n",
) -> str:
    """
    Recall memories and format as plain text for LLM system prompt injection.
    Returns empty string if no memories found (safe to concatenate unconditionally).

    Example:
        context = await recall_as_context("Carvansons pricing", user_id="daniel")
        system = f\"{base_sop}\\n\\n{context}\" if context else base_sop
    """
    memories = await recall(query, user_id=user_id, agent_id=agent_id, limit=limit)
    if not memories:
        return ""

    lines = [prefix]
    for i, mem in enumerate(memories, 1):
        text  = mem.get("memory", mem.get("text", ""))
        score = mem.get("score", 0.0)
        lines.append(f"  {i}. {text}  [{score:.2f}]")

    return "\n".join(lines)


async def get_all(user_id: str = "system", agent_id: str | None = None) -> list[dict]:
    """Return all stored memories for a user (for inspection/debug)."""
    def _sync():
        m = _get_mem0()
        kw: dict = {"user_id": user_id}
        if agent_id:
            kw["agent_id"] = agent_id
        return m.get_all(**kw)

    try:
        raw = await asyncio.to_thread(_sync)
        return _extract_results(raw)
    except Exception as e:
        logger.error("[MEM0] get_all() failed: %s", e)
        return []


async def wipe(user_id: str, agent_id: str | None = None) -> bool:
    """Delete all memories for a user. Irreversible."""
    def _sync():
        m = _get_mem0()
        if agent_id:
            return m.delete_all(user_id=user_id, agent_id=agent_id)
        return m.delete_all(user_id=user_id)

    try:
        await asyncio.to_thread(_sync)
        logger.info("[MEM0] wiped memories — user=%s agent=%s", user_id, agent_id)
        return True
    except Exception as e:
        logger.error("[MEM0] wipe() failed: %s", e)
        return False
