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
2. `[ ]` Real-DB migration chain verification (01→22+own) on disposable Postgres;
   paste engine output.
3. `[ ]` Band 1 exit-criteria sweep; request integrator review.

### Lane CORE-B — extraction & gating (owns `backend/app/services/procedure_extraction/**`, `invariants.py`, `applicability.py`, `precondition_gate.py`, `state.py`)
1. `[ ]` **1.8a** Precondition relevance filter (derive gates only load-bearing facts).
2. `[ ]` **1.8b** V6 authoring-time invariant validator + z3 off event loop w/ timeout.
3. `[ ]` **1.8c** Memoized `project_state()` in applicability cascade; tenant-scoped
   cold-start gate.
Rule: NO new migrations (schema unchanged); no edits outside owned paths.

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
>>>>>>> 35670e7 (measure: spec-40 harness skeleton - three-arm runner, scoring, exact-McNemar scoreboard with power footer; synthetic fixtures + 43 harness-local tests)
