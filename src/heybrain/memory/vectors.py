"""Chroma vector store wrapper (plan.md §7, §8.4).

Persists memory embeddings at $HEYBRAIN_HOME/chroma/ in a single collection
named "memories". Chroma's built-in embedding function is disabled: every
embedding is supplied by the caller, this module never calls Bedrock itself.

SQLite (heybrain.storage) is the source of truth for memories; Chroma is a
disposable index that `brain reindex` regenerates from it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from heybrain.core.config import get_settings
from heybrain.core.models import Memory

_COLLECTION_NAME = "memories"
_DEFAULT_STATUS_FILTER = "active"


def memory_metadata(memory: Memory) -> dict[str, Any]:
    """Build the Chroma metadata dict for a Memory (plan.md §7)."""
    return {
        "memory_type": memory.memory_type.value,
        "topic": memory.topic,
        "importance": memory.importance,
        "status": memory.status.value,
        "created_at": memory.created_at.isoformat(),
        "conversation_id": memory.conversation_id,
    }


class VectorStore:
    """Thin wrapper around a Chroma `PersistentClient` with explicit vectors."""

    def __init__(self, chroma_dir: Path | None = None) -> None:
        self._path = chroma_dir if chroma_dir is not None else get_settings().chroma_dir
        self._path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._path))
        self._collection = self._get_or_create_collection()

    def _get_or_create_collection(self):
        # embedding_function=None (both here and in `configuration`) disables
        # Chroma's default local embedding model; add()/query() then require
        # explicit vectors from the caller and raise if none are given.
        return self._client.get_or_create_collection(
            _COLLECTION_NAME,
            embedding_function=None,
            configuration={"embedding_function": None},
        )

    def upsert(
        self, memory_id: str, embedding: list[float], metadata: dict[str, Any]
    ) -> None:
        self._collection.upsert(
            ids=[memory_id], embeddings=[embedding], metadatas=[metadata]
        )

    def search(
        self,
        embedding: list[float],
        k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        where: dict[str, Any] = {"status": _DEFAULT_STATUS_FILTER}
        if filters:
            where.update(filters)

        conditions = [{key: value} for key, value in where.items()]
        where_clause = conditions[0] if len(conditions) == 1 else {"$and": conditions}

        result = self._collection.query(
            query_embeddings=[embedding], n_results=k, where=where_clause
        )
        ids = result["ids"][0]
        distances = result["distances"][0]
        return list(zip(ids, distances))

    def delete(self, memory_id: str) -> None:
        self._collection.delete(ids=[memory_id])

    def close(self) -> None:
        """Release the underlying Chroma client's file handles.

        Needed before deleting/replacing `chroma_dir` out from under a live
        client (e.g. in tests) -- otherwise the sqlite backing store is left
        open and later writers see it as read-only.
        """
        self._client.close()

    def rebuild(self, memories: list[Memory], embeddings: list[list[float]]) -> None:
        """Drop and repopulate the collection from SQLite state.

        Chroma is disposable (plan.md §7) — this is what `brain reindex` calls.
        """
        if len(memories) != len(embeddings):
            raise ValueError("memories and embeddings must be the same length")

        self._client.delete_collection(_COLLECTION_NAME)
        self._collection = self._get_or_create_collection()

        if not memories:
            return

        self._collection.upsert(
            ids=[memory.id for memory in memories],
            embeddings=embeddings,
            metadatas=[memory_metadata(memory) for memory in memories],
        )
