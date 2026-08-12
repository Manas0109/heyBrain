"""Prompt text for every Bedrock call.

Pure text — module-level constants or functions that format and return a
string. No I/O, no Bedrock/langchain imports, no side effects. Orchestration
(context assembly, model choice, effort, schema binding) lives in the
callers, not here.

Kept terse and non-prescriptive: current models follow instructions
literally, and over-scripted prompts degrade output quality.
"""

from __future__ import annotations

INTENTS = "capture, question, recall, resume, reminder"


def conversation_prompt(
    *,
    conversation_summary: str | None = None,
    relevant_memories: list[str] | None = None,
) -> str:
    """System prompt for one `think` turn.

    Classifies intent and produces the reply in a single call — there is no
    separate classification round-trip on this path (plan.md §9).
    """
    parts = [
        "You are heyBrain, a terminal thinking partner. The user is dumping "
        "thoughts, asking questions, or picking up earlier threads.",
        f"Classify their message as one of: {INTENTS}. Then reply directly "
        "to what they said, in the same turn.",
        "capture: they're thinking out loud, no question to answer — "
        "acknowledge briefly, don't interrogate.",
        "question: they want an answer grounded in the memories below, if any.",
        "recall: they're asking what they previously thought about something.",
        "resume: they want to continue an earlier topic.",
        "reminder: they want to be reminded of something at a future time.",
    ]
    if conversation_summary:
        parts.append(f"Conversation so far: {conversation_summary}")
    if relevant_memories:
        joined = "\n".join(f"- {memory}" for memory in relevant_memories)
        parts.append(f"Relevant memories:\n{joined}")
    return "\n\n".join(parts)


def memory_extraction_prompt(*, conversation_text: str) -> str:
    """Prompt for pulling durable memory candidates out of a conversation.

    Facts must be rewritten as self-contained statements, never quotes
    (plan.md §8.1), and each gets a short topic label.
    """
    return (
        "Read the conversation below and pull out anything worth remembering "
        "long-term: ideas, goals, preferences, facts, decisions, plans.\n\n"
        "Rewrite each as a self-contained statement in third person — never "
        "a quote of what was said. 'User said \"Kafka is interesting\"' is "
        "wrong; 'User wants to learn Kafka for system design interview prep' "
        "is right.\n\n"
        "Assign each a short topic label and an importance from 0.0 to 1.0 "
        "— low for passing chatter, high for something they'd want surfaced "
        "again later. Skip anything not worth remembering; an empty list is "
        "a fine answer.\n\n"
        f"Conversation:\n{conversation_text}"
    )


def summarization_prompt(*, conversation_text: str) -> str:
    """Prompt for a conversation's title, summary, and topic label."""
    return (
        "Summarize the conversation below: a short title, a one- or "
        "two-sentence summary, and a short topic label consistent with how "
        "the user talks about this subject.\n\n"
        f"Conversation:\n{conversation_text}"
    )


def recall_synthesis_prompt(*, query: str, memories: list[str]) -> str:
    """Prompt for answering a recall query from retrieved memories only."""
    if not memories:
        return (
            f"The user asked: {query}\n\n"
            "No relevant memories were found. Say so plainly — don't "
            "invent an answer."
        )
    joined = "\n".join(f"- {memory}" for memory in memories)
    return (
        f"The user asked: {query}\n\n"
        f"Answer using only these memories, nothing else:\n{joined}\n\n"
        "Synthesize a direct answer in your own words. If the memories "
        "don't actually cover what was asked, say that instead of "
        "stretching them to fit."
    )


def continuation_prompt(
    *,
    topic: str,
    summaries: list[str],
    memories: list[str],
    open_tasks: list[str] | None = None,
) -> str:
    """Prompt for reconstructing a topic so the user can pick it back up."""
    parts = [f"The user wants to resume the topic '{topic}'."]
    if summaries:
        joined = "\n".join(f"- {summary}" for summary in summaries)
        parts.append(f"Earlier conversations on this topic:\n{joined}")
    if memories:
        joined = "\n".join(f"- {memory}" for memory in memories)
        parts.append(f"Relevant memories:\n{joined}")
    if open_tasks:
        joined = "\n".join(f"- {task}" for task in open_tasks)
        parts.append(f"Open tasks:\n{joined}")
    parts.append(
        "State plainly what was previously discussed — nothing invented — "
        "then end with a forward-looking question that picks the thread "
        "back up."
    )
    return "\n\n".join(parts)


def reminder_extraction_prompt(
    *, message: str, current_datetime: str, timezone: str
) -> str:
    """Prompt for resolving a spoken reminder to an absolute datetime.

    Needs the caller's current local datetime and timezone so relative
    phrasing ("tomorrow at 7pm") resolves correctly (plan.md §11).
    """
    return (
        f"The current local datetime is {current_datetime} ({timezone}).\n\n"
        f"The user said: {message}\n\n"
        "Extract a short reminder title and resolve the requested time to "
        "an absolute, timezone-aware ISO 8601 datetime in that timezone. "
        "If a recurrence was mentioned, record it as free text; otherwise "
        "leave it unset. If no time was given or it's ambiguous, resolve it "
        "as best you can rather than guessing wildly."
    )


def dedupe_verdict_prompt(*, existing_memory: str, candidate_memory: str) -> str:
    """Prompt for deciding how a new memory relates to a near-duplicate."""
    return (
        f"Existing memory: {existing_memory}\n"
        f"New candidate: {candidate_memory}\n\n"
        "These were flagged as near-duplicates. Decide one of:\n"
        "- merge: they describe the same fact; combine them into one "
        "statement that keeps everything useful from both.\n"
        "- supersede: the candidate replaces the existing one (it's newer "
        "or corrects it).\n"
        "- separate: they're actually different facts; keep both.\n\n"
        "Prefer merge or supersede unless the two are genuinely distinct."
    )
