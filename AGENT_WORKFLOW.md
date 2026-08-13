# heyBrain — Issue Workflow

How we work through GitHub issues. One issue at a
time, in dependency order, with a review checkpoint before any code is written.

## Per-issue loop

1. **Fetch** — `gh issue view <n>` to pull the live issue body (title, labels,
   scope, acceptance criteria). Treat the GitHub issue as source of truth over
   `ISSUES.md` if they ever drift.

2. **Understand** — read the issue plus its referenced sections of `plan.md`,
   and skim the current state of the relevant `src/heybrain/...` paths (if
   any exist yet from prior issues). Check the issue's `Dependencies` line
   against what's actually landed — don't start an issue whose deps aren't
   merged.

3. **Plan** — enter plan mode and present: scope interpretation, the files to
   be created/touched, key technical decisions (esp. anywhere the issue is
   ambiguous or where I'd deviate from `plan.md`), and how the acceptance
   criteria will be verified. You approve, redirect, or amend before anything
   is written.

4. **Implement** — build the smallest version that satisfies the issue's
   acceptance criteria. No scope creep into later issues.

5. **Verify** — run the issue's acceptance criteria literally (tests, `brain
   doctor`, manual command run, whatever the issue specifies). Don't mark
   done on green tests alone if the issue calls for a manual run.

6. **Close the loop** — show you the diff/result, commit only when you say
   so, and close or comment on the GitHub issue with what landed and how it
   was verified.

7. **Advance** — move to the next issue per the order below. Re-check
   dependencies before starting it, since a prior issue may have shifted
   scope during review.

## Ordering (sequential, per ISSUES.md dependency graph)

```
#1  Contracts, scaffold, config          (blocks everything — alone)
#2  SQLite storage layer
#3  BedrockService                       (highest external risk — verify Bedrock
                                           model access in console before this one)
#4  Prompt library and eval set
#5  Audio capture and transcription
#6  brain doctor
#7  brain think (conversation flow)      — first integration point
#8  Chroma vector store wrapper
#9  Memory write path (extraction/dedup) — hardest issue, don't rush review
#10 Memory read path (retrieval/ranking)
#11 brain recall / brain remember
#12 brain resume
#13 Reminders                            (stretch — first to cut if time is short)
#14 Terminal presentation + demo script  (stretch — final assembly)
```

Even though ISSUES.md marks several of these "parallel" (#4/#5 vs #2/#3, #8
vs #7, etc.), we're running one at a time by choice — simpler to review each
plan with you before moving on. If we later want to reclaim that parallelism,
the fix is running independent streams in separate git worktrees, not doing
more without a checkpoint.

## Standing rules for every issue

- Never touch an issue's `Acceptance criteria` as a place to cut corners —
  if something in scope turns out to be harder than expected, surface that
  in the plan step, don't quietly narrow it during implementation.
- `plan.md` is authoritative when it conflicts with `ISSUES.md` (the latter
  is derived from the former).
- Stop and ask if an issue's dependencies aren't actually satisfied yet,
  rather than stubbing around a missing contract.
- Commits never include a `Co-Authored-By: Claude` (or any AI co-author)
  trailer.
