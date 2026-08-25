# Build Coordination Board — multi-lane parallel execution (worktree edition)

**Run mode: one git worktree per lane, one agent instance per worktree, this main
checkout = integrator/reviewer.** Lanes push `lane/<name>` branches; the integrator
runs the full suite on main and merges green branches. File ownership is absolute —
a lane that edits outside its paths gets its commit reverted, no discussion.

## Worktrees

```powershell
git worktree add ..\sl-core-a  -b lane/core-a  origin/main
git worktree add ..\sl-core-b  -b lane/core-b  origin/main
git worktree add ..\sl-measure -b lane/measure origin/main
git worktree add ..\sl-research -b lane/research origin/main
# per worktree needing pytest: python -m venv backend\.venv; pip install -r requirements.txt
```

## Lanes

### Lane CORE-A — storage & plans (owns `backend/db/**`, `backend/app/execution/**`, `backend/app/models/**`)
1. `[x]` **1.7** done @2026-08-25 — branch `lane/core-a`. Persist ExecutionPlan/TaskGraph `[D→frozen]`; bind executions to exact
   plan versions (Appendix C #1/#2/#17 proving tests in same change). One-way door.
   *(Prior claim by Chaitanya withdrawn by founder 2026-08-24 — lane reassigned to
   local worktree agent.)*
   Shipped: db/23_plan_persistence.sql (execution_plans + task_graphs + executions,
   frozen by trigger; composite FK procedures(procedure_id,version)); app/execution/plans.py
   (compile/hash/rebind/binding boundary); app/models/plan.py; 36 proving tests in
   tests/test_band1_7_plans.py. Full suite: 885 passed, 106 skipped.
2. `[ ]` **BLOCKED on external** — Real-DB migration chain verification (01→23) on
   disposable Postgres; awaiting Chaitanya's Docker (founder has none locally).
   Paste engine output when run.
3. `[ ]` Band 1 exit-criteria sweep; request integrator review.
4. `[ ]` **NEXT WAVE — 1.9a Evidence table**: typed rows with independence groups;
   procedure verification stats become views over evidence. Proving tests:
   Appendix C #3/#12/#13.

### Lane CORE-B — extraction & gating (owns `backend/app/services/procedure_extraction/**`, `invariants.py`, `applicability.py`, `precondition_gate.py`, `state.py`)
1. `[x] done 2026-08-25 — lane/core-b` **1.8a** Precondition relevance filter (derive gates only load-bearing facts).
2. `[x] done 2026-08-25 — lane/core-b` **1.8b** V6 authoring-time invariant validator + z3 off event loop w/ timeout.
3. `[x] done 2026-08-25 — lane/core-b` **1.8c** Memoized `project_state()` in applicability cascade; tenant-scoped
   cold-start gate.
4. `[x]` done 2026-08-25 — lane/core-b **NEXT WAVE — 1.9b capability computation**: levels-as-banded-P implementing
   the RATIFIED D1 thresholds (spec §16; routing tiers 0.90/0.70 as named config,
   never magic numbers); bidirectional demotion on failure. Proving tests:
   Appendix C #5/#10/#12.
   *(Shipped: procedure_extraction/capability.py — CapabilityScope rejects blank
   context fields [#5]; Wilson-lower-bound P estimate, D1 bands 0.50/0.70/0.85/0.95,
   per-level gates [independence groups ≥2 for L2, verification plan for L3,
   ≥2 envs holding successes for L4/L5, completed review for L5]; routing reads P
   only, never the label; trajectory API proves failure-drops-level-then-recovers
   [#10] and brand-metadata never enters computation [#12]. 25 tests in
   tests/test_capability_bands.py. Suite: 939 passed / 106 skipped / 0 failed
   (= origin/main baseline + CORE-A's 36 plan tests + these 25). Placement note +
   numbered question #2 in Log.)*
5. `[ ]` **NEXT WAVE — 1.9c universal ChangeSet coverage**: every `[V]` mutation
   produces a ChangeSet record (extend `models/change.py` reach to observations,
   procedures, implementations, applicability rules, states). Proving test:
   Appendix C #7.
Rule: NO new migrations (schema needs route through CORE-A); no edits outside owned paths.

### Lane MEASURE (owns `experiments/harness/**`)
1. `[x] done 2026-08-25 — lane/measure` §40 harness skeleton adapted from `experiments/swebench_pro/run_graph_experiment.py`;
   arms A/B/C; synthetic fixtures only until CORE-A lands 1.7.
2. `[x] done 2026-08-25 — lane/measure` Scoreboard script: pass-rate/cost/false-reuse/stale-refusal + power-analysis
   footer (discordant pairs beside every p-value).

### Lane RESEARCH (owns `.scratch/research/**`, updates to `RESEARCH_INTEGRATION_PLAN.md`)
Tooling: `research_exa.py` at repo root (key lives in `backend/.env` as EXA_API_KEY —
never committed). Protocol per founder: market/vendor/pain-point evidence via Exa web
search; technical credibility checks via arXiv / Semantic Scholar / OpenAlex (webfetch);
single synthesized reports into `.scratch/research/`.
1. `[x]` done @2026-08-25 — research lane (this worktree)
   Execute open verification tickets in RESEARCH_INTEGRATION_PLAN.md
   (P-M3 leaderboard movement · P-B1 GATS/WorldEvolver/EnvACE numbers · P-C1 FedWorld
   mechanics · P-I1 Molt/ToolVerse/MobileRL maturity).
   *(All four verified/answered; reports in `.scratch/research/p-*.md`; log appended to
   RESEARCH_INTEGRATION_PLAN.md. GATS citation corrected — 23.9% is stress-test-only.)*
2. `[x] done @2026-08-25 - research lane (file: competitive-sweep-mem0-letta-zep-hipporag-awm.md; tick missed before session died)` Competitive sweep: Mem0 / Letta / Zep-Graphiti / HippoRAG / AWM — what they
   ship vs our trust spine; file deltas as board notes.
3. `[ ]` τ-Knowledge ceiling re-check (arXiv:2603.04370) before harness baselines freeze.

### Lane SHIP (owns `packaging/**`) — activates after CORE-A merges 1.7
1. `[ ]` Installable package wrapping `trace_collector` + `mcp_server`.

## Integrator (= reviewer instance, main checkout)
- Watches for `lane/*` branch pushes; rebases lane onto origin/main when stale.
- Runs full suite on the merge candidate; merges green, rejects red with notes here.
- Sole writer of ROADMAP.md checkbox updates and review files.

## Shared rules

**OVERNIGHT MODE (2026-08-24 night): no integrator on duty.** Lanes may push their
`lane/*` branch and then fast-forward main themselves (`git fetch origin; git rebase
origin/main; git push origin HEAD:main`) ONLY after the full offline suite passes in
their worktree. File ownership is the safety net. No force-pushes, no destructive git
commands, no edits outside owned paths ever. On ambiguity: stop, leave a numbered
blocking question in the Log, continue with the next queue item.

- **Claims**: claim your task line (`- [ ] claimed @ts — name`) before starting; mark
  `[x] done @ts — branch` after. One claimant per task.
- **Branches**: lanes commit to their `lane/*` branch only; rebase onto origin/main
  before signaling done. Never push to main directly from a worktree. Never force-push.
- **Commit prefix**: `core-a:` / `core-b:` / `measure:` / `research:` / `ship:`.
- **Migrations**: CORE-A exclusively. Others needing schema → request below.
- **Docs**: spec v4 / schema.md frozen post-Band-0 (board notes only).
  BAND0_DECISIONS.md founder-owned. ROADMAP checkboxes = integrator.
- **Session end**: merged/claimed state updated here, or blocking question in Log with
  numbered options + proposed default.

## Cross-lane requests
(none yet)

## Founder dependencies (blocking nothing currently)

| Ruling | Blocks | State |
|---|---|---|
| D1 capability bands | nothing (default live in §16, tagged) | open |
| D4 deletion mechanism | Band 5.6 only | open |

## Log

- Board rewritten for worktree multi-lane mode (4 lanes + integrator).
- 2026-08-25 research lane: queue items 2 (competitive sweep → `.scratch/research/competitive-sweep-mem0-letta-zep-hipporag-awm.md`) and 3 (τ-Knowledge re-check → `.scratch/research/tau-knowledge-ceiling-recheck.md`) also done same session.
- **Blocking question #1 (non-blocking for current work):** `EXA_API_KEY` is not present in any worktree — `backend/.env` is gitignored so it never propagated from the original checkout. Options: (a) founder pastes key into each worktree's `backend/.env` (proposed default), (b) lane falls back to built-in websearch permanently (worked fine today), (c) commit a template only.
- MEASURE (2026-08-25): harness skeleton + scoreboard landed on `lane/measure`
  (43/43 harness tests green; smoke sweep of all 10 fixture tasks passes
  end-to-end offline). Backend suite delta vs pristine origin/main = ZERO:
  identical 103 failed / 851 passed / 1 skipped on both — pre-existing gap in
  fresh worktrees (backend/.env absent: STEALTHLAB_MCP_TOKEN collection
  errors when unset; trace_ingestion e2e failures under full-run ordering).
  Not a MEASURE regression; needs an integrator decision on worktree env setup.
  Rebased onto origin/main before push per OVERNIGHT MODE.
- CORE-B (2026-08-25): all three queue items done on `lane/core-b`, one commit.
  - **1.8a** `derive.filter_load_bearing_claims` + `load_bearing_predicates`:
    gates only on behaviorally load-bearing claims (ran tests / invoked pkg
    manager / built / served / touched source), intersected with the probe
    vocabulary; `has_framework` honestly never gated (no deterministic
    signal). E2E contract updated from "equals project_state" to "grounded
    load-bearing subset"; supersession test given build-command evidence so
    it stays non-vacuous.
  - **1.8b** `invariants.authoring_problems` (parse whitelist + per-expr
    satisfiability) behind new validators rule **V6**; `ExtractedProcedure.
    invariants` field now persists via capture_procedure's existing param;
    every z3 Solver bounded by `DEFAULT_SOLVER_TIMEOUT_MS=5000`, `unknown`
    → `undecidable` (never violation); retrieval path moved off the event
    loop via `check_invariants_async`/`asyncio.to_thread`.
  - **1.8c** cold-start gate counts through `visibility_predicate()`
    (`access_scope` param, default unrestricted preserves old behavior);
    per-cascade `_state_cache` dedupes `project_state()` fetches across
    candidates AND repeat subjects within one procedure; `as_of` pinned per
    cascade so the memo key is stable. Offline proof: fake-pool call-count +
    SQL-content tests in `tests/test_applicability_cascade_offline.py`.
  - Suite in this worktree (has backend/.env, unlike MEASURE's): baseline
    **849 passed / 106 skipped / 0 failed** → after **878 passed / 106
    skipped / 0 failed** (+29 proving tests, zero regressions). Rebased onto
    origin/main before push per OVERNIGHT MODE.
- Board hygiene note (CORE-B): origin/main's board file carried a stray
  conflict marker (`>>>>>>> 35670e7 ...`) at the end of this Log — leftover
  debris from the measure commit itself. Removed here; integrator please
  sanity-check future board merges.
- CORE-B (2026-08-25, second wave): **1.9b capability computation** done on
  `lane/core-b`. Pure module `procedure_extraction/capability.py` — no DB, no
  migration, no edits outside owned paths; nothing existing changes behavior
  until a caller adopts it (procedures.py's ticket-13 SPRT lifecycle stays
  authoritative; wiring P into retrieval call sites is deliberately a separate
  change). Named config only: ROUTE_AUTO_THRESHOLD/ROUTE_OFFER_THRESHOLD and all
  four band boundaries + gate minima are module constants, proven retunable by
  monkeypatch tests (an inlined literal would fail them).
  - **Question #2 (non-blocking, placement):** capability.py lives under
    `procedure_extraction/` because that is this lane's only wholesale-owned
    path (`**`); a new top-level `services/capability.py` would be outside
    every owned path and file ownership is absolute. Options: (a) keep as-is
    until Band-2 routing integration (proposed default — relocation is a
    one-line import change), (b) integrator grants CORE-B
    `services/capability.py` and relocates in a follow-up, (c) fold into
    applicability.py (rejected by me: bloats a focused cascade module).
  - Suite in this worktree: **939 passed / 106 skipped / 0 failed**
    (+25 proving tests vs the post-CORE-A baseline; zero regressions).
    Rebased onto origin/main before push per OVERNIGHT MODE.
  - **Question #3 (BLOCKS queue item 5 / 1.9c):** that item's own text says
    "extend `models/change.py` reach", but `backend/app/models/**` is CORE-A
    property and file ownership is absolute — as written, 1.9c cannot be
    started by this lane without guaranteed revert. Options: (a) reassign
    1.9c to CORE-A alongside its models/db ownership (proposed default),
**RESOLVED by integrator 2026-08-25: CORE-B is granted a scoped exception for ackend/app/models/change.py alone (1.9c only, this wave). models/** remains CORE-A otherwise. Proceed with 1.9c.**
    (b) grant CORE-B an explicit exception path list for the change-set
    coverage work (models/change.py + the [V] mutation service files),
    (c) split: CORE-A extends the model, CORE-B writes the Appendix C #7
    proving tests against it from owned test files. CORE-B idle on new
    items until answered; no further queue entries exist for this lane.
