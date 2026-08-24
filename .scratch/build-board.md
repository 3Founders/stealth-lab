# Build Coordination Board — Bands 1/3/P parallel execution

Two-agent split modeled on the τ³ campaign protocol: strict file ownership, zero
overlap, claims before work. Either agent may hold either lane long-term; lanes are
defined by FILES, not by identity.

## Lanes

### Lane CORE (default: Chaitanya's ox-alpha)
Owns: `backend/db/**` · `backend/app/**` · `backend/tests/**`
Current queue (in order):
1. `[ ]` **1.7** Persist ExecutionPlan/TaskGraph `[D→frozen]` tables; bind executions
   to exact plan versions (Appendix C rows #1/#2/#17 proving tests). One-way door —
   highest priority in the repo.
2. `[ ]` **Real-DB migration verification**: apply full chain (01→22) on a disposable
   Postgres via `scripts/migrate.py`; paste engine output. Static text tests are not
   enough for CHECK constraints / `ALTER TYPE ADD VALUE`.
3. `[ ]` **1.8** Extraction correctness bundle: precondition relevance filter, V6
   authoring-time validator, z3 off event loop + solver timeout, memoized
   `project_state()`, tenant-scoped cold-start gate.
4. `[ ]` Band 1 exit-criteria sweep + request review from reviewer agent.

Rules: sole author of any new migration files. Never edit `experiments/harness/**`.

### Lane MEASURE+SHIP (default: reviewer ox-alpha instance)
Owns: `experiments/harness/**` (new) · `packaging/**` (new) · `.scratch/build-board.md`
Current queue (in order):
1. `[ ]` **Harness skeleton** (`experiments/harness/`): adapt the three-arm pattern
   from `experiments/swebench_pro/run_graph_experiment.py`; arms A/B/C per spec §40;
   synthetic fixtures only until CORE lands 1.7 — no backend file may be modified.
2. `[ ]` **Scoreboard script**: pass-rate/cost/false-reuse/stale-refusal table +
   power-analysis footer (discordant pairs printed beside every p-value).
3. `[ ]` **P1 packaging**: installable package wrapping `trace_collector` +
   `mcp_server`. Import-only dependency on `backend.app` — read-only, never edits.
4. `[ ]` Backend hooks discovered to be missing (e.g. latency timers inside
   `retrieval.py`) → **do not implement**; file a `CORE-request` claim below.

Rules: never creates migrations; never modifies `backend/**`; reads freely.

## Shared rules

- **Claims**: write `- [ ] claimed @timestamp — agent` on a task line before starting;
  move to `[x] done @timestamp — commit` after merge. One claimant per task.
- **Git**: `git pull --rebase origin main` immediately before every push; expect
  rejection, rebase, retry. Never force-push. Commit prefix = lane tag
  (`core:` / `measure:` / `ship:`).
- **Migrations**: Lane CORE exclusively. MEASURE+SHIP needing schema changes files a
  request; CORE schedules it.
- **Docs**: `ROADMAP.md` checkboxes updated by CORE only. `BAND0_DECISIONS.md` is
  founder-owned; both agents read rulings, neither writes them. Spec v4 / schema.md
  frozen post-Band-0 — discrepancies get board notes, not silent edits.
- **Session end**: merged commits with pytest output in the message, or an open
  blocking question written here.

## Founder dependencies (blocking nothing currently)

| Ruling | Blocks | State |
|---|---|---|
| D1 capability bands | nothing (default live in §16, tagged) | open |
| D4 deletion mechanism | Band 5.6 only | open |

## Log

- (initial board created)
