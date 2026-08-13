"""Repository layer — plain sqlite3, no ORM.

Each repo owns CRUD plus the read patterns the app needs, and maps rows
straight to the Pydantic models from core.models (plan.md §7). SQLite is
the source of truth; this is the only place raw SQL lives.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from heybrain.core.models import (
    Conversation,
    Memory,
    Message,
    Reminder,
    Task,
    UsageRecord,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class ConversationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, conversation: Conversation) -> Conversation:
        self._conn.execute(
            """
            INSERT INTO conversations
                (id, title, summary, topic, status, created_at, updated_at)
            VALUES
                (:id, :title, :summary, :topic, :status, :created_at, :updated_at)
            """,
            {
                "id": conversation.id,
                "title": conversation.title,
                "summary": conversation.summary,
                "topic": conversation.topic,
                "status": conversation.status.value,
                "created_at": conversation.created_at.isoformat(),
                "updated_at": conversation.updated_at.isoformat(),
            },
        )
        self._conn.commit()
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        row = self._conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def update(self, conversation: Conversation) -> Conversation:
        self._conn.execute(
            """
            UPDATE conversations
            SET title = :title, summary = :summary, topic = :topic,
                status = :status, updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": conversation.id,
                "title": conversation.title,
                "summary": conversation.summary,
                "topic": conversation.topic,
                "status": conversation.status.value,
                "updated_at": conversation.updated_at.isoformat(),
            },
        )
        self._conn.commit()
        return conversation

    def list_recent(self, limit: int = 10) -> list[Conversation]:
        rows = self._conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def list_by_topic(self, topic: str) -> list[Conversation]:
        rows = self._conn.execute(
            "SELECT * FROM conversations WHERE topic = ? ORDER BY created_at ASC",
            (topic,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def distinct_topics(self) -> list[tuple[str, datetime]]:
        """Distinct non-null topic labels with their most recent touch (`resume`, issue #12)."""
        rows = self._conn.execute(
            """
            SELECT topic, MAX(updated_at) AS last_touched_at
            FROM conversations
            WHERE topic IS NOT NULL
            GROUP BY topic
            """
        ).fetchall()
        return [(row["topic"], _dt(row["last_touched_at"])) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            summary=row["summary"],
            topic=row["topic"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )


class MessageRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, message: Message) -> Message:
        self._conn.execute(
            """
            INSERT INTO messages (id, conversation_id, role, content, created_at)
            VALUES (:id, :conversation_id, :role, :content, :created_at)
            """,
            {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "role": message.role.value,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            },
        )
        self._conn.commit()
        return message

    def get(self, message_id: str) -> Message | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def list_by_conversation(self, conversation_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            created_at=_dt(row["created_at"]),
        )


class MemoryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, memory: Memory) -> Memory:
        self._conn.execute(
            """
            INSERT INTO memories
                (id, conversation_id, memory_type, content, topic, importance,
                 status, created_at, updated_at)
            VALUES
                (:id, :conversation_id, :memory_type, :content, :topic, :importance,
                 :status, :created_at, :updated_at)
            """,
            {
                "id": memory.id,
                "conversation_id": memory.conversation_id,
                "memory_type": memory.memory_type.value,
                "content": memory.content,
                "topic": memory.topic,
                "importance": memory.importance,
                "status": memory.status.value,
                "created_at": memory.created_at.isoformat(),
                "updated_at": memory.updated_at.isoformat(),
            },
        )
        self._conn.commit()
        return memory

    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def update(self, memory: Memory) -> Memory:
        self._conn.execute(
            """
            UPDATE memories
            SET content = :content, topic = :topic, importance = :importance,
                status = :status, updated_at = :updated_at
            WHERE id = :id
            """,
            {
                "id": memory.id,
                "content": memory.content,
                "topic": memory.topic,
                "importance": memory.importance,
                "status": memory.status.value,
                "updated_at": memory.updated_at.isoformat(),
            },
        )
        self._conn.commit()
        return memory

    def get_many(self, ids: list[str]) -> list[Memory]:
        """Fetch memories by id, preserving the order of `ids`.

        Retrieval ranks before fetching, so callers rely on this ordering
        rather than SQLite's (unspecified) IN-clause result order. Ids with
        no matching row are silently skipped.
        """
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", ids
        ).fetchall()
        by_id = {row["id"]: self._to_model(row) for row in rows}
        return [by_id[i] for i in ids if i in by_id]

    def list_by_topic(self, topic: str) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE topic = ? ORDER BY created_at DESC",
            (topic,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def list_all(self) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY created_at ASC"
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def distinct_topics(self) -> list[tuple[str, datetime]]:
        """Distinct topic labels with their most recent touch (`resume`, issue #12)."""
        rows = self._conn.execute(
            """
            SELECT topic, MAX(created_at) AS last_touched_at
            FROM memories
            GROUP BY topic
            """
        ).fetchall()
        return [(row["topic"], _dt(row["last_touched_at"])) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            conversation_id=row["conversation_id"],
            memory_type=row["memory_type"],
            content=row["content"],
            topic=row["topic"],
            importance=row["importance"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )


class TaskRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, task: Task) -> Task:
        self._conn.execute(
            """
            INSERT INTO tasks
                (id, conversation_id, title, description, status, created_at, completed_at)
            VALUES
                (:id, :conversation_id, :title, :description, :status, :created_at, :completed_at)
            """,
            {
                "id": task.id,
                "conversation_id": task.conversation_id,
                "title": task.title,
                "description": task.description,
                "status": task.status.value,
                "created_at": task.created_at.isoformat(),
                "completed_at": task.completed_at.isoformat()
                if task.completed_at
                else None,
            },
        )
        self._conn.commit()
        return task

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def update(self, task: Task) -> Task:
        self._conn.execute(
            """
            UPDATE tasks
            SET title = :title, description = :description, status = :status,
                completed_at = :completed_at
            WHERE id = :id
            """,
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "status": task.status.value,
                "completed_at": task.completed_at.isoformat()
                if task.completed_at
                else None,
            },
        )
        self._conn.commit()
        return task

    def list_by_conversation(self, conversation_id: str) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    def list_open_by_topic(self, topic: str) -> list[Task]:
        """Open tasks for `topic`, via its conversations (tasks carry no topic of their own)."""
        rows = self._conn.execute(
            """
            SELECT tasks.* FROM tasks
            JOIN conversations ON tasks.conversation_id = conversations.id
            WHERE conversations.topic = ? AND tasks.status = 'open'
            ORDER BY tasks.created_at ASC
            """,
            (topic,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            conversation_id=row["conversation_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            created_at=_dt(row["created_at"]),
            completed_at=_dt(row["completed_at"]),
        )


class ReminderRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, reminder: Reminder) -> Reminder:
        self._conn.execute(
            """
            INSERT INTO reminders
                (id, task_id, scheduled_at, status, fired_at, created_at)
            VALUES
                (:id, :task_id, :scheduled_at, :status, :fired_at, :created_at)
            """,
            {
                "id": reminder.id,
                "task_id": reminder.task_id,
                "scheduled_at": reminder.scheduled_at.isoformat(),
                "status": reminder.status.value,
                "fired_at": reminder.fired_at.isoformat()
                if reminder.fired_at
                else None,
                "created_at": reminder.created_at.isoformat(),
            },
        )
        self._conn.commit()
        return reminder

    def get(self, reminder_id: str) -> Reminder | None:
        row = self._conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def update(self, reminder: Reminder) -> Reminder:
        self._conn.execute(
            """
            UPDATE reminders
            SET scheduled_at = :scheduled_at, status = :status, fired_at = :fired_at
            WHERE id = :id
            """,
            {
                "id": reminder.id,
                "scheduled_at": reminder.scheduled_at.isoformat(),
                "status": reminder.status.value,
                "fired_at": reminder.fired_at.isoformat()
                if reminder.fired_at
                else None,
            },
        )
        self._conn.commit()
        return reminder

    def list_pending_due_before(self, before: datetime) -> list[Reminder]:
        rows = self._conn.execute(
            """
            SELECT * FROM reminders
            WHERE status = 'pending' AND scheduled_at < ?
            ORDER BY scheduled_at ASC
            """,
            (before.isoformat(),),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            task_id=row["task_id"],
            scheduled_at=_dt(row["scheduled_at"]),
            status=row["status"],
            fired_at=_dt(row["fired_at"]),
            created_at=_dt(row["created_at"]),
        )


class UsageRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, usage: UsageRecord) -> UsageRecord:
        self._conn.execute(
            """
            INSERT INTO usage
                (id, request_id, operation, model_id, input_tokens, output_tokens,
                 latency_ms, created_at)
            VALUES
                (:id, :request_id, :operation, :model_id, :input_tokens, :output_tokens,
                 :latency_ms, :created_at)
            """,
            {
                "id": usage.id,
                "request_id": usage.request_id,
                "operation": usage.operation,
                "model_id": usage.model_id,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "latency_ms": usage.latency_ms,
                "created_at": usage.created_at.isoformat(),
            },
        )
        self._conn.commit()
        return usage

    def get(self, usage_id: str) -> UsageRecord | None:
        row = self._conn.execute(
            "SELECT * FROM usage WHERE id = ?", (usage_id,)
        ).fetchone()
        return self._to_model(row) if row else None

    def list_by_request(self, request_id: str) -> list[UsageRecord]:
        rows = self._conn.execute(
            "SELECT * FROM usage WHERE request_id = ? ORDER BY created_at ASC",
            (request_id,),
        ).fetchall()
        return [self._to_model(row) for row in rows]

    @staticmethod
    def _to_model(row: sqlite3.Row) -> UsageRecord:
        return UsageRecord(
            id=row["id"],
            request_id=row["request_id"],
            operation=row["operation"],
            model_id=row["model_id"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            latency_ms=row["latency_ms"],
            created_at=_dt(row["created_at"]),
        )
