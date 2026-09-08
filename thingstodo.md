# thingstodo.md — shared work log for every agent

This file is the single place where every agent (human or AI) writes down what
they are doing. It exists so that many agents can work at the same time without
stepping on each other or repeating work.

## The rules (read before you touch anything)

1. **Before you start a task**, add a line for it under "IN PROGRESS" below.
   Write your agent name, the date, what you are about to do, and which files or
   areas you will touch. Do this *first*, before any code change.
2. **While you work**, keep your line updated if the plan changes.
3. **When you finish**, move your line from "IN PROGRESS" to "DONE" and put a
   `[x]` in front of it. Add one plain sentence about what actually changed and
   the commit hash if there is one.
4. **If you stop without finishing**, move the line to "PAUSED / HANDOFF" and
   write what is left and anything the next agent needs to know.
5. **Never delete** someone else's line. Only add or move your own.
6. Keep the language simple. Short sentences. No jargon that a new reader would
   not understand.
7. If two agents want the same task, the one who wrote their line first keeps it.
   The other picks something else.

---

## DONE (already finished this session)

- [x] **Fixed the leaderboard "stale solution" bug (Bug #7).** The product
  leaderboard used to rank fixes that were built on out-of-date knowledge. Now it
  hides those, marks them `STALE`, and keeps their old numbers only for history.
  Pushed to `main`.
- [x] **Fixed the privacy bug on benchmark and evaluation reads (Bug #8).** Before,
  anyone could read a private problem's benchmarks and evaluations. Now those
  reads check the same permission as the problem itself. The MCP server now uses
  the real caller identity, never full-access-bypass. Pushed to `main` and tagged
  `v1-final-2026-09-03.2`.
- [x] **Built the claim-graph web view.** Turned it on. It shows claims and how
  they relate, plus similarity links. Reachable at `/claim-graph`.
- [x] **Built the procedure and task-node web view.** New page at
  `/procedure-graph`. Shows procedures as circles (colored by how verified they
  are, red ring if stale) and task nodes as blue squares, with edges for
  versions, breakdown into tasks, and sub-procedure links. Connected to the live
  backend. Pushed to `main`.
- [x] **Fixed 4 problems from `problems.md` (A, B, C, D):**
  - A: cleaned up 3 broken "verified" procedures that pointed at missing
    sub-procedures. Wrote a repair script.
  - B: added a missing database column (`candidates.no_action_justified`) with a
    new migration file and applied it to the live database.
  - C: the MCP procedure tools now accept both kinds of procedure id (the stable
    handle and the raw table row key).
  - D: added real seed data — 3 procedures (2 fully verified) and 2 runnable
    implementations — so the registry tools return real results.
  - All 4 pushed to `main`.
- [x] **Wrote down the state of the big 60-source run.** File:
  `.scratch/autonomous_run_state.md`. It records the git state, the database
  state, what features already exist, and the decisions made for this run.
- [x] **Resolved all 60 source seeds.** File:
  `.scratch/better_ways_corpus/source_resolution/seeds.jsonl` plus a `README.md`.
  Result: 21 sources have a confirmed real artifact, 15 more are probably real,
  the rest are article-only, guessed, or could not be found. The 60 seeds
  collapse into 9 "families" (groups that are really the same procedure). Pushed
  to `main`.
- [x] **Checked the claim-graph `KeyError: 'relation'` bug.** It was already
  fixed earlier. Confirmed by running the tests (11 passed).

---

## IN PROGRESS (someone is working on this right now)

- [ ] **MCP evaluation harness** — _core-a / Claude Sonnet 5 — started 2026-09-08._
  Building the end-to-end test that goes through the real MCP interface:
  search a procedure → retrieve it → inspect it → plan → execute → check the
  result with plain rules → then check it again with a separate LLM judge →
  write an evaluation → search again. The LLM judge must not be the same model
  that produced the answer. Will reuse the existing LLM setup in
  `backend/app/debate/panel.py` (do not build a second one). New code goes under
  `backend/app/eval/` or `backend/scripts/`, with unit tests.
  Files likely touched: new harness module + new test file. Will push to `main`
  when it passes.

---

## TO DO (not started — pick one, add your line to IN PROGRESS first)

- [ ] **Representative end-to-end run for each of the 9 families.** Take at least
  one real source per family and run it all the way through the real MCP surface
  (ingest → implementation → execute → verify with rules → verify with LLM judge
  → evaluation → admit or reject). Save one artifact file per source under
  `.scratch/better_ways_corpus/evaluations/`. Depends on the MCP evaluation
  harness above being done first.
- [ ] **Composite score: baseline vs Stealth.** Build the comparison that shows,
  across the representative sources, how the plain baseline does versus going
  through Stealth. One combined benchmark.
- [ ] **Final report + `run_state.json`.** Write an honest summary of the whole
  run: which sources were admitted, which are promising, which lack evidence,
  which were rejected, which were blocked. No made-up results. Machine-readable
  `run_state.json` next to it.
- [ ] **Full regression gate.** Run the whole offline test suite and the
  packaging checks. Record pass/fail counts. Fix anything this run broke; do not
  touch pre-existing unrelated failures beyond noting them.

---

## PAUSED / HANDOFF (started, not finished — read before picking up)

_(nothing here yet)_

---

## Prompts to give other agents

Copy one of these into a new agent. Fill in the `<...>` parts.

### Prompt A — generic worker

> You are joining a shared task run. **First, open `thingstodo.md` at the repo
> root and read all of it, especially "The rules".** Then:
>
> 1. Pick the single task `<TASK NAME>` from the "TO DO" section (or the specific
>    task I name here).
> 2. Before you write any code, add a line under "IN PROGRESS" in
>    `thingstodo.md`: your agent name, today's date, one sentence on what you are
>    about to do, and the files or areas you will touch. Save the file.
> 3. Do the task. Keep your line updated if your plan changes.
> 4. When done: move your line to "DONE", put `[x]` in front of it, add one plain
>    sentence about what changed and the commit hash.
> 5. If you have to stop early: move your line to "PAUSED / HANDOFF" and write
>    exactly what is left and what the next agent needs to know.
>
> Rules: never delete another agent's line. Only add or move your own. Keep the
> writing simple. Commit with the prefix `core-a:` and include the pytest
> pass/skip/fail counts in the commit message. Re-fetch and rebase onto
> `origin/main` before pushing; fast-forward only, never force-push.

### Prompt B — end-to-end family run (needs the MCP harness done first)

> Read `thingstodo.md` at the repo root first, all of it.
>
> Your task: take the family `<FAMILY NAME>` from
> `.scratch/better_ways_corpus/source_resolution/README.md`, pick its best real
> source, and run it fully through the **real MCP interface** (not the Python
> functions directly): search → retrieve → inspect → plan → execute → check with
> plain rules → check again with the independent LLM judge → write the
> evaluation. Save one artifact file for this source under
> `.scratch/better_ways_corpus/evaluations/`.
>
> Before you start: add your line under "IN PROGRESS" in `thingstodo.md`
> (agent name, date, which family, which files). When done: move it to "DONE"
> with `[x]`, one sentence, and the commit hash. If a source is genuinely weak,
> mark it `INSUFFICIENT_EVIDENCE` or `REJECT` honestly — do not invent a passing
> result. Never conflate a setup/infrastructure failure with the source being
> bad.
>
> Follow the same commit and push rules as everyone else (`core-a:` prefix,
> pytest counts in the message, rebase on `origin/main`, fast-forward only).

### Prompt C — reviewer / regression gate

> Read `thingstodo.md` at the repo root first.
>
> Your task: run the full offline test suite and the packaging checks. Record the
> pass / skip / fail counts. Compare against the last known-good counts in the
> recent commit messages. If this run introduced a new failure, find and fix the
> cause (do not weaken the test). Pre-existing unrelated failures: just list
> them, do not fix them here.
>
> Add your line under "IN PROGRESS" before you begin. Move it to "DONE" with the
> final counts when finished.
