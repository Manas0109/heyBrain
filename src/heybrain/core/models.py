"""Domain models.

Pure Pydantic models — no I/O, no SQL, no network calls. These are the
shapes storage repositories read/write and services pass around.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class MemoryType(StrEnum):
    IDEA = "idea"
    GOAL = "goal"
    PREFERENCE = "preference"
    FACT = "fact"
    DECISION = "decision"
    PLAN = "plan"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"


class TaskStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    FIRED = "fired"
    MISSED = "missed"


class Conversation(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str | None = None
    summary: str | None = None
    topic: str | None = None
    status: ConversationStatus = ConversationStatus.OPEN
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Message(BaseModel):
    id: str = Field(default_factory=_new_id)
    conversation_id: str
    role: Role
    content: str
    created_at: datetime = Field(default_factory=_now)


class Memory(BaseModel):
    id: str = Field(default_factory=_new_id)
    conversation_id: str
    memory_type: MemoryType
    content: str
    topic: str
    importance: float = Field(ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Task(BaseModel):
    id: str = Field(default_factory=_new_id)
    conversation_id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class Reminder(BaseModel):
    id: str = Field(default_factory=_new_id)
    task_id: str
    scheduled_at: datetime
    status: ReminderStatus = ReminderStatus.PENDING
    fired_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class UsageRecord(BaseModel):
    id: str = Field(default_factory=_new_id)
    request_id: str
    operation: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    created_at: datetime = Field(default_factory=_now)
