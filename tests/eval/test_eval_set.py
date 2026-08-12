from datetime import datetime

from tests.eval.loader import (
    load_capture_examples,
    load_recall_queries,
    load_reminder_phrasings,
)


def test_capture_examples_count_and_shape() -> None:
    examples = load_capture_examples()
    assert len(examples) == 10
    for example in examples:
        assert example["id"]
        assert example["input"]
        assert example["expected_intent"] == "capture"


def test_recall_queries_count_and_shape() -> None:
    queries = load_recall_queries()
    assert len(queries) == 10
    for query in queries:
        assert query["id"]
        assert query["query"]
        assert "expected_memory_content" in query


def test_recall_queries_cover_all_stored_memories() -> None:
    examples = load_capture_examples()
    queries = load_recall_queries()
    stored_memories = {
        e["expected_memory"] for e in examples if e["expected_memory"] is not None
    }
    queried_memories = {
        q["expected_memory_content"]
        for q in queries
        if q["expected_memory_content"] is not None
    }
    assert stored_memories <= queried_memories


def test_reminder_phrasings_count_and_resolve() -> None:
    phrasings = load_reminder_phrasings()
    assert len(phrasings) == 5
    for phrasing in phrasings:
        assert phrasing["id"]
        assert phrasing["phrasing"]
        reference = datetime.fromisoformat(phrasing["reference_datetime"])
        resolved = datetime.fromisoformat(phrasing["expected_resolved_datetime"])
        assert resolved.tzinfo is not None
        assert reference.tzinfo is not None
        assert resolved >= reference
