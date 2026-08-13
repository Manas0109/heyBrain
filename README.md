# heyBrain

A laptop-first personal thinking and memory assistant, invoked from the terminal.

> Think out loud → AI understands → system remembers → you continue later.

heyBrain lets you dump a thought — typed or spoken — from your terminal at
any moment, have it understood and turned into durable, searchable memory,
and pick that thread back up days later without re-explaining yourself.
Everything happens in one CLI command; there's no app to open, no daemon
running in the background, and no dashboard to check.

CLI-only, no daemon: all logic lives in an importable `heybrain` package; the
`brain` command is a thin Typer wrapper around it. All inference goes through
Amazon Bedrock. See `plan.md` for the full design.

## What it does

- **Capture** a thought by typing or speaking (`brain think`) — heyBrain
  transcribes (local `faster-whisper`), understands intent, and replies
  conversationally.
- **Remember** selectively — a background extraction step curates
  high-value facts out of the conversation into long-term memory; not
  every sentence you say becomes a memory.
- **Recall** memories with natural-language queries (`brain recall`) —
  semantic vector search over everything you've captured, synthesized
  into a direct answer by the model, even when your wording differs from
  the original.
- **Resume** a past topic (`brain resume`) — reconstructs prior context
  and lets you continue the conversation where you left off.
- **Remind** yourself later — reminders persist in SQLite and fire native
  macOS notifications via a `launchd` agent, no resident process required.

## Architecture

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

**Design rules that shape the codebase:**
- The CLI (`src/heybrain/cli/`) contains no Bedrock calls, no SQL, and no
  prompt text — it only parses args, renders output, and calls
  `AppService` (`src/heybrain/core/service.py`), the single orchestration
  layer.
- Everything Bedrock-specific — auth, model IDs, prompt templates,
  response schemas, retries, usage logging, error translation — is
  isolated in `src/heybrain/bedrock/`. Nothing outside that package ever
  touches a `botocore` exception.
- `MemoryService` (`src/heybrain/memory/`) owns both the SQLite memory
  rows and the Chroma vector index and keeps them in sync. Chroma is
  disposable and can always be rebuilt from SQLite (`brain reindex`);
  SQLite is the source of truth.
- Platform-specific code — microphone capture (`src/heybrain/audio/`) and
  notification delivery (`src/heybrain/reminders/notify.py`) — sits
  behind thin, isolated adapters so the platform-specific surface stays
  small, even though only macOS is implemented today.
- Memory extraction timing is intent-dependent: a capture-only `brain
  think` replies immediately and extracts memories in the background
  before the process exits; a `brain recall`/`brain resume` does the
  extraction work synchronously because the answer depends on it.

### Persistence

Local data lives entirely under `HEYBRAIN_HOME` (`~/.heybrain` by
default) — nothing leaves your machine except the Bedrock inference
payloads themselves (the thought content you capture, sent to Amazon
Bedrock for chat, extraction, and embeddings):

- `brain.db` — SQLite: conversations, messages, memories, reminders.
- `chroma/` — ChromaDB `PersistentClient`: vector embeddings for
  semantic search over memories. Fully rebuildable from SQLite.

## Requirements

- Python 3.12+
- macOS (audio capture and notifications are macOS-specific; see `plan.md` §0).
  A Docker image is also included for the text-only CLI flows on any
  platform — see "Running in Docker" below.
- An AWS account with access to Amazon Bedrock in the target region, and the
  chat/embedding models enabled for that account (Bedrock console → Model
  access). Model availability is region-specific — confirm your models
  actually resolve in `AWS_REGION` before relying on this.

## Setup

```bash
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in your AWS region/models:

```bash
cp .env.example .env
```

```
AWS_REGION=us-east-1
AWS_PROFILE=              # optional -- see below
BEDROCK_MODEL_ID=anthropic.claude-opus-5
BEDROCK_FAST_MODEL_ID=anthropic.claude-haiku-4-5
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
HEYBRAIN_HOME=~/.heybrain
```

**Credentials:** `AWS_PROFILE` is optional and unset by default. Credentials
come from the AWS SDK's default credential chain — a named profile if
`AWS_PROFILE` is set, otherwise environment variables (e.g.
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or `AWS_BEARER_TOKEN_BEDROCK`),
an IAM role, or SSO, in that order. Leave `AWS_PROFILE` unset unless you
actually use a named profile — setting it to a literal `"default"` forces
boto3 to require a profile named `default` in `~/.aws/config`, which breaks
env-var-only setups. No AWS access keys are stored in this repo; `.env` is
gitignored.

Local data lives under `HEYBRAIN_HOME` (`~/.heybrain` by default):
`brain.db` (SQLite) and `chroma/` (vector store). Nothing is uploaded
anywhere except the Bedrock inference payloads themselves — thought content
you capture is sent to Amazon Bedrock for chat, extraction, and embeddings.

## Usage

```bash
brain think "some thought"     # capture + converse; no args -> prompt, or --voice to speak
brain remember "a fact"        # force a long-term memory, no conversation
brain recall "a question"      # semantic search + LLM-synthesized answer
brain resume [topic]           # list recent topics, reconstruct, continue
brain list                     # recent conversations
brain show <id>                # one conversation in full
brain reindex                  # rebuild the Chroma index from SQLite
brain reprocess <id>           # re-run memory extraction on an existing conversation
brain reminders list           # pending reminders
brain reminders tick           # internal: fire due reminders (called by launchd)
```

Run `brain --help` or `brain <command> --help` for the full option list.

A typical session:

```bash
brain think "thinking through how to structure the onboarding flow, leaning towards a wizard"
brain think "actually reminders should fire even if the app isn't open"
brain recall "what was I deciding about onboarding?"
brain resume onboarding
```

## Running the demo

`scripts/demo.sh` runs the full demo path from `plan.md` §1 end-to-end
against **real** Bedrock, in one command:

1. Three `brain think` captures on different topics.
2. `brain recall` with a query worded differently than the originals.
3. `brain resume`, continuing a reconstructed conversation.
4. A listing/show pass proving everything persisted (every `brain`
   invocation is already its own fresh process — there's no daemon to
   restart).

It needs your AWS credentials configured as above and is a manual
verification script, not part of the automated test suite (same idea as
`scripts/bedrock_smoke.py` for `BedrockService` alone):

```bash
scripts/demo.sh
```

## Running in Docker

A `Dockerfile` is included for the **text-based CLI flows only**:
`brain think "some text"`, `brain recall`, `brain remember`, `brain resume`,
`brain list`, `brain show`, `brain reminders list`/`tick` (storage only),
plus the automated test suite. Voice capture and native reminder
notifications are macOS-specific and do not work inside the container.

```bash
docker build -t heybrain .

docker run --rm -it \
  -v heybrain-data:/root/.heybrain \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY \
  heybrain think "some thought"
```

## Testing

```bash
pytest
```

Bedrock is mocked everywhere in the automated test suite — no test hits
AWS. `scripts/demo.sh` and `scripts/bedrock_smoke.py` are the manual,
real-Bedrock smoke tests; run them by hand before a live demo.

## Built with Agent Orchestrator (AO)

heyBrain was built end-to-end using Agent Orchestrator. The plan
(`plan.md`) was broken into scoped, independently reviewable tasks; each
task ran on its own branch with a coding agent, came back as a pull
request with a focused diff, went through AO-driven code review, and was
merged once it held up. That loop — spec a slice, hand it to an agent,
review the PR, merge — is how the CLI, the Bedrock integration, the
memory pipeline, the reminders system, and the Docker packaging all got
built.
