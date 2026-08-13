from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlite3

from heybrain.core.models import (
    Conversation,
    Memory,
    MemoryType,
    Message,
    Reminder,
    Role,
    Task,
    TaskStatus,
    UsageRecord,
)
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import (
    ConversationRepo,
    MemoryRepo,
    MessageRepo,
    ReminderRepo,
    TaskRepo,
    UsageRepo,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "brain.db"


@pytest.fixture
def conn(db_path: Path) -> sqlite3.Connection:
    connection = get_connection(db_path)
    yield connection
    connection.close()


def _make_conversation(**overrides) -> Conversation:
    defaults = {"title": "t", "summary": "s", "topic": "topic"}
    defaults.update(overrides)
    return Conversation(**defaults)


def test_conversation_repo_roundtrip(conn: sqlite3.Connection) -> None:
    repo = ConversationRepo(conn)
    original = _make_conversation()

    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_conversation_repo_list_recent(conn: sqlite3.Connection) -> None:
    repo = ConversationRepo(conn)
    older = _make_conversation()
    repo.create(older)
    newer = _make_conversation()
    newer.updated_at = older.updated_at + timedelta(seconds=1)
    repo.create(newer)

    recent = repo.list_recent(limit=10)

    assert [c.id for c in recent] == [newer.id, older.id]


def test_message_repo_roundtrip(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MessageRepo(conn)
    original = Message(conversation_id=conversation.id, role=Role.USER, content="hi")
    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_message_repo_list_by_conversation(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MessageRepo(conn)
    first = Message(conversation_id=conversation.id, role=Role.USER, content="first")
    repo.create(first)
    second = Message(conversation_id=conversation.id, role=Role.ASSISTANT, content="second")
    repo.create(second)

    messages = repo.list_by_conversation(conversation.id)

    assert [m.id for m in messages] == [first.id, second.id]


def test_memory_repo_roundtrip(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MemoryRepo(conn)
    original = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="user likes tea",
        topic="preferences",
        importance=0.8,
    )
    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_memory_repo_get_many_preserves_order(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MemoryRepo(conn)
    memories = [
        Memory(
            conversation_id=conversation.id,
            memory_type=MemoryType.FACT,
            content=f"fact {i}",
            topic="t",
            importance=0.7,
        )
        for i in range(3)
    ]
    for memory in memories:
        repo.create(memory)

    shuffled_ids = [memories[2].id, memories[0].id, memories[1].id]
    fetched = repo.get_many(shuffled_ids)

    assert [m.id for m in fetched] == shuffled_ids


def test_memory_repo_get_many_skips_missing_ids(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MemoryRepo(conn)
    memory = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="fact",
        topic="t",
        importance=0.7,
    )
    repo.create(memory)

    fetched = repo.get_many([memory.id, "does-not-exist"])

    assert [m.id for m in fetched] == [memory.id]


def test_memory_repo_list_by_topic(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MemoryRepo(conn)
    matching = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="matching",
        topic="kafka",
        importance=0.7,
    )
    repo.create(matching)
    other = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="other",
        topic="other-topic",
        importance=0.7,
    )
    repo.create(other)

    results = repo.list_by_topic("kafka")

    assert [m.id for m in results] == [matching.id]


def test_memory_repo_distinct_topics(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = MemoryRepo(conn)
    older = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="older kafka fact",
        topic="kafka",
        importance=0.7,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newer = Memory(
        conversation_id=conversation.id,
        memory_type=MemoryType.FACT,
        content="newer kafka fact",
        topic="kafka",
        importance=0.7,
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    repo.create(older)
    repo.create(newer)

    topics = dict(repo.distinct_topics())

    assert topics["kafka"] == datetime(2024, 6, 1, tzinfo=timezone.utc)


def test_conversation_repo_list_by_topic(conn: sqlite3.Connection) -> None:
    repo = ConversationRepo(conn)
    matching = _make_conversation(topic="kafka")
    repo.create(matching)
    other = _make_conversation(topic="other-topic")
    repo.create(other)

    results = repo.list_by_topic("kafka")

    assert [c.id for c in results] == [matching.id]


def test_conversation_repo_distinct_topics_ignores_null_topic(conn: sqlite3.Connection) -> None:
    repo = ConversationRepo(conn)
    repo.create(_make_conversation(topic="kafka"))
    repo.create(_make_conversation(topic=None))

    topics = dict(repo.distinct_topics())

    assert "kafka" in topics
    assert None not in topics
    assert len(topics) == 1


def test_task_repo_roundtrip(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)

    repo = TaskRepo(conn)
    original = Task(conversation_id=conversation.id, title="do the thing")
    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_task_repo_list_open_by_topic(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    kafka_conversation = conversation_repo.create(_make_conversation(topic="kafka"))
    other_conversation = conversation_repo.create(_make_conversation(topic="other-topic"))

    repo = TaskRepo(conn)
    open_task = Task(conversation_id=kafka_conversation.id, title="read the docs")
    repo.create(open_task)
    completed_task = Task(
        conversation_id=kafka_conversation.id,
        title="already done",
        status=TaskStatus.COMPLETED,
    )
    repo.create(completed_task)
    other_topic_task = Task(conversation_id=other_conversation.id, title="unrelated")
    repo.create(other_topic_task)

    results = repo.list_open_by_topic("kafka")

    assert [t.id for t in results] == [open_task.id]


def test_reminder_repo_roundtrip(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)
    task_repo = TaskRepo(conn)
    task = Task(conversation_id=conversation.id, title="do the thing")
    task_repo.create(task)

    repo = ReminderRepo(conn)
    original = Reminder(
        task_id=task.id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_reminder_repo_list_pending_due_before(conn: sqlite3.Connection) -> None:
    conversation_repo = ConversationRepo(conn)
    conversation = _make_conversation()
    conversation_repo.create(conversation)
    task_repo = TaskRepo(conn)
    task = Task(conversation_id=conversation.id, title="do the thing")
    task_repo.create(task)

    repo = ReminderRepo(conn)
    now = datetime.now(timezone.utc)
    due_soon = Reminder(task_id=task.id, scheduled_at=now + timedelta(minutes=5))
    repo.create(due_soon)
    due_later = Reminder(task_id=task.id, scheduled_at=now + timedelta(days=5))
    repo.create(due_later)

    cutoff = now + timedelta(hours=1)
    pending = repo.list_pending_due_before(cutoff)

    assert [r.id for r in pending] == [due_soon.id]


def test_usage_repo_roundtrip(conn: sqlite3.Connection) -> None:
    repo = UsageRepo(conn)
    original = UsageRecord(
        request_id="req-1",
        operation="chat",
        model_id="anthropic.claude-opus-5",
        input_tokens=10,
        output_tokens=20,
        latency_ms=123,
    )
    repo.create(original)
    fetched = repo.get(original.id)

    assert fetched == original


def test_schema_recreated_after_deleting_db(db_path: Path) -> None:
    conn = get_connection(db_path)
    repo = ConversationRepo(conn)
    repo.create(_make_conversation())
    conn.close()

    assert db_path.exists()
    db_path.unlink()
    # WAL sidecar files, if present, don't block recreation.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()

    new_conn = get_connection(db_path)
    tables = {
        row["name"]
        for row in new_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "conversations",
        "messages",
        "memories",
        "tasks",
        "reminders",
        "usage",
    } <= tables
    assert ConversationRepo(new_conn).list_recent() == []
    new_conn.close()


def test_foreign_key_violation_raises(conn: sqlite3.Connection) -> None:
    repo = MessageRepo(conn)
    orphan = Message(conversation_id="does-not-exist", role=Role.USER, content="hi")

    with pytest.raises(sqlite3.IntegrityError):
        repo.create(orphan)


def test_pragmas_are_set(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
