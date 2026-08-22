# 0xAlpha Plan — τ³-Banking: small models + HTN scaffold vs the leaders

## Goal

Beat the τ³-Banking leaderboard (#1 = Qwen3.8-Max, 55.2% pass^1, alltools) using **small
open-weight models only** — gemma-4-31B-it (agent) + gpt-oss-120b (user sim) — with
StealthLab's verified-procedural-memory substrate + HTN execution as the differentiating
scaffold.

## Evidence base (from live phaseD_stealthlab_gemma run, 204 sims)

- Leaderboard: #1 55.2%; classic-RAG rows ~10%; our arm ~2%; bm25-gemma baseline ~20%
  (10-task subset)
- **40% of sweep voided by timeouts** (~37s/turn endpoint latency; not substrate-caused —
  sims with zero substrate_search calls are the slowest at ~224s)
- **64/123 normal-end fails: task required DB writes, agent made zero write calls**
  (`apply_for_credit_card` called 11× total across 204 sims)
- Variant structurally hides actions: `stealthlab_procedures.md` says the tool is "not this
  domain's document knowledge base" while discoverable tools/write flows exist only in
  documents; `additional_instructions.md` demands KB searches this variant cannot perform
- Retrieval decent but improvable: 53% first-call / 64% any-call hit on `required_documents`,
  mostly rank 1–2 when found
- Seeded corpus is single-step doc dumps — none of the substrate machinery actually used in
  this domain: multi-step `ProcedureStep`s with `allowed_implementations`, preconditions,
  z3 numeric invariants (`invariants.py` has zero live callers), slots

## Design: turn the substrate from "doc search" into an action compiler

1. **Surface fix** — rewrite `stealthlab_procedures.md` (drop "not the KB" framing; add a
   completion contract: execute, don't explain); bridge returns structured blocks
   (`GOAL/ELIGIBILITY/STEPS [tool:…]/WATCH OUT`, mirroring `_render_step` goal-first);
   add exact-fetch tool `substrate_get(topic)`
2. **Seed real procedures** — deterministic compiler over the 698 docs → multi-step
   procedures; tool names from doc text ("use `tool_name` to X") → `allowed_implementations`;
   eligibility rules → preconditions; numeric thresholds → z3 invariants; provenance
   `company_ingested`; validators V1–V5 gate every row; contamination line: encode bank
   procedures, never task answers
3. **HTN execution layer** — adapt the `Node`/`SchedulerStrategy` pattern from
   `backend/app/execution/htn_agent.py` to banking conversations; retrieved procedure
   instantiates as a step DAG; deterministic completion tracking drives the weak model
   leaf-by-leaf with bound arguments
4. **Cascade gating** — conversation-discovered customer state (card type, income, state) →
   `current_scope`/invariant bindings → `_scope_matches`/`_excluded`/`check_invariants`
   filter ineligible procedures BEFORE ranking (`applicability.py` doing its designed job)
5. **Retrieval upgrade** — pgvector cosine ⊕ Postgres tsvector lexical, RRF-fused inside the
   bridge; state-conditioned query expansion (deterministic, no LLM); offline precision@5
   eval vs `required_documents`: 64% → ≥80%
6. **Learning loop** — record outcomes per served procedure across the 4 trials; report
   reward-vs-trial curve — evidence no other leaderboard row can show, and the product
   thesis measured rather than claimed

## Status update (2026-08-22, stealthlab-8c session)

No `0xAlpha` peer session is actually running (`ListAgents` shows none) and
`.scratch/tau3/board.md` doesn't exist, so there was no live agent to hand this off to or
collide with. Did the cheap parts of A1 + Phase 1a directly instead of waiting:

- **A1 (partial)** — dropped the "not this domain's document knowledge base" framing from
  all three prompt files (`stealthlab_procedures.md`, `stealthlab_bm25.md`,
  `stealthlab_alltools.md`); replaced with explicit completion-contract language ("if a step
  says to call a tool, call it; do not describe the action instead of taking it") and told
  the model that documents can carry equally-actionable tool instructions, not just
  `substrate_search` results. Still open: the structured `GOAL/ELIGIBILITY/STEPS/WATCH OUT`
  bridge-rendering format and `substrate_get(topic)` exact-fetch tool are NOT done.
- **Phase 1a (top_k)** — raised `SubstrateSpec.top_k` 5 → 18 in `stealthlab_bridge.py`
  (mean requirement is 9.8, 70/97 tasks need >5; 18 gives headroom without reopening the
  `candidate_pool_size` truncation bug fixed earlier).
- **New retrieval variants already built this session, not yet reflected above**:
  `stealthlab_bm25` (`substrate_search` + `KB_search_bm25`, no shell — Windows-runnable) and
  `stealthlab_alltools` (adds read-only `shell`, blocked on this machine by
  `sandbox_manager`'s Linux/macOS-only requirement) — both registered in `retrieval.py` and
  `retrieval_toolkits.py`. `stealthlab_bm25` is mid-run now (`phaseE_stealthlab_bm25_gemma`,
  19/388 @ reward 0.21 at last check, vs. `phaseD` baseline 235/388 @ 0.02).
- **Not touched**: A2 (procedure compiler / real multi-step procedures with preconditions +
  z3 invariants — corpus is still single-step doc dumps), A3 (HTN layer), A4 (cascade
  gating), A5 (hybrid RRF retrieval), A6 (learning loop). These remain the larger, real
  differentiators this plan describes and are unstarted.

## Workstreams & tickets

### A — Scaffold (0xAlpha)

| # | Ticket | Depends on | Gate |
|---|---|---|---|
| A1 | Prompt rewrite + structured bridge rendering + `substrate_get(topic)` | — | rendered block review vs doc text |
| A2 | Doc→procedure compiler (seed v2): steps, tool bindings, preconditions, z3 invariants | — | V1–V5 green on all synthesized rows; spot-check 10 |
| A3 | HTN execution layer for banking (build against existing corpus first) | A1, A2 | dev-subset write-attempt rate >80% |
| A4 | Cascade gating via current_scope + invariant bindings before ranking | A2 | exclusion demonstrated on ≥5 cases |
| A5 | Hybrid retrieval RRF + expansion + offline precision script | — | precision@5 ≥80% (from 64%) |
| A6 | Learning-loop instrumentation + reward-vs-trial curve | A3 | curve computed over full sweep |

### B — Measurement (side-by-side Claude Code)

| # | Ticket | Depends on | Gate |
|---|---|---|---|
| B0 | Monitor phaseD_stealthlab_gemma to completion; snapshot results.json + log into `.scratch/tau3/results/phaseD/` | — | archived |
| B1 | Re-baseline @ 600s timeout: `phaseC2_bm25_{gemma,gptoss}` + `phaseC2_golden` (new save-tos; paired comparability) | B0 | paired numbers on board |
| B2 | Dev harness: fixed 12-task stratified subset incl. ACTION-basis tasks, one-command runner | — | reproducible run |
| B3 | Scorecard: pass^k, mean reward, tokens (`tokens_report.py`), duration; posts gate status to board | B1, B2 | gate ladder live |

## Coordination protocol (with side-by-side Claude Code)

- Primary channel: board file `.scratch/tau3/board.md` (claims + status; both agents read/write)
- Strict file ownership — A touches `backend/app/services/*` +
  `vendor/tau2-bench/src/tau2/domains/banking_knowledge/{retrieval*, stealthlab_bridge, prompts/*}`;
  B touches only `data/simulations/phase*`, logs, `.scratch/tau3/`. Zero overlap
- Worktree isolation if either side needs branch-level separation
- Optional live messaging: claude-peers-mcp; optional collision guard: claude-presence MCP

## Gates (ladder)

normal-end >90% → write-attempt >80% on write-expecting tasks → dev subset ≥40% (2× bm25) →
full 97×4 sweep ≥50–55% → leaderboard submission (retrieval column:
`stealthlab_procedures`, custom-config disclosed).

## Harness notes

- Raise `--timeout` 300s → 600s (fixes the 40%-voided sweep); all baselines re-run under the
  same setting or comparisons are invalid
- Keep `--max-concurrency 2` while endpoint latency dominates; revisit after Phase 1
- `--auto-resume` makes every sweep kill/resume safe; new save-to names per config change
- litellm cost mapping is broken for these models (`model isn't mapped yet` errors are
  cosmetic) — token truth lives in message `usage` dicts; use `tokens_report.py`

## Honest risks

- gemma-4-31B may cap below 50% regardless of scaffold quality (gpt-oss arm signals early)
- HTN-in-conversation is new ground vs its coding-episode origin
- Voyage free-tier rate limits constrain query-expansion volume
- Leaderboard submission requires custom-retrieval disclosure (accepted — it's the point)
- n=4 trials carry wide intervals; report pass^k with explicit uncertainty, never bare point estimates
