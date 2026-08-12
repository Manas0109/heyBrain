from typer.testing import CliRunner

from heybrain.bedrock.schemas import ConversationAnalysis, Intent, MemoryCandidate
from heybrain.cli.main import app
from heybrain.core.models import Conversation, MemoryType


def test_imports_touch_no_network() -> None:
    assert Intent.CAPTURE == "capture"
    assert MemoryType.IDEA == "idea"


def test_memory_candidate_importance_bounds() -> None:
    candidate = MemoryCandidate(content="x", memory_type="fact", importance=0.5, topic="t")
    assert 0.0 <= candidate.importance <= 1.0


def test_conversation_analysis_roundtrip() -> None:
    analysis = ConversationAnalysis(
        title="t", summary="s", topic="topic", memory_candidates=[], tasks=[]
    )
    assert analysis.memory_candidates == []


def test_conversation_defaults() -> None:
    conversation = Conversation()
    assert conversation.status == "open"
    assert conversation.created_at.tzinfo is not None


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["think", "remember", "recall", "resume", "list", "show", "reminders", "doctor"]:
        assert command in result.output
