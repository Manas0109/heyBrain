# Recall and Remember (issue #11)

Exposes the memory engine as user-facing commands. `brain remember` forces a
long-term memory outside any conversation; `brain recall` answers a query by
retrieving relevant memories and synthesizing an answer from them via the
LLM — it never surfaces raw vector-search hits.

## Public API

### `core/service.py` — `AppService`

```python
def recall(self, query: str) -> RecallResult
```
Calls `MemoryRetriever.retrieve(query, k=RETRIEVAL_K)` (issue #10). If no
memories come back, returns immediately — **no Bedrock call is made**. If
memories are found, builds `recall_synthesis_prompt(query=query, memories=[m.content for m in memories])`
(issue #4) and runs it through `BedrockService.structured(..., schema=RecallSynthesis, effort="medium")`
(issue #3). Returns `RecallResult(answer=synthesis.answer, memories=memories)`
— `memories` is the full retrieved list, not just what the model cited.

```python
def remember(self, text: str) -> Memory
```
Already implemented by issue #9; unchanged here. `AppService.remember` is a
synchronous, thin call into `MemoryService.remember`, which classifies
`text`, forces `importance=1.0`, and runs the same dedup pipeline as
background extraction.

### `core/models.py`

```python
class RecallResult(BaseModel):
    answer: str
    memories: list[Memory] = Field(default_factory=list)
```

### `cli/recall.py`

```python
def run(query: str) -> None
```
Calls `AppService.recall(query)` and renders via `render.print_recall_result`:
the synthesized answer printed prominently (bold), then — only if
`memories` is non-empty — a `Sources:` heading followed by each memory as a
numbered card: `[n] <type badge>  <topic>  (<YYYY-MM-DD>)` with the
memory's content italicized underneath.

### `cli/remember.py`

```python
def run(text: str) -> None
```
Thin wrapper: calls `AppService.remember(text)`, then
`render.print_remembered(memory)` — prints "Remembered " followed by the
same badge/topic/date/content card used for recall sources, so a stored
memory is always confirmed with its assigned type and content.

Both commands are registered in `cli/main.py` as `brain recall <query>` and
`brain remember <text>`.

## Key constraints

- **Synthesis is mandatory.** `AppService.recall` never returns retrieved
  memories' content as the "answer" — the answer field always comes from an
  LLM call over `RecallSynthesis`, except in the empty-result case below.
- **Honest empty response, no Bedrock call.** When retrieval returns `[]`,
  `recall()` returns `RecallResult(answer="I don't have anything on that
  yet.", memories=[])` directly — the LLM is never invoked, so an empty
  store can never hallucinate an answer regardless of prompt adherence.
- **Attribution.** Every source memory rendered under a recall answer (and
  every memory echoed back by `remember`) shows its `memory_type`, `topic`,
  and `created_at` date alongside its content — callers integrating against
  `RecallResult.memories` get full `Memory` objects, not stripped content
  strings, so this metadata is always available.

## Manual verification

```
brain remember "I want to explore AI coding agents handling full PRs."
brain recall "what were my ideas about AI coding agents?"
brain recall "what did I decide about quantum computing hardware?"  # honest empty response
```

Tests: `tests/test_recall.py` (service-level, populated + empty-store paths,
mocked Bedrock), `tests/test_cli_recall.py` (CLI end-to-end, mocked
Bedrock), `tests/test_memory_write_integration.py::test_cli_remember_command`.
