"""Tests for issue #13 -- reminders.

Bedrock is faked everywhere (no AWS); the osascript notify adapter is
mocked/stubbed everywhere (no real macOS notification calls).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from heybrain.bedrock.schemas import ConversationAnalysis, ConversationTurn, ReminderCandidate
from heybrain.core.config import Settings
from heybrain.core.models import ReminderStatus, Task
from heybrain.core.service import AppService
from heybrain.reminders.notify import notify
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ReminderRepo, TaskRepo


class FakeBedrock:
    def __init__(self, turns, reminder_candidates, analysis=None) -> None:
        self._turns = list(turns)
        self._reminder_candidates = list(reminder_candidates)
        self._analysis = analysis or ConversationAnalysis(
            title="t", summary="s", topic="top", memory_candidates=[], tasks=[]
        )
        self.structured_calls: list[tuple[list[dict], str, type]] = []

    def structured(self, messages, system, schema, effort, model=None):
        self.structured_calls.append((messages, system, schema))
        if schema is ConversationAnalysis:
            return self._analysis
        if schema is ReminderCandidate:
            return self._reminder_candidates.pop(0)
        return self._turns.pop(0)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


# --- notify adapter -----------------------------------------------------


def test_notify_invokes_osascript_with_escaped_text() -> None:
    with patch("heybrain.reminders.notify.subprocess.run") as mock_run:
        notify('Title "quoted"', "line with \\ backslash")

    args = mock_run.call_args[0][0]
    assert args[0] == "osascript"
    assert args[1] == "-e"
    script = args[2]
    assert 'Title \\"quoted\\"' in script
    assert "line with \\\\ backslash" in script


def test_notify_never_raises_on_failure() -> None:
    with patch("heybrain.reminders.notify.subprocess.run", side_effect=OSError("no osascript")):
        notify("t", "m")  # must not raise


# --- reminder-intent flow in think --------------------------------------


def test_think_reminder_intent_saves_task_and_reminder(settings, conn) -> None:
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    turns = [ConversationTurn(intent="reminder", reply="Sure thing.")]
    candidates = [
        ReminderCandidate(title="Continue the coding agent", scheduled_at=future)
    ]
    bedrock = FakeBedrock(turns, candidates)
    inputs = iter([""])
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    conversation = service.think("remind me in 2 hours to continue the coding agent")

    task_repo = TaskRepo(conn)
    tasks = task_repo.list_by_conversation(conversation.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Continue the coding agent"

    pending = service.list_reminders()
    assert len(pending) == 1
    assert pending[0].task_id == tasks[0].id
    assert pending[0].status == ReminderStatus.PENDING

    assert any("I'll remind you at" in line for line in outputs)


def test_think_reminder_past_time_is_rejected_and_reasked(settings, conn) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    turns = [ConversationTurn(intent="reminder", reply="Okay.")]
    candidates = [
        ReminderCandidate(title="do the thing", scheduled_at=past),
        ReminderCandidate(title="do the thing", scheduled_at=future),
    ]
    bedrock = FakeBedrock(turns, candidates)
    # First input starts the conversation; second is the re-ask reply for the
    # reminder time; third ends the outer think loop.
    inputs = iter(["tomorrow at 9am", ""])
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    conversation = service.think("remind me yesterday to do the thing")

    task_repo = TaskRepo(conn)
    tasks = task_repo.list_by_conversation(conversation.id)
    assert len(tasks) == 1  # only saved once, after the corrected time

    pending = service.list_reminders()
    assert len(pending) == 1

    assert any("already passed" in line for line in outputs)
    assert any("I'll remind you at" in line for line in outputs)


def test_think_reminder_past_time_blank_reask_skips(settings, conn) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    turns = [ConversationTurn(intent="reminder", reply="Okay.")]
    candidates = [ReminderCandidate(title="do the thing", scheduled_at=past)]
    bedrock = FakeBedrock(turns, candidates)
    inputs = iter(["", ""])  # blank re-ask reply, then end outer loop
    outputs: list[str] = []

    service = AppService(
        conn=conn,
        settings=settings,
        bedrock=bedrock,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    conversation = service.think("remind me yesterday to do the thing")

    task_repo = TaskRepo(conn)
    assert task_repo.list_by_conversation(conversation.id) == []
    assert service.list_reminders() == []
    assert any("skipping that reminder" in line for line in outputs)


# --- tick logic ----------------------------------------------------------


def _seed_reminder(conn, *, conversation_id: str, scheduled_at: datetime):
    task_repo = TaskRepo(conn)
    reminder_repo = ReminderRepo(conn)
    task = task_repo.create(Task(conversation_id=conversation_id, title="a task"))
    from heybrain.core.models import Reminder

    return reminder_repo.create(Reminder(task_id=task.id, scheduled_at=scheduled_at))


def _make_conversation(conn):
    from heybrain.storage.repositories import ConversationRepo
    from heybrain.core.models import Conversation

    return ConversationRepo(conn).create(Conversation())


def test_tick_fires_due_now_reminder_without_prefix(settings, conn) -> None:
    conversation = _make_conversation(conn)
    now = datetime.now(timezone.utc)
    reminder = _seed_reminder(conn, conversation_id=conversation.id, scheduled_at=now)

    bedrock = FakeBedrock([], [])
    service = AppService(conn=conn, settings=settings, bedrock=bedrock)
    notified = []
    summary = service.tick_reminders(now=now, notify_fn=lambda t, m: notified.append((t, m)))

    assert [r.id for r in summary.fired] == [reminder.id]
    assert summary.missed == []
    assert len(notified) == 1
    assert "(overdue)" not in notified[0][1]

    updated = ReminderRepo(conn).get(reminder.id)
    assert updated.status == ReminderStatus.FIRED
    assert updated.fired_at is not None


def test_tick_fires_overdue_under_24h_with_prefix(settings, conn) -> None:
    conversation = _make_conversation(conn)
    now = datetime.now(timezone.utc)
    scheduled = now - timedelta(hours=2)
    reminder = _seed_reminder(conn, conversation_id=conversation.id, scheduled_at=scheduled)

    bedrock = FakeBedrock([], [])
    service = AppService(conn=conn, settings=settings, bedrock=bedrock)
    notified = []
    summary = service.tick_reminders(now=now, notify_fn=lambda t, m: notified.append((t, m)))

    assert [r.id for r in summary.fired] == [reminder.id]
    assert notified[0][1].startswith("(overdue)")

    updated = ReminderRepo(conn).get(reminder.id)
    assert updated.status == ReminderStatus.FIRED


def test_tick_marks_overdue_beyond_24h_as_missed_without_firing(settings, conn) -> None:
    conversation = _make_conversation(conn)
    now = datetime.now(timezone.utc)
    scheduled = now - timedelta(hours=30)
    reminder = _seed_reminder(conn, conversation_id=conversation.id, scheduled_at=scheduled)

    bedrock = FakeBedrock([], [])
    service = AppService(conn=conn, settings=settings, bedrock=bedrock)
    notified = []
    summary = service.tick_reminders(now=now, notify_fn=lambda t, m: notified.append((t, m)))

    assert summary.fired == []
    assert [r.id for r in summary.missed] == [reminder.id]
    assert notified == []

    updated = ReminderRepo(conn).get(reminder.id)
    assert updated.status == ReminderStatus.MISSED


def test_tick_ignores_future_reminders(settings, conn) -> None:
    conversation = _make_conversation(conn)
    now = datetime.now(timezone.utc)
    _seed_reminder(conn, conversation_id=conversation.id, scheduled_at=now + timedelta(hours=1))

    bedrock = FakeBedrock([], [])
    service = AppService(conn=conn, settings=settings, bedrock=bedrock)
    summary = service.tick_reminders(now=now, notify_fn=lambda t, m: None)

    assert summary.fired == []
    assert summary.missed == []


def test_tick_default_notify_fn_is_osascript_adapter(settings, conn, monkeypatch) -> None:
    conversation = _make_conversation(conn)
    now = datetime.now(timezone.utc)
    _seed_reminder(conn, conversation_id=conversation.id, scheduled_at=now)

    calls = []
    monkeypatch.setattr(
        "heybrain.core.service.osascript_notify", lambda t, m: calls.append((t, m))
    )

    bedrock = FakeBedrock([], [])
    service = AppService(conn=conn, settings=settings, bedrock=bedrock)
    service.tick_reminders(now=now)

    assert len(calls) == 1
