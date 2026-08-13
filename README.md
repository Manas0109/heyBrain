# heyBrain

A laptop-first personal thinking and memory assistant, invoked from the terminal.

> Think out loud → AI understands → system remembers → you continue later.

CLI-only, no daemon: all logic lives in an importable `heybrain` package; the
`brain` command is a thin Typer wrapper around it. All inference goes through
Amazon Bedrock. See `plan.md` for the full design.

## Requirements

- Python 3.12+
- macOS (audio capture and notifications are macOS-specific; see `plan.md` §0)
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
```

Run `brain --help` or `brain <command> --help` for the full option list.

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

## Testing

```bash
pytest
```

Bedrock is mocked everywhere in the automated test suite — no test hits
AWS. `scripts/demo.sh` and `scripts/bedrock_smoke.py` are the manual,
real-Bedrock smoke tests; run them by hand before a live demo.
