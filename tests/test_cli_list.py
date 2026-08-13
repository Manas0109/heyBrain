"""End-to-end CLI test for `brain list --json` (issue #57)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heybrain.cli.main import app
from heybrain.core.config import Settings
from heybrain.core.models import Conversation
from heybrain.core.service import AppService
from heybrain.storage.db import get_connection
from heybrain.storage.repositories import ConversationRepo


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(heybrain_home=tmp_path)


@pytest.fixture
def conn(tmp_path: Path):
    connection = get_connection(tmp_path / "brain.db")
    yield connection
    connection.close()


def test_cli_list_json_outputs_valid_json(settings, conn, monkeypatch) -> None:
    ConversationRepo(conn).create(
        Conversation(id="conv-1", title="First chat", summary="A summary", topic="ai")
    )
    ConversationRepo(conn).create(
        Conversation(id="conv-2", title="Second chat", summary=None, topic="misc")
    )

    monkeypatch.setattr(
        "heybrain.cli.main.AppService",
        lambda **_: AppService(conn=conn, settings=settings),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert {c["id"] for c in parsed} == {"conv-1", "conv-2"}
    for conversation in parsed:
        assert set(conversation) == {
            "id",
            "title",
            "summary",
            "topic",
            "status",
            "created_at",
            "updated_at",
        }


def test_cli_list_without_json_renders_table(settings, conn, monkeypatch) -> None:
    ConversationRepo(conn).create(Conversation(id="conv-1", title="First chat"))

    monkeypatch.setattr(
        "heybrain.cli.main.AppService",
        lambda **_: AppService(conn=conn, settings=settings),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "First chat" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)
