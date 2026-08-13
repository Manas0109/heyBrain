"""Live eval: retriever.retrieve() against the issue #4 recall query set.

Needs real Bedrock embeddings to mean anything -- several queries (see
recall_queries.json's `notes`) are deliberately worded not to overlap the
stored memory's text, so a keyword-matching fake embedding would pass or
fail them for the wrong reason. Skipped by default; opt in with
HEYBRAIN_RUN_LIVE_EVAL=1 and real AWS credentials.

Acceptance target (issue #10): >= 8/10 queries return the expected memory
in the top 3.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from heybrain.core.config import Settings
from heybrain.core.models import Conversation, Memory, MemoryType
from heybrain.memory.retriever import MemoryRetriever
from heybrain.memory.vectors import VectorStore, memory_metadata
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo, MemoryRepo, UsageRepo
from tests.eval.loader import load_capture_examples, load_recall_queries

pytestmark = [
    pytest.mark.live_eval,
    pytest.mark.skipif(
        os.environ.get("HEYBRAIN_RUN_LIVE_EVAL") != "1",
        reason="needs real Bedrock credentials and network access; "
        "set HEYBRAIN_RUN_LIVE_EVAL=1 to run",
    ),
]


def test_recall_queries_hit_top_3(tmp_path: Path) -> None:
    from heybrain.bedrock.client import BedrockService

    settings = Settings(heybrain_home=tmp_path)
    conn = get_connection(tmp_path / "brain.db")
    bedrock = BedrockService(UsageRepo(conn), settings)
    vector_store = VectorStore(tmp_path / "chroma")
    memory_repo = MemoryRepo(conn)
    conversation = ConversationRepo(conn).create(Conversation())

    examples = load_capture_examples()
    stored_contents = sorted(
        {e["expected_memory"] for e in examples if e["expected_memory"] is not None}
    )
    embeddings = bedrock.embed(stored_contents)
    for content, embedding in zip(stored_contents, embeddings):
        memory = Memory(
            conversation_id=conversation.id,
            memory_type=MemoryType.FACT,
            content=content,
            topic="eval",
            importance=0.8,
        )
        vector_store.upsert(memory.id, embedding, memory_metadata(memory))
        memory_repo.create(memory)

    retriever = MemoryRetriever(
        bedrock=bedrock, vector_store=vector_store, memory_repo=memory_repo
    )

    queries = load_recall_queries()
    hits = 0
    misses = []
    for query in queries:
        expected = query["expected_memory_content"]
        top3 = [m.content for m in retriever.retrieve(query["query"], k=3)]
        if expected is None:
            # No memory should confidently match; not asserted strictly here
            # (retrieve() has no confidence cutoff yet), but doesn't count
            # against the recall score either way.
            continue
        if expected in top3:
            hits += 1
        else:
            misses.append((query["id"], expected, top3))

    scored = [q for q in queries if q["expected_memory_content"] is not None]
    print(f"\nrecall eval: {hits}/{len(scored)} in top 3. Misses: {misses}")
    assert hits >= 8, f"only {hits}/{len(scored)} queries hit top 3; misses: {misses}"
