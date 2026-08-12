"""AppService — the only layer the CLI talks to.

Orchestrates transcription, Bedrock, memory, reminders, and storage.
Method bodies are stubs for now; each phase in plan.md fills one in.
"""

from __future__ import annotations

from heybrain.core.models import Conversation, Memory, Reminder


class AppService:
    def think(self, text: str | None = None, *, voice: bool = False) -> Conversation:
        raise NotImplementedError

    def remember(self, text: str) -> Memory:
        raise NotImplementedError

    def recall(self, query: str) -> str:
        raise NotImplementedError

    def resume(self, topic: str | None = None) -> Conversation:
        raise NotImplementedError

    def list_conversations(self) -> list[Conversation]:
        raise NotImplementedError

    def show_conversation(self, conversation_id: str) -> Conversation:
        raise NotImplementedError

    def list_reminders(self) -> list[Reminder]:
        raise NotImplementedError

    def tick_reminders(self) -> None:
        raise NotImplementedError

    def doctor(self) -> dict[str, bool]:
        raise NotImplementedError
