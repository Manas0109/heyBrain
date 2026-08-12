"""Structured-output schemas for Bedrock extraction paths.

These models are passed directly as structured-output schemas, so they
must stay compatible with the API's JSON schema subset: no recursion,
no numeric constraints beyond simple bounds, and every object is closed
(no extra properties).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(StrEnum):
    CAPTURE = "capture"
    QUESTION = "question"
    RECALL = "recall"
    RESUME = "resume"
    REMINDER = "reminder"


MemoryTypeLiteral = Literal["idea", "goal", "preference", "fact", "decision", "plan"]


class MemoryCandidate(_StrictModel):
    content: str  # rewritten, self-contained fact
    memory_type: MemoryTypeLiteral
    importance: float = Field(ge=0.0, le=1.0)
    topic: str  # short label


class TaskCandidate(_StrictModel):
    title: str
    description: str


class ReminderCandidate(_StrictModel):
    title: str
    scheduled_at: str  # ISO 8601, timezone-aware
    recurrence: str | None = None


class ConversationAnalysis(_StrictModel):
    title: str
    summary: str
    topic: str
    memory_candidates: list[MemoryCandidate]
    tasks: list[TaskCandidate]


class RecallSynthesis(_StrictModel):
    answer: str
    source_memory_ids: list[str]


class TopicReconstruction(_StrictModel):
    topic: str
    summary: str
    open_threads: list[str]
