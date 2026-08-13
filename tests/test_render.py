"""Unit tests for cli/render.py (issue #14).

These check that render functions produce the expected rich output/structure
for given Pydantic models -- no real terminal, no network, no AWS. Each
function takes an `out` console so output can be captured deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import typer
from rich.console import Console

from heybrain.cli import render
from heybrain.core.errors import BedrockError, HeyBrainError, StorageError, TranscriptionError
from heybrain.core.models import (
    Conversation,
    ConversationStatus,
    Memory,
    MemoryType,
    Message,
    RecallResult,
    Role,
    TopicSummary,
)


def _console() -> Console:
    return Console(file=None, record=True, width=100, force_terminal=True, color_system="truecolor")


def _plain(console: Console) -> str:
    return console.export_text()


def _memory(memory_type: MemoryType, *, topic: str = "demo") -> Memory:
    return Memory(
        conversation_id="conv-1",
        memory_type=memory_type,
        content=f"A rewritten {memory_type.value} fact.",
        topic=topic,
        importance=0.8,
        created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )


def test_memory_badge_uses_a_distinct_style_per_memory_type() -> None:
    styles = {t: render.memory_badge(t).style for t in MemoryType}
    assert len(set(styles.values())) == len(MemoryType), "every MemoryType needs its own style"
    for memory_type, badge in ((t, render.memory_badge(t)) for t in MemoryType):
        assert memory_type.value in badge.plain


def test_memory_card_includes_index_topic_date_and_content() -> None:
    memory = _memory(MemoryType.IDEA, topic="ai-agents")
    card = render.memory_card(memory, index=3)
    assert "[3]" in card.plain
    assert "idea" in card.plain
    assert "ai-agents" in card.plain
    assert "2026-08-13" in card.plain
    assert memory.content in card.plain


def test_print_recall_result_puts_answer_before_numbered_sources() -> None:
    console = _console()
    result = RecallResult(
        answer="You've been excited about AI coding agents.",
        memories=[_memory(MemoryType.IDEA), _memory(MemoryType.GOAL)],
    )
    render.print_recall_result(result, out=console)

    text = _plain(console)
    answer_pos = text.index(result.answer)
    sources_pos = text.index("Sources:")
    first_source_pos = text.index("[1]")
    assert answer_pos < sources_pos < first_source_pos
    assert "[2]" in text


def test_print_recall_result_with_no_memories_omits_sources_section() -> None:
    console = _console()
    result = RecallResult(answer="I don't have anything on that yet.", memories=[])
    render.print_recall_result(result, out=console)

    text = _plain(console)
    assert result.answer in text
    assert "Sources:" not in text


def test_print_transcript_labels_user_and_assistant_turns() -> None:
    console = _console()
    conversation = Conversation(
        id="conv-1", title="Kafka", summary="Discussed Kafka.", topic="kafka",
        status=ConversationStatus.CLOSED,
    )
    messages = [
        Message(conversation_id="conv-1", role=Role.USER, content="Tell me about Kafka."),
        Message(conversation_id="conv-1", role=Role.ASSISTANT, content="Kafka is a log."),
    ]
    render.print_transcript(conversation, messages, out=console)

    text = _plain(console)
    assert "Kafka" in text
    assert "Discussed Kafka." in text
    assert "closed" in text
    assert "You:" in text
    assert "brain:" in text
    assert "Tell me about Kafka." in text
    assert "Kafka is a log." in text


def test_print_conversations_shows_status_and_title() -> None:
    console = _console()
    conversations = [
        Conversation(id="c1", title="Open one", status=ConversationStatus.OPEN),
        Conversation(id="c2", title="Closed one", status=ConversationStatus.CLOSED),
    ]
    render.print_conversations(conversations, out=console)

    text = _plain(console)
    assert "c1" in text and "Open one" in text and "open" in text
    assert "c2" in text and "Closed one" in text and "closed" in text


def test_print_topics_numbers_each_topic() -> None:
    console = _console()
    topics = [
        TopicSummary(topic="kafka", last_touched_at=datetime(2026, 8, 10, tzinfo=timezone.utc)),
        TopicSummary(topic="coding agents", last_touched_at=datetime(2026, 8, 12, tzinfo=timezone.utc)),
    ]
    render.print_topics(topics, out=console)

    text = _plain(console)
    assert "1. kafka" in text
    assert "2. coding agents" in text


def test_select_from_topics_falls_back_without_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    # `interactive=False` simulates piped input (scripts/demo.sh, CI,
    # `docker run` without -it) without relying on real TTY detection.
    console = _console()
    topics = [
        TopicSummary(topic="kafka", last_touched_at=datetime(2026, 8, 10, tzinfo=timezone.utc)),
        TopicSummary(topic="coding agents", last_touched_at=datetime(2026, 8, 12, tzinfo=timezone.utc)),
    ]
    # typer/click bind `visible_prompt_func = input` at import time, so
    # patching `builtins.input` doesn't reach it -- patch the bound name.
    monkeypatch.setattr("typer._click.termui.visible_prompt_func", lambda _prompt="": "2")

    selected = render.select_from_topics(topics, out=console, interactive=False)

    assert selected == "coding agents"
    text = _plain(console)
    assert "1. kafka" in text
    assert "2. coding agents" in text


def test_select_from_topics_fallback_rejects_non_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _console()
    topics = [TopicSummary(topic="kafka", last_touched_at=datetime(2026, 8, 10, tzinfo=timezone.utc))]
    monkeypatch.setattr("typer._click.termui.visible_prompt_func", lambda _prompt="": "nope")

    selected = render.select_from_topics(topics, out=console, interactive=False)

    assert selected is None
    assert "Not a number" in _plain(console)


def test_select_from_topics_fallback_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    console = _console()
    topics = [TopicSummary(topic="kafka", last_touched_at=datetime(2026, 8, 10, tzinfo=timezone.utc))]
    monkeypatch.setattr("typer._click.termui.visible_prompt_func", lambda _prompt="": "5")

    selected = render.select_from_topics(topics, out=console, interactive=False)

    assert selected is None
    assert "Out of range" in _plain(console)


@pytest.mark.parametrize(
    "exc,expected_fragment",
    [
        (BedrockError("Bedrock request failed: throttled"), "AWS credentials"),
        (TranscriptionError("Empty transcript"), "Microphone"),
        (StorageError("db locked"), "~/.heybrain"),
        (HeyBrainError("No conversation found with id 'x'"), None),
    ],
)
def test_render_exception_prints_message_and_remediation_for_known_errors(
    exc: HeyBrainError, expected_fragment: str | None
) -> None:
    console = _console()
    render.render_exception(exc, out=console)

    text = _plain(console)
    assert str(exc) in text
    assert "✗" in text
    if expected_fragment:
        assert expected_fragment in text
    assert "Traceback" not in text


def test_render_exception_never_leaks_a_traceback_for_unexpected_errors() -> None:
    console = _console()
    render.render_exception(ValueError("boom"), out=console)

    text = _plain(console)
    assert "✗" in text
    assert "internally" in text
    assert "ValueError" in text
    assert "Traceback" not in text
    assert "File \"" not in text


def test_error_without_remediation_prints_only_the_message_line() -> None:
    console = _console()
    render.error("Something specific happened.", out=console)

    text = _plain(console)
    assert "Something specific happened." in text


def test_guard_lets_typer_exit_pass_through_untouched() -> None:
    @render.guard
    def command() -> None:
        raise typer.Exit(code=2)

    with pytest.raises(typer.Exit) as excinfo:
        command()
    assert excinfo.value.exit_code == 2


def test_guard_converts_heybrain_error_into_exit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Exception] = []
    monkeypatch.setattr(render, "render_exception", lambda exc, **_: seen.append(exc))

    @render.guard
    def command() -> None:
        raise HeyBrainError("nope")

    with pytest.raises(typer.Exit) as excinfo:
        command()
    assert excinfo.value.exit_code == 1
    assert isinstance(seen[0], HeyBrainError)


def test_guard_converts_unexpected_exception_into_exit_one_without_raising_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Exception] = []
    monkeypatch.setattr(render, "render_exception", lambda exc, **_: seen.append(exc))

    @render.guard
    def command() -> None:
        raise RuntimeError("kaboom")

    with pytest.raises(typer.Exit) as excinfo:
        command()
    assert excinfo.value.exit_code == 1
    assert isinstance(seen[0], RuntimeError)


def test_guard_does_not_catch_keyboard_interrupt() -> None:
    @render.guard
    def command() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        command()


def test_spinner_is_a_context_manager_that_yields_control() -> None:
    console = _console()
    ran = False
    with render.spinner("Thinking…", out=console):
        ran = True
    assert ran
