"""
Vector memory via ChromaDB (http://localhost:8001).

Persistent semantic storage. Projects store findings, summaries, vendor profiles,
or any text chunks — then retrieve the most relevant ones by meaning later.

Import from any project:
    from systemOS.mcp.memory import upsert, search, delete, collection_info

Each project uses its own named collection to keep data isolated.

Usage:
    # Store a research finding
    await upsert(
        collection="researchos_findings",
        doc_id="finding_42",
        text="Carvansons offers 10L fragrance oils with 4-week lead time...",
        metadata={"topic_id": 42, "project": "nnl-supply-chain", "source": "carvansons.co.uk"},
    )

    # Semantic search — find the 5 most relevant past findings
    results = await search(
        collection="researchos_findings",
        query="fragrance supplier lead times UK",
        n=5,
        where={"project": "nnl-supply-chain"},  # optional metadata filter
    )
    for r in results:
        print(r["text"], r["score"], r["metadata"])

    # Delete a document
    await delete(collection="researchos_findings", doc_id="finding_42")

    # Check collection stats
    info = await collection_info("researchos_findings")
    print(info["count"])  # number of stored documents

Embeddings:
    ChromaDB uses its built-in embedding function by default (sentence-transformers).
    To use Ollama nomic-embed-text instead, set MEMORY_USE_OLLAMA_EMBED=1 in .env
    and ensure nomic-embed-text is pulled on the Ollama host.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8001")
USE_OLLAMA_EMBED = os.getenv("MEMORY_USE_OLLAMA_EMBED", "0") == "1"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://100.76.139.41:11434")

_client = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.HttpClient(
            host=CHROMA_URL.replace("http://", "").split(":")[0],
            port=int(CHROMA_URL.split(":")[-1]),
        )
    return _client


def _get_embed_fn():
    if not USE_OLLAMA_EMBED:
        return None  # ChromaDB uses its default (sentence-transformers)

    # Use Ollama nomic-embed-text for consistency with the rest of the system
    import httpx
    from chromadb.api.types import Documents, Embeddings

    class OllamaEmbedFn:
        def __call__(self, input: Documents) -> Embeddings:
            results = []
            for text in input:
                resp = httpx.post(
                    f"{OLLAMA_URL}/api/embeddings",
                    json={"model": "nomic-embed-text", "prompt": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                results.append(resp.json()["embedding"])
            return results

    return OllamaEmbedFn()


def _get_collection(name: str):
    client = _get_client()
    embed_fn = _get_embed_fn()
    kwargs: dict[str, Any] = {"name": name, "get_or_create": True}
    if embed_fn:
        kwargs["embedding_function"] = embed_fn
    return client.get_or_create_collection(**{k: v for k, v in kwargs.items() if k != "get_or_create"},
                                            )


def _collection(name: str):
    client = _get_client()
    embed_fn = _get_embed_fn()
    if embed_fn:
        return client.get_or_create_collection(name=name, embedding_function=embed_fn)
    return client.get_or_create_collection(name=name)


async def upsert(
    collection: str,
    doc_id: str,
    text: str,
    metadata: dict | None = None,
) -> None:
    """
    Store or update a document in the named collection.
    doc_id should be unique per document — upsert overwrites on collision.
    """
    if not text or not text.strip():
        logger.warning("[MEMORY] upsert skipped — empty text for id=%s", doc_id)
        return
    try:
        col = _collection(collection)
        col.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )
        logger.debug("[MEMORY] upserted id=%s into collection=%s", doc_id, collection)
    except Exception as e:
        logger.error("[MEMORY] upsert failed for id=%s: %s", doc_id, e)
        raise


async def search(
    collection: str,
    query: str,
    n: int = 5,
    where: dict | None = None,
) -> list[dict]:
    """
    Semantic search. Returns up to n results sorted by relevance.

    Each result dict: {"id": str, "text": str, "score": float, "metadata": dict}
    Lower score = more similar (ChromaDB uses L2 distance by default).
    """
    if not query.strip():
        return []
    try:
        col = _collection(collection)
        kwargs: dict[str, Any] = {"query_texts": [query], "n_results": n}
        if where:
            kwargs["where"] = where
        results = col.query(**kwargs)

        docs = results.get("documents", [[]])[0]
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        return [
            {"id": ids[i], "text": docs[i], "score": distances[i], "metadata": metadatas[i]}
            for i in range(len(docs))
        ]
    except Exception as e:
        logger.error("[MEMORY] search failed for collection=%s: %s", collection, e)
        return []


async def delete(collection: str, doc_id: str) -> None:
    """Remove a document by ID."""
    try:
        col = _collection(collection)
        col.delete(ids=[doc_id])
        logger.debug("[MEMORY] deleted id=%s from collection=%s", doc_id, collection)
    except Exception as e:
        logger.error("[MEMORY] delete failed for id=%s: %s", doc_id, e)


async def collection_info(collection: str) -> dict:
    """Return basic stats about a collection: {"name", "count"}."""
    try:
        col = _collection(collection)
        return {"name": collection, "count": col.count()}
    except Exception as e:
        logger.error("[MEMORY] collection_info failed: %s", e)
        return {"name": collection, "count": 0, "error": str(e)}


async def list_collections() -> list[str]:
    """Return names of all collections in ChromaDB."""
    try:
        client = _get_client()
        return [c.name for c in client.list_collections()]
    except Exception as e:
        logger.error("[MEMORY] list_collections failed: %s", e)
        return []
