# heyBrain — Personal Second-Brain Assistant

**Status:** Authoritative plan. Supersedes all earlier drafts.
**Context:** Hackathon build, ~1 week. Optimize for a working, demoable end-to-end product — not for a maintainable long-lived product.
**Last updated:** 2026-08-13 (§5/§10 voice recording UX revised post-launch, issue #5)

---

## 0. Locked Decisions

These were ambiguous or contradictory in earlier drafts. They are now settled. Do not re-litigate them mid-build.

| # | Question | Decision |
|---|---|---|
| 1 | Who is this for? | Hackathon demo. Single user (the developer). No accounts, no auth, no multi-tenancy, no installer polish. |
| 2 | Timebox | Under 1 week. Anything not on the critical path is a stretch goal. |
| 3 | Build order | Phase by phase, CLI first. Do not start a phase before the prior one demonstrably works. |
| 4 | Process architecture | **CLI-only, no daemon.** All logic lives in an importable core package; the Typer CLI calls it in-process. No FastAPI in the MVP. |
| 5 | Command surface | **One binary, `brain`, with subcommands.** No bare `think`/`recall`/`continue` binaries (`continue` collides with the shell builtin). |
| 6 | Model provider | **Amazon Bedrock only.** No OpenAI/Anthropic-direct/Gemini providers. No `LLMProvider` protocol. The old "Phase 7 — Multi-LLM Abstraction" is **deleted**. |
| 7 | Speech-to-text | **Local `faster-whisper`.** No cloud STT, no extra API keys. (Bedrock has no STT.) |
| 8 | Vector store | **ChromaDB, embedded `PersistentClient`.** Embeddings come from Bedrock. |
| 9 | Memory extraction timing | **Intent-dependent.** Capture-only intents reply immediately and extract in the background before exit. Question/recall intents do the work synchronously because the answer depends on it. |
| 10 | Platform | **macOS-first.** Portability is *not* an MVP constraint. Platform-specific code (audio capture, notifications) sits behind thin adapter modules so a port is possible later, but no Windows/Linux implementation is written. |
| 11 | `users` table | **Dropped.** Single-user local app. |
| 12 | Repo layout | One layout, §4. The three conflicting trees in earlier drafts are gone. |
| 13 | Persistence | SQLite (single file) for relational data + Chroma (directory) for vectors. Both under `~/.heybrain/`. |
| 14 | Reminders | SQLite rows + a `launchd` agent that runs `brain reminders tick` every minute and fires `osascript` notifications. No resident Python process. |

---

## 1. Product Vision

A laptop-first personal thinking and memory assistant, invoked from the terminal.

> **Think out loud → AI understands → system remembers → you continue later.**

The one hypothesis to validate:

> **Can a user dump thoughts quickly and reliably retrieve/continue them later?**

Everything else — CLI polish, voice, reminders, vector search, Bedrock — is implementation detail supporting that loop.

### The core product loop

```
CAPTURE → UNDERSTAND → REMEMBER → RETRIEVE → CONTINUE → ACT → CAPTURE
```

### Demo definition of success

A judge watches this in under 3 minutes:

1. `brain think` → speak a thought → get a useful reply + summary.
2. Two more `brain think` sessions on different topics.
3. `brain recall "what was I thinking about coding agents?"` → returns the right memories, synthesized, using *different wording* than the original input.
4. `brain resume` → picks a topic, reconstructs context, continues the conversation.
5. "Remind me tomorrow at 7pm to continue this" → reminder created and visible.
6. Kill the process, restart, everything is still there.

---

## 2. Principles

1. **Zero-friction capture.** The user never picks a notebook, folder, tag, or type. The system decides.
2. **Natural language first.** Subcommands are an entry point, not a syntax. Inside a session everything is prose.
3. **Memory is curated, not a log.** Conversation history ≠ long-term memory. Only high-value, rewritten facts become memories.
4. **Capture must feel instant.** Never make the user wait for work that doesn't change what they see next.
5. **Bedrock is the only model gateway.** All inference goes through one `BedrockService`.

---

## 3. Architecture

```
                        brain (Typer CLI)
                              │
                    ┌─────────┴─────────┐
                    │   AppService      │   ← orchestration, the only layer
                    └─────────┬─────────┘     the CLI talks to
        ┌──────────┬──────────┼──────────┬──────────┐
        │          │          │          │          │
  Transcription  Bedrock   Memory    Reminders   Storage
   (whisper)    Service    Service    Service   (SQLite)
                    │          │
                    │      Retriever ── Chroma (vectors)
                    │
              Amazon Bedrock
```

**Rules:**
- The CLI contains **no** Bedrock calls, no SQL, no prompt text. It parses args, renders output, and calls `AppService`.
- All Bedrock specifics (auth, model IDs, prompts, schemas, retries, usage logging, error translation) live in `bedrock/`. The rest of the app never sees a `botocore` exception.
- `MemoryService` owns both SQLite memory rows and Chroma vectors and keeps them consistent.
- Platform-specific code (`audio/`, `notify/`) is isolated behind a function-level interface.

---

## 4. Repository Layout

One layout. This is it.

```
heybrain/
├── src/heybrain/
│   ├── cli/
│   │   ├── main.py            # Typer app, subcommand registration
│   │   ├── think.py
│   │   ├── recall.py
│   │   ├── remember.py
│   │   ├── resume.py
│   │   └── render.py          # rich formatting helpers
│   ├── core/
│   │   ├── config.py          # pydantic-settings, reads .env
│   │   ├── models.py          # domain Pydantic models
│   │   └── service.py         # AppService — orchestration
│   ├── bedrock/
│   │   ├── client.py          # BedrockService (chat + embeddings)
│   │   ├── prompts.py         # all prompt text, version-controlled
│   │   ├── schemas.py         # structured-output Pydantic schemas
│   │   └── usage.py           # token/latency/cost logging
│   ├── memory/
│   │   ├── service.py         # write path: extract → dedupe → store
│   │   ├── extractor.py
│   │   ├── retriever.py       # read path: embed → search → filter
│   │   └── vectors.py         # Chroma wrapper
│   ├── transcription/
│   │   └── whisper.py         # faster-whisper
│   ├── audio/
│   │   └── record.py          # sounddevice capture (macOS adapter)
│   ├── reminders/
│   │   ├── service.py
│   │   └── notify.py          # osascript adapter
│   └── storage/
│       ├── db.py              # SQLite engine/session
│       ├── schema.sql         # plain SQL DDL, applied on startup
│       └── repositories.py
├── tests/
├── scripts/
│   └── install_launchd.sh
├── .env.example
├── pyproject.toml
├── Makefile
└── README.md
```

**No migrations framework.** `schema.sql` is applied idempotently at startup. If the schema changes during the hackathon, delete `~/.heybrain/brain.db` and start over.

---

## 5. Command Surface

```
brain think [text...]        # capture + converse. No args → prompt or record.
brain remember <text>        # force a long-term memory, no conversation
brain recall <query>         # semantic search + LLM synthesis
brain resume [topic]         # list recent topics, reconstruct, continue
brain list                   # recent conversations
brain show <id>              # one conversation in full
brain reminders list         # pending reminders
brain reminders tick         # internal: fire due reminders (called by launchd)
brain doctor                 # verify AWS creds, Bedrock access, mic, models
```

**Voice vs text within `think`:**
- `brain think "some text"` → text, no mic.
- `brain think` with no args → `--voice/--text` flag decides; default is **voice** once Phase 2 lands, `--text` falls back to a prompt.
- Recording is a **press-Enter-to-start / press-Enter-to-stop toggle** (not hold-to-talk, and not auto-start-on-invoke). Revised post-launch (issue #5) after using the app: auto-start-the-instant-you-call-it left no beat to get ready before capture began. Hold-to-talk was considered and rejected as too complex -- it needs `pynput` plus macOS Accessibility permissions just to detect a held key from the terminal. The toggle needs nothing extra and still works over screen share.
- The assistant's replies are **text only**. No TTS in the MVP.

Suggested user alias (documented in the README, not installed by us): `alias think='brain think'`.

---

## 6. Amazon Bedrock

### 6.1 Configuration

```
AWS_REGION=us-east-1
AWS_PROFILE=              # optional — see below
BEDROCK_MODEL_ID=anthropic.claude-opus-5
BEDROCK_FAST_MODEL_ID=anthropic.claude-haiku-4-5
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
HEYBRAIN_HOME=~/.heybrain
```

`AWS_PROFILE` is optional and unset by default. Credentials come from the AWS SDK's default credential chain — a named profile if `AWS_PROFILE` is set, otherwise environment variables, an IAM role, or SSO, in that order. Passing a literal `"default"` profile name forces boto3 to require a profile named `default` in `~/.aws/config`/`credentials`, which breaks env-var-only setups (e.g. `AWS_BEARER_TOKEN_BEDROCK`/`AWS_ACCESS_KEY_ID` exported directly) — leave it unset unless you actually use a named profile. **No access keys in the repo.** Model IDs are configuration; changing a model must never require a code change.

### 6.2 Client choice

- **Chat / structured extraction:** the `langchain-aws` package's Bedrock **Converse API** client (`from langchain_aws import ChatBedrockConverse`; `ChatBedrockConverse(model_id=..., region_name=...)`) — not the legacy `ChatBedrock` (`invoke_model`) wrapper, which has no `reasoning_effort`/`output_config` fields and can't satisfy §6.3/§6.4. Bedrock model IDs carry an `anthropic.` prefix. This gives us structured outputs and tool use without hand-rolling request bodies.
- **Embeddings:** `langchain-aws`'s `BedrockEmbeddings` (itself backed by `boto3` `bedrock-runtime` `invoke_model`) against the Titan embedding model.

Both are wrapped inside `BedrockService`; the rest of the app sees only our own methods.

**Day 0 task:** run `brain doctor` equivalent by hand — confirm both models are enabled in the target region in the Bedrock console, and confirm which model IDs actually resolve. Model availability by region is the single most likely day-one blocker.

### 6.3 Request rules (current Claude API surface)

These are not optional — violating them returns HTTP 400:

- **Do not send `temperature`, `top_p`, or `top_k`.** They are removed on current Opus/Sonnet-tier models. Steer behavior with prompts.
- **Do not send `budget_tokens`.** Use `output_config.effort` (`low` | `medium` | `high` | `xhigh` | `max`) instead.
- **Do not use assistant-turn prefills** to force JSON. Use structured outputs.
- On Opus 5, thinking is **on by default**. `max_tokens` caps thinking *plus* response text — size it with headroom or responses truncate.
- Disabling thinking is only legal at `effort: high` or below.

### 6.4 Effort routing

| Operation | Model | Effort |
|---|---|---|
| Intent classification | fast model | `low` |
| Conversation turn | primary | `medium` |
| Memory extraction | primary | `low` |
| Recall synthesis | primary | `medium` |
| Topic reconstruction (`resume`) | primary | `medium` |
| Reminder extraction | fast model | `low` |

Start every route at these values. Only raise effort if quality demonstrably fails.

### 6.5 Structured output

Every extraction path uses a Pydantic schema via structured outputs — never free-form parsing.

```python
class MemoryCandidate(BaseModel):
    content: str                      # rewritten, self-contained fact
    memory_type: Literal["idea","goal","preference","fact","decision","plan"]
    importance: float                 # 0.0–1.0
    topic: str                        # short label

class ConversationAnalysis(BaseModel):
    title: str
    summary: str
    topic: str
    memory_candidates: list[MemoryCandidate]
    tasks: list[TaskCandidate]
```

Validation pipeline: `Bedrock → parse → Pydantic validate → business rules → SQLite`.

### 6.6 Reliability

- Bounded retries with exponential backoff + jitter for throttling (`ThrottlingException`), 5xx, and timeouts. Max 3 attempts.
- Structured-output validation failure → **one** repair retry, then a safe fallback (skip extraction, keep the conversation; never crash the user's session).
- Every `botocore`/`anthropic` exception is translated into a `BedrockError` before leaving `bedrock/`. The CLI prints a human sentence, never a stack trace.
- Timeouts: 30s for chat, 10s for embeddings.

### 6.7 Usage logging

Log per call to a `usage` table: `request_id, operation, model_id, input_tokens, output_tokens, latency_ms, created_at`. Never log raw conversation content in this table.

---

## 7. Data Model

```sql
conversations(id, title, summary, topic, status, created_at, updated_at)
messages(id, conversation_id, role, content, created_at)
memories(id, conversation_id, memory_type, content, topic, importance,
         status, created_at, updated_at)
tasks(id, conversation_id, title, description, status, created_at, completed_at)
reminders(id, task_id, scheduled_at, status, fired_at, created_at)
usage(id, request_id, operation, model_id, input_tokens, output_tokens,
      latency_ms, created_at)
```

**No `users` table. No `topics` table.**

- `topic` is a **string label on the row**, assigned by the LLM during extraction. Topic "identity" is fuzzy string + semantic match at read time, not a foreign key. This is deliberate — a topics table with proper clustering is post-MVP.
- `memories.status` ∈ `active | archived | superseded`.
- `conversations.status` ∈ `open | closed`. A conversation is **closed when the CLI process exits**. `brain resume` opens a *new* conversation linked by shared topic — it does not reopen the old one.
- Vectors live in Chroma keyed by `memories.id`. SQLite is the source of truth; Chroma is a rebuildable index (`brain reindex` regenerates it).

---

## 8. Memory Model

### 8.1 What becomes a memory

Not every message. The extractor produces candidates; only `importance >= 0.6` is stored automatically. `brain remember` bypasses the threshold and stores at importance 1.0.

Memories are **rewritten into self-contained facts**, never quoted:

- Bad: `User said "Kafka is interesting."`
- Good: `User wants to learn Kafka as part of system design interview prep.`

### 8.2 Deduplication (required, not optional)

Before inserting any memory:

1. Embed the candidate.
2. Search Chroma for the nearest existing memory.
3. If cosine similarity ≥ **0.90** → send both to the LLM: *merge, supersede, or keep separate?* Apply the verdict (update in place, or mark the old one `superseded`).
4. Otherwise insert.

Without this the store degenerates into `User is learning Kafka / User wants to learn Kafka / User plans to study Kafka / ...` and the demo dies. This ships in Phase 3, not later.

### 8.3 Context assembly

Never send full history. For each turn, build context from:

1. Current message (always)
2. Last N=6 messages of the current conversation (always)
3. Current conversation summary (if one exists)
4. Top-K=5 relevant long-term memories (only when the turn is a question, a recall, or a resume)

Layers 3 and 4 are conditional. A pure capture turn does not need a vector search.

### 8.4 Retrieval

```
query → embed → Chroma top-K=8 → metadata filter (status=active)
      → rerank by importance × recency → top 5 → LLM synthesis
```

`recall` **never** dumps raw search results. The LLM always synthesizes an answer with the source memories listed underneath.

---

## 9. Latency Budget

Capture must feel instant. Targets on the demo machine:

| Step | Target |
|---|---|
| Recording stop → transcript | < 3s for a 20s clip |
| Transcript → first visible reply | < 4s |
| Total `think` turn (voice) | < 7s |
| `recall` end-to-end | < 6s |
| `resume` context reconstruction | < 8s |

**Enforcement mechanism (decision #9):**

- The first Bedrock call classifies intent *and* produces the reply in one request — no separate classification round-trip on the conversational path.
- **Capture-only intent** (user is dumping a thought): print the reply immediately, then run extraction + embedding + dedupe on a background thread. The CLI joins that thread before exit and shows a `saving…` spinner if it hasn't finished.
- **Question / recall / resume intent:** retrieval must happen *before* the reply, so it is synchronous. Show a spinner.
- **Reminder intent:** extract the reminder synchronously (the user needs confirmation the time was parsed correctly) and echo the resolved absolute datetime back for confirmation.

If the process is killed mid-background-extraction, the conversation is still saved; the memories are lost. Acceptable for a hackathon; `brain reprocess <conversation_id>` is the escape hatch.

Whisper model: `base.en` by default (fast, good enough). `small.en` configurable if accuracy on technical terms is poor.

---

## 10. Voice

```
[Enter] start → [Enter] stop → temp WAV → faster-whisper → transcript → pipeline
                                                        └→ delete WAV immediately
```

- Recording is a **toggle** (revised post-launch, issue #5; see §5): the CLI prints "Press Enter to start recording" and blocks on `input()` with the mic *not yet open*; only once the user presses Enter does it open the stream, print the recording-in-progress prompt, and start capturing; a second Enter stops it. This replaced an earlier version that started capturing the instant the function was called.
- Mic capture via `sounddevice` into a temp WAV under `HEYBRAIN_HOME/tmp/`.
- The temp file is deleted in a `finally` block, always. **Raw audio is never persisted.**
- Empty or whitespace-only transcript → tell the user, offer to retype, do not call Bedrock.
- Mic permission failure on macOS → clear message pointing at System Settings, not a traceback.
- **One mic stream per voice session, not per turn.** `brain think --voice` opens a single `sd.InputStream` lazily on the first turn and keeps it open for every subsequent turn in that session; each turn just toggles whether the callback keeps or drops incoming frames. This exists because opening/closing an `InputStream` repeatedly within one process is a known class of PortAudio/CoreAudio deadlock on macOS, and a user hit exactly that: recording hung indefinitely (no traceback, no CPU activity) on the 3rd consecutive voice turn under the old open-per-turn design.
- Both the stream open and the stream close are wrapped in a watchdog with a bounded wait (10s open / 5s close) so a stuck native call surfaces as a `TranscriptionError` instead of hanging the CLI forever. This is a mitigation, not a root-cause fix -- it wasn't reproduced locally -- so a hang is still possible in principle up to the timeout, but it can no longer hang *silently forever*.
- First run downloads the Whisper model (~150MB for `base.en`). `brain doctor` pre-warms it so the demo never stalls on a download.

---

## 11. Reminders

Natural-language extraction produces:

```json
{
  "title": "Continue working on the coding agent",
  "scheduled_at": "2026-08-14T19:00:00+05:30",
  "recurrence": null
}
```

- The prompt is given the **current local datetime and timezone** so "tomorrow at 7pm" resolves correctly. Always store timezone-aware ISO 8601.
- The resolved absolute time is **echoed to the user for confirmation** before saving.
- A time in the past → reject and ask again. Never silently fire.
- Delivery: `launchd` runs `brain reminders tick` every 60s. It selects reminders where `scheduled_at <= now AND status = 'pending'`, fires `osascript -e 'display notification ...'`, marks them `fired`.
- **Missed reminders** (laptop asleep/off): on the next tick, anything overdue by < 24h fires immediately with an "(overdue)" prefix; older than 24h is marked `missed` and shown in `brain reminders list`.
- Recurrence is parsed and stored but **only `none` is honored in the MVP**. Recurring reminders are a stretch goal.

---

## 12. Privacy & Security

Stated plainly so it can be answered in the demo Q&A:

- All user data stays local: `~/.heybrain/brain.db` and `~/.heybrain/chroma/`. Nothing is uploaded anywhere except Bedrock inference payloads.
- **Thought content is sent to Amazon Bedrock.** That is inherent to the design and should be said out loud, not hidden.
- SQLite is **not encrypted at rest**. Documented, not fixed, for the hackathon.
- The `usage` table logs metadata only — no conversation content.
- No credentials in the repo; `.env` is gitignored, `.env.example` is committed.
- Raw audio is deleted immediately after transcription.

---

## 13. Build Order

Each phase must demonstrably work before the next begins. Phases 0–4 are the critical path; 5–6 are stretch.

### Phase 0 — Skeleton (target: 2 hours)
Project scaffold, config, SQLite schema, `brain --help`, `brain doctor` verifying AWS creds + Bedrock model access. Tests running.
**Exit:** `brain doctor` prints green for region, credentials, chat model, embedding model.

### Phase 1 — Text `think` (target: half a day)
Conversation loop, Bedrock chat via `BedrockService`, structured `ConversationAnalysis`, persistence, `brain list` / `brain show`.
**Exit:** capture a thought, converse, get a summary, restart the process, retrieve the conversation.

### Phase 2 — Voice (target: half a day)
`sounddevice` capture, faster-whisper transcription, wired into `think`.
**Exit:** `brain think` → speak → Enter → the same pipeline runs, no typing.

### Phase 3 — Memory engine (target: 1 day — the hardest phase)
Extraction with importance scoring, Bedrock embeddings, Chroma persistence, **deduplication**, retrieval with metadata filtering.
**Exit:** query with wording that does not overlap the original input, and the right memories come back. Say the same thing twice — one memory, not two.

### Phase 4 — `recall`, `remember`, `resume` (target: half a day)
Expose the memory engine. LLM synthesis for `recall`. Topic listing + context reconstruction for `resume`.
**Exit:** stop a topic, start a fresh process, `brain resume`, and continue meaningfully.

### Phase 5 — Reminders (stretch)
Extraction, confirmation, storage, `launchd` tick, `osascript` notification.
**Exit:** a spoken reminder fires a real macOS notification.

### Phase 6 — Presentation polish (stretch)
Rich terminal output — panels for memories, spinners, colored memory-type badges, a clean `recall` layout. This is what judges actually see; budget real time for it if Phase 5 slips.

**Do not invert this order.** Memory and continuity are the product. A menu-bar app with no memory demos nothing.

---

## 14. Explicitly Out of Scope

Not in this build, no exceptions:

- iOS/Android apps, web dashboard
- Menu bar app, global shortcut, overlay UI
- FastAPI service / any daemon
- Any model provider other than Bedrock
- Multi-user, accounts, auth, sync, cloud storage
- Knowledge graph, calendar/email/Slack/GitHub/browser integrations
- Local LLM hosting, multi-agent orchestration
- Encryption at rest, migrations framework, Windows/Linux support
- TTS output
- Automatic ingestion of files on the laptop

---

## 15. Failure Modes to Handle

Each must produce a clear one-line message, never a traceback:

| Failure | Behavior |
|---|---|
| No AWS credentials | Point at `aws configure` / `AWS_PROFILE`; exit 1 |
| Model not enabled in region | Name the model and the region; link the Bedrock console |
| Bedrock throttled | Retry with backoff; then "Bedrock is busy, try again" |
| Bedrock timeout | Same; conversation so far is still saved |
| Malformed structured output | One repair retry, then skip extraction and keep the conversation |
| Empty transcription | "I didn't catch that" → offer retype |
| Mic unavailable / permission denied | Name the permission; fall back to text input |
| Whisper model missing | Download with a progress bar; `brain doctor` pre-warms |
| SQLite locked / corrupt | Clear message + path to the DB file |
| Duplicate memory | Merge or supersede (§8.2) — this is normal operation, not an error |
| Reminder time in the past | Reject, re-ask, never silently fire |
| Ctrl-C mid-turn | Save the conversation, skip extraction, exit cleanly |

---

## 16. Testing

Hackathon-appropriate, not exhaustive:

- **Unit, no network:** conversation creation, message persistence, structured-response parsing (from recorded fixtures), importance thresholding, dedupe decision logic, reminder datetime resolution.
- **Bedrock is mocked everywhere in tests.** Record 3–4 real responses to JSON fixtures once; replay them. No test hits AWS.
- **One manual smoke script** (`scripts/demo.sh`) that runs the full demo path end-to-end against real Bedrock. Run it before the demo. This is the real safety net.
- **A small eval set built during Phase 3, not at the end:** 10 capture examples, 10 recall queries with expected memory IDs, 5 reminder phrasings. Used to tune prompts while writing them, and to prove retrieval quality in the demo writeup.

---

## 17. Success Metrics

For a hackathon these are demo-facing, not analytics:

- Retrieval hit rate on the eval set: **≥ 8/10** recall queries return the expected memory in the top 3.
- Duplicate rate: saying the same idea three different ways produces **1** memory, not 3.
- Capture latency: p50 under the §9 targets on the demo machine.
- Persistence: full restart loses nothing.
- Cost per `think` session: tracked and reportable from the `usage` table.

---

## 18. Post-MVP Direction

Only after the loop above is proven useful:

- Menu bar app + global shortcut (⌥Space) over a local HTTP layer
- Proper topics table with embedding-centroid clustering
- Memory lifecycle: candidate → validated → active → archived, with decay
- Recurring reminders
- Cross-device sync
- Digital context layer: email, calendar, GitHub, browser, files

The long-term vision remains: **an AI memory layer for your digital life.**

---

## 19. Working Agreement for Implementation

- Build the smallest vertical slice per phase; run it; commit; move on.
- Never a single giant "build my second brain" prompt.
- Prompts live in `bedrock/prompts.py`, version-controlled, never inline in orchestration code.
- Every phase ends with a manual run of the actual user flow, not just green tests.
- If a phase runs long, cut scope inside that phase — do not skip ahead to a later phase.
