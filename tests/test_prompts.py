from heybrain.bedrock import prompts


def test_conversation_prompt_renders_bare() -> None:
    text = prompts.conversation_prompt()
    assert "capture" in text and "reminder" in text


def test_conversation_prompt_includes_context_when_given() -> None:
    text = prompts.conversation_prompt(
        conversation_summary="Discussing Kafka for interview prep.",
        relevant_memories=["User wants to learn Kafka."],
    )
    assert "Kafka for interview prep" in text
    assert "User wants to learn Kafka." in text


def test_memory_extraction_prompt_instructs_no_quotes() -> None:
    text = prompts.memory_extraction_prompt(conversation_text="user: I like Kafka")
    assert "never a quote" in text or "never a quote of what was said" in text
    assert "topic label" in text
    assert "user: I like Kafka" in text


def test_summarization_prompt_renders() -> None:
    text = prompts.summarization_prompt(conversation_text="hello world")
    assert "hello world" in text


def test_recall_synthesis_prompt_empty_memories() -> None:
    text = prompts.recall_synthesis_prompt(query="what about Kafka?", memories=[])
    assert "No relevant memories were found" in text


def test_recall_synthesis_prompt_with_memories() -> None:
    text = prompts.recall_synthesis_prompt(
        query="what about Kafka?", memories=["User wants to learn Kafka."]
    )
    assert "User wants to learn Kafka." in text
    assert "only these memories" in text


def test_continuation_prompt_renders() -> None:
    text = prompts.continuation_prompt(
        topic="kafka-prep",
        summaries=["Discussed Kafka fundamentals."],
        memories=["User wants to learn Kafka."],
        open_tasks=["Read the Kafka docs."],
    )
    assert "kafka-prep" in text
    assert "Discussed Kafka fundamentals." in text
    assert "Read the Kafka docs." in text
    assert "forward-looking question" in text


def test_reminder_extraction_prompt_includes_datetime_context() -> None:
    text = prompts.reminder_extraction_prompt(
        message="remind me tomorrow at 7pm",
        current_datetime="2026-08-13T09:00:00-07:00",
        timezone="America/Los_Angeles",
    )
    assert "2026-08-13T09:00:00-07:00" in text
    assert "America/Los_Angeles" in text
    assert "remind me tomorrow at 7pm" in text


def test_dedupe_verdict_prompt_lists_options() -> None:
    text = prompts.dedupe_verdict_prompt(
        existing_memory="User wants to learn Kafka.",
        candidate_memory="User should study Kafka before interviews.",
    )
    assert "merge" in text and "supersede" in text and "separate" in text


def test_prompts_have_no_pressure_language() -> None:
    module_source = "\n".join(
        getattr(prompts, name)(**_sample_kwargs(name))
        if callable(getattr(prompts, name))
        else str(getattr(prompts, name))
        for name in dir(prompts)
        if not name.startswith("_") and name != "INTENTS"
    )
    for banned in ("CRITICAL", "YOU MUST", "MUST NOT"):
        assert banned not in module_source


def _sample_kwargs(name: str) -> dict:
    return {
        "conversation_prompt": {},
        "memory_extraction_prompt": {"conversation_text": "x"},
        "summarization_prompt": {"conversation_text": "x"},
        "recall_synthesis_prompt": {"query": "x", "memories": []},
        "continuation_prompt": {"topic": "x", "summaries": [], "memories": []},
        "reminder_extraction_prompt": {
            "message": "x",
            "current_datetime": "x",
            "timezone": "x",
        },
        "dedupe_verdict_prompt": {"existing_memory": "x", "candidate_memory": "x"},
    }[name]
