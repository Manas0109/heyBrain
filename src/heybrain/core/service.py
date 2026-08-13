"""AppService — the only layer the CLI talks to.

Orchestrates transcription, Bedrock, memory, reminders, and storage.
Method bodies are stubs for now; each phase in plan.md fills one in.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, ContextManager

from heybrain.audio.record import record_until_enter
from heybrain.bedrock.client import BedrockService
from heybrain.bedrock.prompts import (
    continuation_prompt,
    conversation_prompt,
    recall_synthesis_prompt,
    reminder_extraction_prompt,
    summarization_prompt,
)
from heybrain.bedrock.schemas import (
    ConversationAnalysis,
    ConversationTurn,
    Intent,
    RecallSynthesis,
    ReminderCandidate,
    TopicReconstruction,
)
from heybrain.core.config import Settings, get_settings
from heybrain.core.errors import HeyBrainError, TranscriptionError
from heybrain.core.models import (
    Conversation,
    ConversationStatus,
    Memory,
    Message,
    RecallResult,
    Reminder,
    ReminderStatus,
    Role,
    Task,
    TopicSummary,
)
from heybrain.memory.retriever import MemoryRetriever
from heybrain.memory.service import MemoryService
from heybrain.memory.vectors import VectorStore
from heybrain.reminders.notify import notify as osascript_notify
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

logger = logging.getLogger(__name__)

# plan.md §8.3 — never send full history, only the last N messages + summary.
CONTEXT_WINDOW = 6

_EXIT_WORDS = {"exit", "quit", "bye", ":q"}

# plan.md §8.3 layer 4 -- top-K=5 relevant long-term memories, only pulled in
# for question/recall/resume turns; a pure capture turn never needs a vector
# search.
RETRIEVAL_K = 5

# plan.md §8.4 -- recall never invents an answer when nothing was found.
# Skipping the Bedrock call entirely (rather than trusting the prompt alone)
# means an empty store never depends on the model actually following the
# "don't invent an answer" instruction.
_NO_MEMORIES_MESSAGE = "I don't have anything on that yet."

# Words/punctuation that mark a turn as plausibly a question, recall, or
# resume, so it's worth an embed + Chroma search before replying. Deliberately
# cheap and over-inclusive (a false positive just costs one extra vector
# search) rather than an LLM call, which would reintroduce the
# classify-then-reply round trip plan.md §9 rules out.
_RETRIEVAL_KEYWORDS = (
    "remember", "recall", "earlier", "before", "previously", "again",
    "continue", "resume", "picking up", "pick up", "what did", "what was",
    "what were", "what have",
)


def _looks_like_retrieval_turn(text: str) -> bool:
    lowered = text.lower()
    if "?" in lowered:
        return True
    return any(keyword in lowered for keyword in _RETRIEVAL_KEYWORDS)


# plan.md §11 -- a tick fires every 60s; anything scheduled within the last
# tick is "on time", not overdue, so it fires without the "(overdue)" prefix.
_OVERDUE_GRACE = timedelta(minutes=1)
_MISSED_AFTER = timedelta(hours=24)


@dataclass
class ReminderTickSummary:
    fired: list[Reminder] = field(default_factory=list)
    missed: list[Reminder] = field(default_factory=list)


SpinnerFactory = Callable[[str], ContextManager[None]]


def _default_spinner(label: str) -> ContextManager[None]:
    """No-op spinner used when the caller (e.g. a test) doesn't supply one.

    Keeps this module free of any rich/CLI dependency -- the real spinner is
    injected by cli/render.py so every blocking call over ~500ms gets a
    labeled spinner (issue #14) without core knowing rich exists.
    """
    return nullcontext()


class AppService:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        settings: Settings | None = None,
        vector_store: VectorStore | None = None,
        bedrock: BedrockService | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        spinner_fn: SpinnerFactory = _default_spinner,
    ) -> None:
        self._settings = settings or get_settings()
        self._conn = conn or get_connection(self._settings.db_path)
        self._conversations = ConversationRepo(self._conn)
        self._messages = MessageRepo(self._conn)
        self._memories = MemoryRepo(self._conn)
        self._tasks = TaskRepo(self._conn)
        self._reminders = ReminderRepo(self._conn)
        self._vector_store = vector_store or VectorStore(self._settings.chroma_dir)
        self._bedrock = bedrock or BedrockService(UsageRepo(self._conn), self._settings)
        self._input = input_fn
        self._output = output_fn
        self._spinner_fn = spinner_fn

        self._memory_service = MemoryService(
            bedrock=self._bedrock,
            vector_store=self._vector_store,
            memory_repo=self._memories,
            message_repo=self._messages,
        )
        self._memory_retriever = MemoryRetriever(
            bedrock=self._bedrock,
            vector_store=self._vector_store,
            memory_repo=self._memories,
        )
        # Guards the shared connection against the background extraction
        # thread (issue #9, plan.md §9) racing a foreground call.
        self._db_lock = threading.Lock()
        self._extraction_thread: threading.Thread | None = None

    def reindex(self) -> int:
        """Rebuild Chroma from SQLite. Chroma is disposable; SQLite is authoritative."""
        memories = self._memories.list_all()
        with self._spinner_fn("Embedding memories…"):
            embeddings = self._bedrock.embed([memory.content for memory in memories])
        self._vector_store.rebuild(memories, embeddings)
        return len(memories)

    def think(self, text: str | None = None, *, voice: bool = False) -> Conversation:
        conversation = self._conversations.create(Conversation())
        return self._converse(conversation, text, voice=voice)

    def _converse(
        self, conversation: Conversation, text: str | None, *, voice: bool
    ) -> Conversation:
        """The interactive turn loop shared by `think` and `resume`.

        `resume` hands off here once it has opened a new conversation and
        printed the reconstruction, so continuing after a resume behaves
        exactly like a normal `think` session (plan.md §7).
        """
        analyze = True
        first_turn = True
        had_capture_turn = False

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

                intent = self._run_turn(conversation, stripped)
                if intent == Intent.CAPTURE:
                    had_capture_turn = True
        except (KeyboardInterrupt, EOFError):
            analyze = False
            self._output("\nSaving conversation and exiting.")

        conversation = self._close_conversation(conversation, analyze=analyze)

        # plan.md §9 -- capture-intent turns get their memories extracted on
        # a background thread so the reply above never waits on it. The CLI
        # joins this thread (with a spinner) before the process exits.
        if analyze and had_capture_turn:
            self._start_background_extraction(conversation.id)

        return conversation

    def _next_input(self, voice: bool) -> str:
        if voice:
            try:
                path = record_until_enter()
                with self._spinner_fn("Transcribing…"):
                    return transcribe(path)
            except TranscriptionError as exc:
                self._output(str(exc))
                self._output("Type it instead:")
                return self._input("> ")
        return self._input("> ")

    def _run_turn(self, conversation: Conversation, user_text: str) -> Intent:
        self._messages.create(
            Message(conversation_id=conversation.id, role=Role.USER, content=user_text)
        )

        recent = self._messages.list_by_conversation(conversation.id)[-CONTEXT_WINDOW:]
        context_messages = [{"role": m.role.value, "content": m.content} for m in recent]

        # Layer 4 (plan.md §8.3): retrieval must land in the same call that
        # produces the reply, since intent classification and reply
        # generation happen together (no separate classification round-trip,
        # plan.md §9). True intent isn't known until that call returns, so
        # this is a cheap local guess used only to decide whether it's worth
        # paying for a vector search before asking -- turn.intent below is
        # still the real, authoritative classification.
        relevant_memories: list[str] = []
        if _looks_like_retrieval_turn(user_text):
            memories = self._memory_retriever.retrieve(user_text, k=RETRIEVAL_K)
            relevant_memories = [memory.content for memory in memories]

        system = conversation_prompt(
            conversation_summary=conversation.summary,
            relevant_memories=relevant_memories,
        )
        with self._spinner_fn("Thinking…"):
            turn = self._bedrock.structured(
                context_messages, system, ConversationTurn, effort="medium"
            )

        self._messages.create(
            Message(conversation_id=conversation.id, role=Role.ASSISTANT, content=turn.reply)
        )

        self._output(turn.reply)

        if turn.intent == Intent.REMINDER:
            self._handle_reminder(conversation, user_text)

        return turn.intent

    def _handle_reminder(self, conversation: Conversation, user_text: str) -> None:
        """Resolve a spoken reminder to an absolute time and persist it.

        plan.md §11: the resolved time is echoed back before saving, and a
        time in the past is rejected and re-asked rather than silently
        stored -- this loops on user input until a future time is given or
        the user gives up (blank input).
        """
        message = user_text
        while True:
            local_now = datetime.now().astimezone()
            tz_name = local_now.tzname() or local_now.strftime("%z")
            with self._spinner_fn("Resolving reminder time…"):
                candidate = self._bedrock.structured(
                    [
                        {
                            "role": "user",
                            "content": reminder_extraction_prompt(
                                message=message,
                                current_datetime=local_now.isoformat(),
                                timezone=tz_name,
                            ),
                        }
                    ],
                    system="Resolve the reminder to a structured, timezone-aware datetime.",
                    schema=ReminderCandidate,
                    effort="low",
                )
            scheduled_at = self._parse_reminder_datetime(candidate.scheduled_at)

            if scheduled_at is None or scheduled_at <= datetime.now(timezone.utc):
                self._output(
                    "That time's already passed — when would you like to be "
                    "reminded instead?"
                )
                message = self._input("> ")
                if not message.strip():
                    self._output("Okay, skipping that reminder.")
                    return
                continue

            self._output(
                f"Got it — I'll remind you at {scheduled_at.isoformat()} to "
                f"{candidate.title}."
            )
            task = self._tasks.create(
                Task(conversation_id=conversation.id, title=candidate.title)
            )
            self._reminders.create(Reminder(task_id=task.id, scheduled_at=scheduled_at))
            return

    @staticmethod
    def _parse_reminder_datetime(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed

    def _close_conversation(self, conversation: Conversation, *, analyze: bool) -> Conversation:
        if analyze:
            messages = self._messages.list_by_conversation(conversation.id)
            if messages:
                conversation_text = "\n".join(
                    f"{m.role.value}: {m.content}" for m in messages
                )
                try:
                    with self._spinner_fn("Summarizing conversation…"):
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
                    # Task extraction is not wired up yet (no consumer exists
                    # for TaskCandidate rows). Memory candidates are handled
                    # separately by memory.extractor, not analysis.memory_candidates
                    # -- see _start_background_extraction below.
                except HeyBrainError as exc:
                    self._output(f"Couldn't summarize that conversation ({exc}).")

        conversation.status = ConversationStatus.CLOSED
        conversation.updated_at = datetime.now(timezone.utc)
        return self._conversations.update(conversation)

    def _start_background_extraction(self, conversation_id: str) -> None:
        def run() -> None:
            with self._db_lock:
                self._memory_service.process_conversation(conversation_id)

        thread = threading.Thread(target=run, daemon=True)
        self._extraction_thread = thread
        thread.start()

    def join_pending_extraction(self, timeout: float | None = None) -> bool:
        """Block until background memory extraction finishes.

        The CLI calls this before the process exits, showing a spinner
        while it waits (plan.md §9). Returns True once extraction has
        finished (or there was nothing to wait for); False if `timeout`
        elapsed first.
        """
        thread = self._extraction_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def remember(self, text: str) -> Memory:
        """Force a long-term memory, bypassing the importance threshold.

        Still runs the full dedup pipeline (plan.md §8.1) -- an explicit
        `brain remember` can still be a near-duplicate of something already
        stored. Runs synchronously: the user is waiting on this command.
        """
        conversation = self._conversations.create(
            Conversation(status=ConversationStatus.CLOSED, title=f"remember: {text[:60]}")
        )
        with self._db_lock:
            self._messages.create(
                Message(conversation_id=conversation.id, role=Role.USER, content=text)
            )
            with self._spinner_fn("Checking for duplicates…"):
                return self._memory_service.remember(text, conversation.id)

    def reprocess(self, conversation_id: str) -> list[Memory]:
        """Re-run memory extraction on an existing conversation.

        Escape hatch for interrupted background extraction (plan.md §9):
        if the process was killed before a background extraction thread
        finished, the conversation is still saved but its memories are
        lost, and this re-derives them.
        """
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise HeyBrainError(f"No conversation found with id {conversation_id!r}")
        with self._db_lock, self._spinner_fn("Re-extracting memories…"):
            return self._memory_service.process_conversation(conversation_id)

    def recall(self, query: str) -> RecallResult:
        """Semantic search + LLM synthesis (plan.md §8.4).

        Never returns raw search results: an empty retrieval short-circuits
        before any Bedrock call, and a non-empty one is always passed
        through recall_synthesis_prompt so the reply is synthesized, not
        dumped.
        """
        with self._spinner_fn("Searching memories…"):
            memories = self._memory_retriever.retrieve(query, k=RETRIEVAL_K)
        if not memories:
            return RecallResult(answer=_NO_MEMORIES_MESSAGE, memories=[])

        with self._spinner_fn("Synthesizing answer…"):
            synthesis = self._bedrock.structured(
                [
                    {
                        "role": "user",
                        "content": recall_synthesis_prompt(
                            query=query, memories=[memory.content for memory in memories]
                        ),
                    }
                ],
                system="Answer the user's recall query using only the supplied memories.",
                schema=RecallSynthesis,
                effort="medium",
            )
        return RecallResult(answer=synthesis.answer, memories=memories)

    def list_recent_topics(self, limit: int = 10) -> list[TopicSummary]:
        """Distinct topics from conversations and memories, most recently touched first.

        There's no topics table (plan.md §7) -- `topic` is a string label on
        conversations/memories, so this derives the list by merging both
        sources and taking the latest touch per label.
        """
        merged: dict[str, datetime] = {}
        for topic, last_touched_at in (
            *self._conversations.distinct_topics(),
            *self._memories.distinct_topics(),
        ):
            if topic not in merged or last_touched_at > merged[topic]:
                merged[topic] = last_touched_at

        ordered = sorted(merged.items(), key=lambda item: item[1], reverse=True)
        return [
            TopicSummary(topic=topic, last_touched_at=last_touched_at)
            for topic, last_touched_at in ordered[:limit]
        ]

    def resume(self, topic: str | None = None, *, voice: bool = False) -> Conversation:
        """Reconstruct `topic` and hand off into the think loop (plan.md §5, §7).

        Always opens a *new* conversation linked by topic -- a closed
        conversation is never reopened. Callers (the CLI's topic picker /
        fuzzy matcher) are expected to pass an exact, existing topic label;
        `topic=None` falls back to the single most recently touched one.
        """
        if topic is None:
            recent = self.list_recent_topics(limit=1)
            if not recent:
                raise HeyBrainError("No topics to resume yet -- try `brain think` first.")
            topic = recent[0].topic

        with self._spinner_fn("Gathering context…"):
            conversations = self._conversations.list_by_topic(topic)
            summaries = [c.summary for c in conversations if c.summary]
            memories = self._memory_retriever.retrieve_by_topic(topic)
            open_tasks = self._tasks.list_open_by_topic(topic)

        if not summaries and not memories and not open_tasks:
            raise HeyBrainError(f"No topic found matching {topic!r}.")

        system = continuation_prompt(
            topic=topic,
            summaries=summaries,
            memories=[memory.content for memory in memories],
            open_tasks=[task.title for task in open_tasks],
        )
        with self._spinner_fn("Reconstructing topic…"):
            reconstruction = self._bedrock.structured(
                [
                    {
                        "role": "user",
                        "content": f"Reconstruct the topic '{topic}' so we can continue.",
                    }
                ],
                system,
                TopicReconstruction,
                effort="medium",
            )

        reconstruction_text = reconstruction.summary
        if reconstruction.open_threads:
            threads = "\n".join(f"- {thread}" for thread in reconstruction.open_threads)
            reconstruction_text = f"{reconstruction_text}\n\n{threads}"

        conversation = self._conversations.create(
            Conversation(topic=topic, title=f"Resuming: {topic}")
        )
        self._messages.create(
            Message(
                conversation_id=conversation.id,
                role=Role.ASSISTANT,
                content=reconstruction_text,
            )
        )
        self._output(reconstruction_text)

        return self._converse(conversation, None, voice=voice)

    def list_conversations(self) -> list[Conversation]:
        return self._conversations.list_recent()

    def show_conversation(self, conversation_id: str) -> tuple[Conversation, list[Message]]:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise HeyBrainError(f"No conversation found with id {conversation_id!r}")
        return conversation, self._messages.list_by_conversation(conversation_id)

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_reminders(self) -> list[Reminder]:
        """Pending reminders, soonest first."""
        far_future = datetime.now(timezone.utc) + timedelta(days=3650)
        return self._reminders.list_pending_due_before(far_future)

    def tick_reminders(
        self,
        *,
        now: datetime | None = None,
        notify_fn: Callable[[str, str], None] | None = None,
    ) -> ReminderTickSummary:
        """`brain reminders tick` (plan.md §11): fire due reminders, mark missed ones.

        Selects pending reminders with scheduled_at <= now. Overdue by less
        than a tick interval fires normally; overdue by up to 24h fires with
        an "(overdue)" prefix; older than 24h is marked missed without
        firing a notification.
        """
        now = now or datetime.now(timezone.utc)
        notify_fn = notify_fn or osascript_notify
        # list_pending_due_before is a strict "<"; nudge the cutoff so a
        # reminder scheduled for exactly `now` is included too (spec: <=).
        due = self._reminders.list_pending_due_before(now + timedelta(microseconds=1))

        summary = ReminderTickSummary()
        for reminder in due:
            overdue = now - reminder.scheduled_at
            task = self._tasks.get(reminder.task_id)
            title = task.title if task else "Reminder"

            if overdue > _MISSED_AFTER:
                reminder.status = ReminderStatus.MISSED
                self._reminders.update(reminder)
                summary.missed.append(reminder)
                continue

            prefix = "(overdue) " if overdue > _OVERDUE_GRACE else ""
            notify_fn("heyBrain reminder", f"{prefix}{title}")
            reminder.status = ReminderStatus.FIRED
            reminder.fired_at = now
            self._reminders.update(reminder)
            summary.fired.append(reminder)

        return summary

    def doctor(self) -> dict[str, bool]:
        raise NotImplementedError
