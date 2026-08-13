# heyBrain — Implementation Issues

Derived from `plan.md`. 14 issues across 5 named parallel workstreams (A–E, see §"Suggested parallel workstreams") plus a set of integration issues that depend on multiple streams. Ordering assumes a ~1 week hackathon timebox and possible parallel agents.

**Legend:** 🔴 critical path · 🟡 parallel after contracts · 🟢 stretch

---

## #1 — Contracts, scaffold, and configuration 🔴

**Objective**
Establish the package skeleton, configuration, domain models, and structured-output schemas so every other workstream can start against a stable contract.

**Context**
This is the single unblocker for the entire build. Nothing else can be parallelized until the types exist. `plan.md` §3, §4, §6.5, §7.

**Scope**
- `pyproject.toml` (Python 3.12+, Typer, Pydantic v2, pydantic-settings, langchain-aws, boto3, chromadb, rich, pytest), `Makefile`, `.env.example`, `.gitignore`.
- Full directory tree from `plan.md` §4 with `__init__.py` files.
- `core/config.py` — pydantic-settings reading `AWS_REGION`, `AWS_PROFILE`, `BEDROCK_MODEL_ID`, `BEDROCK_FAST_MODEL_ID`, `BEDROCK_EMBEDDING_MODEL_ID`, `HEYBRAIN_HOME`. Creates `~/.heybrain/` on first access.
- `core/models.py` — `Conversation`, `Message`, `Memory`, `Task`, `Reminder`, `UsageRecord`, plus enums (`MemoryType`, `MemoryStatus`, `ConversationStatus`, `Role`).
- `bedrock/schemas.py` — `MemoryCandidate`, `TaskCandidate`, `ReminderCandidate`, `ConversationAnalysis`, `RecallSynthesis`, `TopicReconstruction`, `Intent` enum (`capture | question | recall | resume | reminder`).
- `core/errors.py` — `HeyBrainError`, `BedrockError`, `TranscriptionError`, `StorageError`.
- `core/service.py` — `AppService` class with method signatures and `NotImplementedError` bodies.
- `cli/main.py` — Typer app with all subcommands from §5 registered as stubs printing "not implemented".
- `tests/` scaffold with one passing smoke test.

**Technical requirements**
- No I/O in `models.py` or `schemas.py`.
- `MemoryCandidate.importance` is `float` constrained `0.0–1.0`.
- All timestamps are timezone-aware `datetime`.
- Schemas must be structured-output compatible: no recursion, no numeric constraints the API rejects, `additionalProperties: false` on objects.

**Acceptance criteria**
- `make install && brain --help` lists every subcommand from §5.
- `pytest` passes.
- Importing `heybrain.core.models` and `heybrain.bedrock.schemas` requires no AWS credentials and touches no network.

**Dependencies** — none
**Parallelization** — blocks everything; do this first, alone
**Order** — 1

---

## #2 — SQLite storage layer 🔴

**Objective**
Persist conversations, messages, memories, tasks, reminders, and usage records.

**Context**
`plan.md` §7. SQLite is the source of truth; Chroma is a rebuildable index.

**Scope**
- `storage/schema.sql` — DDL for all 6 tables from §7, applied idempotently (`CREATE TABLE IF NOT EXISTS`) at startup.
- `storage/db.py` — connection factory pointing at `$HEYBRAIN_HOME/brain.db`, applies schema on first use, sets `PRAGMA foreign_keys=ON` and `journal_mode=WAL`.
- `storage/repositories.py` — `ConversationRepo`, `MessageRepo`, `MemoryRepo`, `TaskRepo`, `ReminderRepo`, `UsageRepo` with CRUD + the query methods the app needs (recent conversations, memories by id list, pending reminders due before T, memories by topic).

**Technical requirements**
- No ORM. Plain `sqlite3` with row factory mapping to the Pydantic models from #1.
- Indexes on `messages.conversation_id`, `memories.topic`, `memories.status`, `reminders(status, scheduled_at)`.
- `MemoryRepo.get_many(ids)` must preserve caller-supplied ordering (retrieval ranks before fetching).
- No migrations framework — schema changes during the hackathon mean deleting the DB.

**Acceptance criteria**
- Round-trip test per repo: write, read back, field-for-field equality.
- Deleting `brain.db` and re-running recreates the schema cleanly.
- Foreign-key violations raise, not silently pass.

**Dependencies** — #1
**Parallelization** — parallel with #3, #4, #5
**Order** — 2

---

## #3 — BedrockService: chat, structured output, embeddings 🔴

**Objective**
One service that owns every model call, so no other module ever sees `botocore` or `langchain_aws` directly.

**Context**
`plan.md` §6. This is the highest-risk issue — model availability by region is the most likely day-one blocker.

**Scope**
- `bedrock/client.py`:
  - `chat(messages, system, effort, model) -> str`
  - `structured(messages, system, schema: type[BaseModel], effort, model) -> BaseModel`
  - `embed(texts: list[str]) -> list[list[float]]`
- Chat/structured via the `langchain-aws` Bedrock **Converse API** client (`ChatBedrockConverse(model_id=..., region_name=...)`) — required for `reasoning_effort`/`output_config.effort`; the legacy `ChatBedrock` (`invoke_model`) wrapper doesn't expose these fields. Model IDs `anthropic.`-prefixed and read from config.
- Embeddings via `langchain-aws`'s `BedrockEmbeddings` against the Titan embedding model.
- Retries: exponential backoff + jitter, max 3 attempts, on throttling / 5xx / timeout only.
- Structured-output validation failure → one repair retry → raise `BedrockError` with a `recoverable=True` flag so callers can degrade gracefully.
- `bedrock/usage.py` — record `request_id, operation, model_id, input_tokens, output_tokens, latency_ms` to the `usage` table on every call. **Never log conversation content here.**
- Translate every `botocore` / `anthropic` exception into `BedrockError` before it leaves the module.
- Timeouts: 30s chat, 10s embeddings.

**Technical requirements — these return HTTP 400 if violated**
- Do **not** send `temperature`, `top_p`, or `top_k`.
- Do **not** send `budget_tokens`. Use `output_config={"effort": ...}`.
- Do **not** use assistant-turn prefills to force JSON — use structured outputs.
- Thinking is on by default on Opus-tier models and counts against `max_tokens`; size `max_tokens` with headroom (start at 4096 for chat).
- Effort per operation per §6.4.

**Acceptance criteria**
- A live manual script performs: a chat turn, a structured extraction validating against `ConversationAnalysis`, and an embedding returning the expected dimensionality.
- Unit tests replay recorded JSON fixtures — **no test hits AWS**.
- Throttling is retried; a malformed structured response triggers exactly one repair attempt then raises.
- A `usage` row is written per call.

**Dependencies** — #1 (schemas), #2 (usage table)
**Parallelization** — #2 and #3 are **not** independently parallel: #3 needs the `usage` table. To run them side by side, stub `UsageRepo` behind its interface (frozen in #1) and write against the stub; swap in the real repo once #2 lands. Absent that stub, treat #2 → #3 as sequential (see Stream A).
**Order** — 2

---

## #4 — Prompt library and eval set 🟡

**Objective**
All prompt text, version-controlled and separate from orchestration, plus the eval set used to tune it.

**Context**
`plan.md` §6.5, §16. Built *during* development, not at the end — the eval set is how prompts get tuned.

**Scope**
- `bedrock/prompts.py`: `conversation_prompt`, `memory_extraction_prompt`, `summarization_prompt`, `recall_synthesis_prompt`, `continuation_prompt`, `reminder_extraction_prompt`, `dedupe_verdict_prompt`.
- The conversation prompt must produce intent classification **and** the reply in one call (§9) — no separate classification round-trip. This supersedes the "Intent classification | fast model | low" row in the §6.4 effort table, which describes a standalone classification call that §9 explicitly rules out. The merged call runs on the **primary model at `medium` effort** (the conversation-turn row in §6.4) — intent classification does not get its own fast-model call on this path. If quality suffers, revisit before splitting the call back out.
- The memory-extraction prompt must instruct rewriting into self-contained facts, never quotes (§8.1), and must assign a short `topic` label.
- The reminder prompt receives current local datetime + timezone as context so relative times resolve (§11).
- `tests/eval/` — 10 capture examples, 10 recall queries with expected memory content, 5 reminder phrasings with expected resolved datetimes.

**Technical requirements**
- Prompts are module-level constants or pure functions returning strings. No I/O, no Bedrock imports.
- Keep prompts terse and non-prescriptive — current models follow instructions literally, and over-scripted prompts degrade output quality.
- No "CRITICAL / YOU MUST" pressure language.

**Acceptance criteria**
- Every prompt is importable and renderable with no side effects.
- Eval set is committed as JSON/YAML and loadable by tests.

**Dependencies** — #1
**Parallelization** — fully parallel; can be written against schemas before #3 exists
**Order** — 2

---

## #5 — Audio capture and transcription 🟡

**Objective**
Turn a spoken thought into a transcript, locally.

**Context**
`plan.md` §10. Fully independent of the AI/memory layers — the biggest parallelization win in the build.

**Scope**
- `audio/record.py` — `record_until_enter() -> Path`. `sounddevice` capture to a temp WAV under `$HEYBRAIN_HOME/tmp/`, stops on Enter, prints a "🎙 Listening… [Enter to stop]" indicator.
- `transcription/whisper.py` — `transcribe(path: Path) -> str` using `faster-whisper`, model from config (default `base.en`), model cached under `$HEYBRAIN_HOME/models/`.
- Temp WAV deleted in a `finally` block, unconditionally.
- Empty/whitespace transcript → raise `TranscriptionError` with a user-facing message.
- Mic permission failure on macOS → error naming System Settings → Privacy → Microphone.
- `warm_model()` entrypoint so #6 can pre-warm.

**Technical requirements**
- Audio capture is a macOS adapter but keeps a platform-neutral signature.
- No audio is ever written outside `tmp/` and none survives the call.
- 16kHz mono WAV — what Whisper wants, avoids a resample.

**Acceptance criteria**
- Speak a 20s clip → transcript in under 3s on the demo machine.
- `ls $HEYBRAIN_HOME/tmp/` is empty after every run, including after an exception.
- Silence produces `TranscriptionError`, not an empty string passed downstream.

**Dependencies** — #1
**Parallelization** — fully parallel, no Bedrock, no DB
**Order** — 2

---

## #6 — `brain doctor` 🔴

**Objective**
One command that proves the environment works before the demo.

**Context**
`plan.md` §6.2. Model availability by region is the most likely blocker; this catches it in Phase 0 instead of on stage.

**Scope**
Check and print pass/fail for: `HEYBRAIN_HOME` writable; AWS credentials resolvable; region set; chat model reachable (1-token call); fast model reachable; embedding model reachable (embed `"test"`); SQLite openable; microphone available; Whisper model present (download with progress if not, then pre-warm).

**Technical requirements**
- Never raises — every check is caught and rendered as a red line with remediation text.
- Exit code 1 if any check fails.
- Names the exact model ID and region on model failure, and points at the Bedrock console for enabling access.

**Acceptance criteria**
- On a clean machine with no AWS config, prints actionable failures and exits 1.
- On a working machine, all green in under 20s (excluding first Whisper download).

**Dependencies** — #1, #3, #5 (mic/whisper checks can land later)
**Parallelization** — small; do it right after #3 lands
**Order** — 3

---

## #7 — `brain think`: conversation flow 🔴 *(integration)*

**Objective**
The core capture loop, end to end, text-first.

**Context**
`plan.md` §5, §9. First integration point — needs #1, #2, #3, #4 all working.

**Scope**
- `AppService.think(text: str | None, voice: bool)` — create conversation, loop turns, call Bedrock with assembled context, persist messages, produce title/summary/topic via `ConversationAnalysis`, close the conversation on exit.
- `cli/think.py` — arg handling, interactive loop, Ctrl-C handling.
- Context assembly per §8.3 layers 1–3 (memory retrieval comes in #10).
- Intent handling per §9: capture-only intents print the reply immediately; question intents block on retrieval (stubbed until #10).
- `brain list` and `brain show <id>`.
- Wire voice from #5 behind `--voice` once available.

**Technical requirements**
- Ctrl-C mid-turn saves the conversation, skips extraction, exits 0.
- Never send full history — last 6 messages plus summary only.
- The CLI contains no Bedrock calls, no SQL, no prompt text.

**Acceptance criteria**
- Capture a thought, converse 3 turns, get a summary. Kill the process. `brain list` shows it; `brain show <id>` renders it in full.
- Latency: transcript → first visible reply under 4s.

**Dependencies** — #1, #2, #3, #4 (and #5 for the voice path)
**Parallelization** — integration issue; single owner
**Order** — 4

---

## #8 — Chroma vector store wrapper 🟡

**Objective**
Persist and search memory embeddings.

**Context**
`plan.md` §7, §8.4.

**Scope**
- `memory/vectors.py` — `VectorStore` wrapping Chroma `PersistentClient` at `$HEYBRAIN_HOME/chroma/`, single collection `memories`.
- `upsert(memory_id, embedding, metadata)`, `search(embedding, k, filters) -> [(memory_id, distance)]`, `delete(memory_id)`, `rebuild(memories, embeddings)`.
- Metadata stored: `memory_type`, `topic`, `importance`, `status`, `created_at`, `conversation_id`.
- `brain reindex` command — rebuild Chroma from SQLite.

**Technical requirements**
- Embeddings are supplied by the caller (from #3) — this module never calls Bedrock.
- Chroma's built-in embedding function must be **disabled**; we pass vectors explicitly.
- `search` filters on `status = "active"` by default.
- Chroma is treated as disposable; SQLite is authoritative.

**Acceptance criteria**
- Upsert 20 vectors, search, get sensible neighbours back.
- Delete the `chroma/` directory, run `brain reindex`, search results are equivalent.
- Filtering by `status` and `topic` works.

**Dependencies** — #1, #2 (for reindex)
**Parallelization** — parallel with #7; can be built against random vectors before #3 lands
**Order** — 3

---

## #9 — Memory write path: extraction, scoring, deduplication 🔴

**Objective**
Turn conversations into a curated memory store that doesn't degenerate into near-duplicates.

**Context**
`plan.md` §8.1, §8.2. **The hardest and highest-risk issue in the build.** Without dedup the demo dies on stage.

**Scope**
- `memory/extractor.py` — run `memory_extraction_prompt` over a conversation, return `MemoryCandidate[]`.
- `memory/service.py` write path:
  1. Filter candidates to `importance >= 0.6` (configurable).
  2. Embed each surviving candidate.
  3. Search Chroma for the nearest existing memory.
  4. If cosine similarity ≥ 0.90, call `dedupe_verdict_prompt` with both memories → `merge | supersede | separate`.
  5. Apply: merge updates the existing row in place; supersede marks the old row `superseded` and inserts the new; separate inserts.
  6. Keep SQLite and Chroma consistent on every branch.
- `AppService.remember(text)` — bypasses the threshold, stores at importance 1.0, still runs dedup.
- Background execution for capture intents per §9, with the CLI joining the thread before exit and showing a `saving…` spinner.
- `brain reprocess <conversation_id>` — re-run extraction on an existing conversation (escape hatch for interrupted background work).

**Technical requirements**
- Similarity threshold and importance threshold are config constants, not magic numbers inline.
- A dedup LLM failure degrades to `separate` — never lose a memory to an error.
- Extraction failure never fails the conversation; the conversation is already saved by #7.
- Chroma and SQLite writes are ordered so a crash leaves an orphan vector (harmless, cleaned by reindex) rather than an unindexed memory.

**Acceptance criteria**
- Say "I want to learn Kafka for system design prep", then in a separate session "I should probably study Kafka before interviews" → **one** memory exists, not two.
- Say two genuinely different things → two memories.
- Low-importance chatter ("I'm tired today") produces no memory.
- `brain remember "I prefer backend over frontend"` stores a `preference` memory.

**Dependencies** — #2, #3, #4, #7, #8 (needs the `think` background-extraction hook from #7, not just Chroma)
**Parallelization** — single owner; the dedup logic is subtle
**Order** — 5

---

## #10 — Memory read path: retrieval and ranking 🔴

**Objective**
Given a query, return the right memories.

**Context**
`plan.md` §8.4.

**Scope**
- `memory/retriever.py` — `retrieve(query, k=5) -> list[Memory]`:
  1. Embed the query.
  2. Chroma top-K=8 with `status = "active"` filter.
  3. Rerank by `similarity × importance × recency_decay`.
  4. Take top 5, fetch full rows from SQLite preserving rank order.
- `retrieve_by_topic(topic)` for the resume flow.
- Hook layer 4 of §8.3 context assembly into #7's question-intent path.

**Technical requirements**
- Recency decay is a documented formula, not a magic constant.
- Retrieval must be a single Chroma call plus a single SQLite call — no N+1.
- Returns `[]` cleanly when the store is empty (fresh install must not crash).

**Acceptance criteria**
- Against the #4 eval set, ≥ 8/10 recall queries return the expected memory in the top 3.
- Queries whose wording does not overlap the original input still hit (the whole point of semantic search).
- Empty store returns `[]`.

**Dependencies** — #3, #8, #9 (needs real memories to rank)
**Parallelization** — can be written in parallel with #9 against hand-seeded memories
**Order** — 5

---

## #11 — `brain recall` and `brain remember` 🔴

**Objective**
Expose the memory engine as the demo's proof of value.

**Context**
`plan.md` §5, §8.4.

**Scope**
- `AppService.recall(query)` — retrieve, then run `recall_synthesis_prompt` to produce an answer; return the answer *and* the source memories.
- `cli/recall.py` — render the synthesized answer prominently with numbered source memories beneath (type badge, topic, date).
- `cli/remember.py` — thin wrapper on `AppService.remember`, echoes the stored memory type and content for confirmation.

**Technical requirements**
- **Never** dump raw vector-search results. Synthesis is mandatory.
- No results → an honest "I don't have anything on that yet", not a hallucinated answer.
- The synthesis prompt must be instructed to answer only from the supplied memories.

**Acceptance criteria**
- `brain recall "what were my ideas about AI coding agents?"` returns a synthesized paragraph plus 2–3 correctly-attributed source memories.
- Querying a topic never discussed returns the honest empty response.

**Dependencies** — #9, #10
**Parallelization** — parallel with #12
**Order** — 6

---

## #12 — `brain resume` 🔴

**Objective**
The defining feature: stop a topic today, continue it days later.

**Context**
`plan.md` §5, §7 (session boundaries), Flow C.

**Scope**
- `AppService.list_recent_topics()` — distinct topics from recent conversations and memories, ordered by recency, with a last-touched timestamp.
- `AppService.resume(topic)` — gather the topic's conversation summaries, relevant memories, and open tasks; run `continuation_prompt` to reconstruct context; **open a new conversation** linked by topic; hand off to the `think` loop.
- `cli/resume.py` — numbered topic picker when no topic is supplied; fuzzy match when one is.

**Technical requirements**
- Resume opens a *new* conversation. It never reopens a closed one (§7).
- Reconstruction output states what was previously discussed and ends with a forward-looking question.
- Topic matching is fuzzy string + semantic; there is no topics table.

**Acceptance criteria**
- Have a conversation, exit, start a fresh process, `brain resume` → topic appears in the list → selecting it reconstructs accurate context and the conversation continues meaningfully.
- Reconstruction reflects what was actually said, with no invented detail.

**Dependencies** — #9, #10, #7
**Parallelization** — parallel with #11
**Order** — 6

---

## #13 — Reminders 🟢

**Objective**
Turn spoken thoughts into real macOS notifications.

**Context**
`plan.md` §11. Stretch — cut before #14 if time runs short.

**Scope**
- Reminder-intent detection in the conversation flow → `reminder_extraction_prompt` with current local datetime + timezone → `ReminderCandidate`.
- Echo the resolved absolute datetime for confirmation before saving.
- Persist `tasks` + `reminders` rows.
- `reminders/notify.py` — `osascript -e 'display notification ...'` adapter.
- `brain reminders tick` — fire due reminders, mark `fired`.
- `brain reminders list`.
- `scripts/install_launchd.sh` — install a LaunchAgent running `tick` every 60s.

**Technical requirements**
- All datetimes stored timezone-aware ISO 8601.
- Past time → reject and re-ask; never silently fire.
- Overdue by < 24h on the next tick → fire with an "(overdue)" prefix. Older → mark `missed`.
- `recurrence` is parsed and stored but only `none` is honored.

**Acceptance criteria**
- "Remind me tomorrow at 7pm to continue the coding agent" → correct absolute time echoed → row stored → notification fires at the scheduled minute.
- Laptop asleep through the scheduled time → fires on wake with the overdue prefix.

**Dependencies** — #2, #3, #4, #7
**Parallelization** — largely independent once #7 lands
**Order** — 7

---

## #14 — Terminal presentation and demo script 🟢 *(integration)*

**Objective**
Make what the judges actually see look deliberate, and de-risk the live run.

**Context**
`plan.md` §13 Phase 6, §16. If #13 slips, this is worth more.

**Scope**
- `cli/render.py` — `rich` components: memory cards with colored type badges, spinners for every blocking call, a clean `recall` layout, conversation transcript formatting, consistent error rendering.
- Replace all ad-hoc `print` calls across the CLI.
- `scripts/demo.sh` — the §1 demo path end to end against real Bedrock, runnable in one command.
- README with setup, AWS/Bedrock prerequisites, and the demo script.

**Technical requirements**
- Every operation over ~500ms shows a spinner with a meaningful label.
- Errors render as one red line with remediation — never a traceback.
- Output must be legible over a screen share: adequate contrast, no reliance on 256-color subtleties.

**Acceptance criteria**
- `scripts/demo.sh` runs the full demo path without manual intervention.
- No raw traceback is reachable from any documented user action.

**Dependencies** — #7, #11, #12 (#13 if it lands)
**Parallelization** — `render.py` can be built in parallel from #1 onward; wiring is late
**Order** — 8

---

# Dependency Graph

```
                          ┌─────────────────┐
                          │ #1 Contracts    │  (blocks everything)
                          └────────┬────────┘
          ┌──────────┬────────────┼────────────┬──────────┐
          ▼          ▼            ▼            ▼          ▼
      ┌───────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
      │ #2    │  │ #3     │  │ #4     │  │ #5     │  │ #14    │
      │Storage│  │Bedrock │  │Prompts │  │Audio + │  │render  │
      │       │  │        │  │+ evals │  │Whisper │  │ (early)│
      └───┬───┘  └───┬────┘  └───┬────┘  └───┬────┘  └────────┘
          │          │           │           │
          │      ┌───▼───┐       │           │
          │      │ #6    │       │           │
          │      │doctor │◄──────┼───────────┘
          │      └───────┘       │
          │          │           │
      ┌───▼──────────▼───────────▼───┐
      │ #8 Chroma        #7 think     │  ◄── first integration
      └───┬───────────────────┬───────┘
          │                   │
          └────────┬──────────┘
                   ▼
              ┌─────────┐
              │ #9 write│  ◄── hardest issue (dedup)
              │  path   │
              └────┬────┘
                   ▼
              ┌─────────┐
              │ #10 read│
              │  path   │
              └────┬────┘
              ┌────┴────┐
              ▼         ▼
          ┌───────┐ ┌────────┐
          │#11    │ │#12     │
          │recall │ │resume  │
          └───┬───┘ └───┬────┘
              └────┬────┘
                   ▼
            ┌────────────┐     ┌────────┐
            │#14 polish  │◄────│#13     │
            │+ demo      │     │reminders│
            └────────────┘     └────────┘
```

## Blocking issues

- **#1 blocks all 13 others.** Do it first, alone, and do not parallelize around it.
- **#3 blocks** #6, #7, #9, #10, #13 — and carries the region/model-access risk. Land it early.
- **#9 blocks** #10, #11, #12 — the largest single point of schedule risk.

## Fully parallel after #1

#4, #5, and the `render.py` half of #14 have no dependencies on each other or on #2/#3. #2 and #3 are only parallel with each other if #3 stubs `UsageRepo` per its note above — otherwise #2 → #3 is sequential (Stream A). Three to four agents or sittings can run concurrently here.

## Can start with mocks before their dependency lands

| Issue | Mock strategy |
|---|---|
| #4 Prompts | Written against #1 schemas alone; no Bedrock needed |
| #8 Chroma | Built and tested with random vectors before #3 exists |
| #10 Retrieval | Hand-seed memories + vectors; don't wait for #9 |
| #14 render.py | Renders Pydantic models from #1; wire to real data later |
| #7 think | Stub `BedrockService` behind its interface to build the CLI loop first |

## Integration issues — only after multiple components are ready

- **#7** (needs #1+#2+#3+#4) — first vertical slice
- **#11 / #12** (need #9+#10) — the memory engine made visible
- **#14** (needs everything) — final assembly

---

# Development Strategy

## Milestones

| Milestone | Issues | Demo statement |
|---|---|---|
| **M1 — It runs** | #1, #2, #3, #6 | `brain doctor` is all green |
| **M2 — It talks** | #4, #7 | Capture a thought, converse, persist, restart, retrieve |
| **M3 — It hears** | #5 | Speak instead of type |
| **M4 — It remembers** | #8, #9, #10, #11 | Recall with different wording; no duplicates |
| **M5 — It continues** | #12 | Resume a topic days later |
| **M6 — It acts / It shines** | #13, #14 | Reminders fire; the demo script runs clean |

M1–M5 are the product. M6 is upside.

## Contracts to freeze before parallel work

Agree these in #1 and treat them as immutable for the week:

1. **`AppService` method signatures** — the only surface the CLI touches.
2. **`BedrockService` three methods** (`chat`, `structured`, `embed`) — lets #7/#9/#10 be built against a stub.
3. **`bedrock/schemas.py`** — the extraction contract between prompts (#4), Bedrock (#3), and memory (#9).
4. **`VectorStore` interface** — lets #10 be built before #8 is finished.
5. **Repository method names** — #7 and #9 both depend on these.
6. **`core/errors.py` hierarchy** — the CLI's error rendering (#14) depends on the taxonomy, not on individual raise sites.

## Suggested parallel workstreams

| Stream | Issues | Character of the work |
|---|---|---|
| **A — Backbone** | #1 → #2 → #3 → #6 | AWS-facing, highest risk, start here |
| **B — Voice** | #5 | Fully isolated; no AWS, no DB |
| **C — Prompts & evals** | #4 | Text and judgment; no infrastructure |
| **D — Vectors** | #8 → #10 | Testable with synthetic vectors |
| **E — Presentation** | #14 (render.py) | Pure formatting against #1 models |

Streams B, C, D, E can all run the moment #1 lands. Stream A must be sequential and is the pacing item.

## Critical path to a usable MVP

```
#1 → #3 → #7 → #9 → #10 → #11/#12
```

Six issues. Everything else is parallel or optional. If the week compresses:

- Cut **#13 (reminders)** first — it is the least connected to the core hypothesis.
- Cut **#5 (voice)** second — `brain think "text"` demos the same loop.
- **Never cut #9's deduplication.** A memory store full of near-duplicates is a worse demo than no memory store, and it fails visibly in front of judges.

## Risk register

| Risk | Mitigation |
|---|---|
| Bedrock model not enabled in region | #6 `doctor` on day one; verify in console before writing #3 |
| Dedup tuning eats a day | Threshold is config; ship the LLM verdict path early and tune against the #4 eval set |
| Whisper first-run download stalls the demo | `doctor` pre-warms the model |
| Background extraction thread lost on exit | `brain reprocess <id>` escape hatch in #9 |
| Retrieval quality disappoints | Eval set exists from #4, so quality is measurable mid-build, not discovered at demo time |
