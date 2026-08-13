# Issue #12 — `brain resume`

## Purpose

`brain resume` is the product's defining loop: stop a topic today, continue it days later. `AppService.list_recent_topics()` surfaces the distinct topics the user has ever touched, most-recently-touched first; `AppService.resume(topic)` reconstructs one of them from what's actually stored (conversation summaries, active memories, open tasks) via `continuation_prompt`, then hands off into the same interactive turn loop `brain think` uses — but always inside a brand-new conversation row, never by reopening an old one.

## Public API

```python
class AppService:
    def list_recent_topics(self, limit: int = 10) -> list[TopicSummary]: ...

    def resume(self, topic: str | None = None, *, voice: bool = False) -> Conversation: ...
```

- `list_recent_topics(limit=10)` — merges `ConversationRepo.distinct_topics()` and `MemoryRepo.distinct_topics()`, keyed by topic label, taking the max last-touched timestamp per label where the two sources disagree. Returns `TopicSummary(topic: str, last_touched_at: datetime)`, sorted descending, truncated to `limit`. Empty store → `[]`.
- `resume(topic, voice=False)` — `topic=None` falls back to the single most-recently-touched topic (raises `HeyBrainError` if there are none). Otherwise `topic` is expected to already be an exact, existing topic label — resolution (picker/fuzzy match) is the CLI's job, not this method's. Raises `HeyBrainError` if the topic has no summaries, memories, or open tasks (nothing to reconstruct, and nothing to invent). Returns the new `Conversation` once the interactive loop ends (same lifecycle as `think()`: closed, analyzed, background extraction started if a capture turn happened).

```python
# heybrain.cli.resume
def resolve_topic(query: str, topics: list[str]) -> str | None: ...
def run(topic: str | None, voice: bool) -> None: ...
```

- No `topic` argument → `run` calls `list_recent_topics()`, prints a numbered picker (`index. topic  (last touched YYYY-MM-DD HH:MM)`), and prompts for a number.
- `topic` argument given → `resolve_topic(query, topics)` matches it against all known topic labels: exact match (case-insensitive) → substring containment (`"kafka"` matches `"Kafka learning plan"`) → `difflib.get_close_matches` (cutoff `0.4`) for typos. Returns `None` (and prints an error) if nothing is close enough. This is the floor requirement — there's no topics table for FK lookups, and no semantic/embedding fallback is wired in yet.

## Key constraints

- **Never reopens a closed conversation.** `resume()` always calls `ConversationRepo.create(Conversation(topic=..., ...))` for a fresh row before handing off to the shared turn loop (`AppService._converse`, extracted from `think()` so both paths share it). The old conversation(s) for that topic are read-only inputs to reconstruction; their `status`/`summary`/etc. are never mutated by `resume`.
- **Topic identity is a string label, not a foreign key** (plan.md §7 — no `topics` table exists, by design). `resume` and `list_recent_topics` both key off the literal `topic` string on `conversations`/`memories` rows. This means the topic label assigned when a *new* resumed conversation eventually closes (via the normal `ConversationAnalysis` summarization) can drift from the label it was resumed under — that's inherent to the design, not a bug.
- **`continuation_prompt` only ever sees real stored data**: closed conversations' `.summary` for that topic (`ConversationRepo.list_by_topic`), active memories (`MemoryRetriever.retrieve_by_topic`, exact-topic match, no embedding call), and open tasks (`TaskRepo.list_open_by_topic`, joined through `conversations.topic` since tasks carry no topic of their own). Nothing else is fed in — the prompt explicitly instructs the model to state only what was previously discussed, with nothing invented, and to end with a forward-looking question. The model's structured reply (`TopicReconstruction { topic, summary, open_threads }`) becomes the new conversation's first assistant message, so it's part of the turn history the rest of the session sees.
