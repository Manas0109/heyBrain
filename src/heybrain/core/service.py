"""AppService — the only layer the CLI talks to.

Orchestrates transcription, Bedrock, memory, reminders, and storage.
Method bodies are stubs for now; each phase in plan.md fills one in.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

from heybrain.audio.record import record_until_enter
from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.prompts import conversation_prompt, summarization_prompt
from heybrain.bedrock.schemas import ConversationAnalysis, ConversationTurn, Intent
from heybrain.core.config import Settings, get_settings
from heybrain.core.errors import HeyBrainError, TranscriptionError
from heybrain.core.models import (
    Conversation,
    ConversationStatus,
    Memory,
    Message,
    Reminder,
    Role,
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
from heybrain.transcription.whisper import transcribe

# plan.md §8.3 — never send full history, only the last N messages + summary.
CONTEXT_WINDOW = 6

_EXIT_WORDS = {"exit", "quit", "bye", ":q"}

# Layer 4 (long-term memory retrieval) is issue #10, not yet built. Question /
# recall / resume intents would normally block on retrieval here; instead we
# proceed with conversation-only context and say so.
_RETRIEVAL_STUBBED_NOTE = (
    "(long-term memory recall isn't wired up yet, so that answer only draws "
    "on this conversation.)"
)


class AppService:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        settings: Settings | None = None,
        bedrock: BedrockService | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self._settings = settings or get_settings()
        self._conn = conn or get_connection(self._settings.db_path)
        self._conversations = ConversationRepo(self._conn)
        self._messages = MessageRepo(self._conn)
        self._memories = MemoryRepo(self._conn)
        self._tasks = TaskRepo(self._conn)
        self._reminders = ReminderRepo(self._conn)
        self._bedrock = bedrock or BedrockService(UsageRepo(self._conn), self._settings)
        self._input = input_fn
        self._output = output_fn

    def think(self, text: str | None = None, *, voice: bool = False) -> Conversation:
        conversation = self._conversations.create(Conversation())
        analyze = True
        first_turn = True

        try:
            while True:
                if first_turn and text is not None:
                    user_text = text
                else:
                    user_text = self._next_input(voice)
                first_turn = False

                stripped = user_text.strip()
                if not stripped or stripped.lower() in _EXIT_WORDS:
                    break

                self._run_turn(conversation, stripped)
        except (KeyboardInterrupt, EOFError):
            analyze = False
            self._output("\nSaving conversation and exiting.")

        return self._close_conversation(conversation, analyze=analyze)

    def _next_input(self, voice: bool) -> str:
        if voice:
            try:
                path = record_until_enter()
                return transcribe(path)
            except TranscriptionError as exc:
                self._output(str(exc))
                self._output("Type it instead:")
                return self._input("> ")
        return self._input("> ")

    def _run_turn(self, conversation: Conversation, user_text: str) -> None:
        self._messages.create(
            Message(conversation_id=conversation.id, role=Role.USER, content=user_text)
        )

        recent = self._messages.list_by_conversation(conversation.id)[-CONTEXT_WINDOW:]
        context_messages = [{"role": m.role.value, "content": m.content} for m in recent]

        # Layer 4 (relevant_memories) is deferred to issue #10 — see module note.
        system = conversation_prompt(conversation_summary=conversation.summary)
        turn = self._bedrock.structured(
            context_messages, system, ConversationTurn, effort="medium"
        )

        self._messages.create(
            Message(conversation_id=conversation.id, role=Role.ASSISTANT, content=turn.reply)
        )

        self._output(turn.reply)
        if turn.intent in (Intent.QUESTION, Intent.RECALL, Intent.RESUME):
            self._output(_RETRIEVAL_STUBBED_NOTE)
        # Reminder intent: persisted as a normal message for now — real
        # extraction lands with issue #13.

    def _close_conversation(self, conversation: Conversation, *, analyze: bool) -> Conversation:
        if analyze:
            messages = self._messages.list_by_conversation(conversation.id)
            if messages:
                conversation_text = "\n".join(
                    f"{m.role.value}: {m.content}" for m in messages
                )
                try:
                    analysis = self._bedrock.structured(
                        [
                            {
                                "role": "user",
                                "content": summarization_prompt(
                                    conversation_text=conversation_text
                                ),
                            }
                        ],
                        system="Extract a structured summary of this conversation.",
                        schema=ConversationAnalysis,
                        effort="low",
                    )
                    conversation.title = analysis.title
                    conversation.summary = analysis.summary
                    conversation.topic = analysis.topic
                    # Memory candidates / tasks extraction belongs to issue #9;
                    # not applied here.
                except HeyBrainError as exc:
                    self._output(f"Couldn't summarize that conversation ({exc}).")

        conversation.status = ConversationStatus.CLOSED
        conversation.updated_at = datetime.now(timezone.utc)
        return self._conversations.update(conversation)

    def remember(self, text: str) -> Memory:
        raise NotImplementedError

    def recall(self, query: str) -> str:
        raise NotImplementedError

    def resume(self, topic: str | None = None) -> Conversation:
        raise NotImplementedError

    def list_conversations(self) -> list[Conversation]:
        return self._conversations.list_recent()

    def show_conversation(self, conversation_id: str) -> tuple[Conversation, list[Message]]:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise HeyBrainError(f"No conversation found with id {conversation_id!r}")
        return conversation, self._messages.list_by_conversation(conversation_id)

    def list_reminders(self) -> list[Reminder]:
        raise NotImplementedError

    def tick_reminders(self) -> None:
        raise NotImplementedError

    def doctor(self) -> dict[str, bool]:
        raise NotImplementedError
