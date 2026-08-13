# Issue #7 — `brain think`: conversation flow

`AppService.think` is the core capture/converse loop: it opens a conversation, runs an
interactive turn-by-turn exchange with Bedrock (one structured call per turn does intent
classification *and* reply generation, per plan.md §9), persists every message, and on
close produces a title/summary/topic. `cli/think.py` and `brain list`/`brain show <id>`
expose it. This doc describes what issue #7 built. **Issues #9, #10, and #13 have since
merged and replaced some of what's described here as stubbed** — each stub section below
says what superseded it, as of this writing (`main` at `cfe5bfc`).

## Public API

```python
AppService.think(text: str | None = None, *, voice: bool = False) -> Conversation
AppService.list_conversations() -> list[Conversation]
AppService.show_conversation(conversation_id: str) -> tuple[Conversation, list[Message]]
```

- `text=None, voice=False` → prompts on stdin (`input()`) each turn.
- `text="..."` → first turn uses that text verbatim (no prompt); later turns still prompt.
- `voice=True` → turns that would otherwise prompt on stdin instead record until Enter
  (`audio/record.record_until_enter`) and transcribe (`transcription/whisper.transcribe`).
  An empty/failed transcription falls back to a one-off stdin prompt for that turn.
- The loop ends on: blank input, `exit`/`quit`/`bye`/`:q` (case-insensitive) → conversation
  is analyzed and closed normally; `KeyboardInterrupt` or `EOFError` (Ctrl-C / Ctrl-D at any
  prompt) → conversation is closed **without** analysis, see below.
- `cli/think.py::run(text, voice)` joins CLI args into the `text` string, calls
  `service.think(...)`, and wraps it in a `try/except KeyboardInterrupt` purely as a
  defensive backstop — `think()` already handles Ctrl-C internally and returns normally, so
  this backstop should never actually fire in practice. It never touches Bedrock, SQL, or
  prompt text directly; all output goes through `AppService`'s injected `output_fn`.

`show_conversation` returns `(Conversation, list[Message])`, not just `Conversation` — the
CLI needs the message list to render the transcript, so issue #7 widened the return type
from the original stub signature.

## Context assembly (plan.md §8.3, layers 1–3 only)

Every turn, before the Bedrock call:

1. The user's message is persisted first.
2. `messages = MessageRepo.list_by_conversation(id)[-6:]` — the last 6 messages *of the
   conversation, including the one just persisted* — mapped to `{"role", "content"}` dicts.
   This is the only conversation history ever sent; full history is never sent.
3. `system = conversation_prompt(conversation_summary=conversation.summary, ...)` — the
   conversation's own `summary` field, which is `None` until the conversation is closed, so
   in practice this is only populated for conversations opened by `resume` (which seeds a
   reconstruction message but not `conversation.summary` itself) — as built in #7, this
   layer was effectively always empty within a single `think` session.
4. The single call is `BedrockService.structured(messages, system, ConversationTurn,
   effort="medium")`, returning `ConversationTurn { intent: Intent, reply: str }` — a new
   schema added by #7 (not part of #1's original schema list), since intent + reply had no
   existing structured-output shape.

## Intent handling, as built by #7 (see "Since superseded" below)

- **capture**: reply printed immediately, message persisted. Nothing else happens.
- **question / recall / resume**: issue #10 (long-term memory retrieval) didn't exist yet,
  so these intents did **not** block on retrieval. The turn proceeded through the exact same
  path as capture (conversation-only context, no vector search), and the CLI additionally
  printed: `"(long-term memory recall isn't wired up yet, so that answer only draws on this
  conversation.)"` immediately after the reply.
- **reminder**: persisted as an ordinary message, nothing else. Real extraction was
  explicitly deferred to issue #13.
- Voice (`--voice`): fully wired, not stubbed — issue #5 had already merged to `main` by the
  time #7 was built.

### Since superseded (issue #9, #10, #13 — for downstream agents' awareness)

- `_run_turn` now calls `_looks_like_retrieval_turn(user_text)` (a cheap local heuristic —
  `?` in the text or a keyword list like "remember", "earlier", "what did") *before* the
  Bedrock call, and if true, runs a real `MemoryRetriever.retrieve(user_text, k=5)` and feeds
  the results into `conversation_prompt(relevant_memories=...)`. This is a heuristic guess
  used only to decide whether a vector search is worth paying for — true intent still comes
  back from the same structured call as before, it just now has real memory context
  available when the guess was right. There is no more "(long-term memory recall isn't wired
  up yet...)" note anywhere in the code.
- `reminder` intent now runs `_handle_reminder`: resolves an absolute datetime via
  `reminder_extraction_prompt` + `ReminderCandidate`, rejects past times and re-prompts, and
  persists a real `Task` + `Reminder` row (plan.md §11).
- On close, capture-intent turns now trigger `_start_background_extraction`, which runs
  `MemoryService.process_conversation` on a background thread; `cli/think.py` calls
  `AppService.join_pending_extraction(...)` (with a spinner if it isn't done immediately)
  before the process exits, per plan.md §9.
- `AppService.__init__` now also constructs `VectorStore`, `MemoryService`,
  `MemoryRetriever`, and a `threading.Lock` guarding the shared SQLite connection against the
  background extraction thread.

## Closing a conversation

On loop exit, if `analyze=True` (i.e. not a Ctrl-C/EOF exit) and the conversation has at
least one message, `_close_conversation` runs one more `BedrockService.structured` call with
`summarization_prompt` against `ConversationAnalysis`, and copies `title`/`summary`/`topic`
onto the conversation. `memory_candidates`/`tasks` on that same response were — and still
are — discarded here; real memory extraction is a separate call via `memory.extractor`, not
this one. A `HeyBrainError` from the summarization call is caught and printed as a plain
message; the conversation is still saved and closed either way.

`conversation.status = ConversationStatus.CLOSED` is always set before returning, whether or
not analysis ran.

## Ctrl-C / exit behavior

`KeyboardInterrupt` or `EOFError` raised from `_next_input` (stdin `input()` or the
voice-recording `input()` used to detect Enter) is caught by the loop in `think`/`_converse`:
analysis is skipped (`analyze=False`), a "Saving conversation and exiting." message is
printed, and the conversation is still closed and persisted via the normal
`_close_conversation(..., analyze=False)` path — it just skips the summarization call.
`think()` returns normally (does not re-raise), so `cli/think.py` exits 0 with no traceback.
