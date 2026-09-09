# Final Baseline vs Stealth Agent Experiment -- Frozen Protocol

**STATUS: PROTOCOL DESIGN COMPLETE. ZERO TRIALS RUN. Written 2026-09-04.**
**THIS PROTOCOL IS FROZEN. Do not change tasks, model, budgets, stopping
conditions, or success criteria after seeing results.**

Frozen, unmodified baselines this protocol was designed against:
- Product V1 (`main`): `bd768e62a887b13a94fdd118693a5c671df1cf95`
- Better-Ways candidate-testing: `54ca96a1d1aae991fe38208f54e4c21c5cce8e8b`
- Better-Ways admission: `f921e2c385dfab511233adc148a5b7a2b8082932`

This branch (`final-agent-experiment`) is a separate fork of `origin/main`;
none of the above three were touched to produce this protocol.

---

## 1. What this pass actually did (sections 1-6 of the task)

1. Inspected `experiments/harness/` in full (README, `run_harness.py`,
   `scripted_arms.py`, `mcp_surface.py`, `openrouter_arms.py`). **Finding:
   this harness's Stealth arm talks to `StubSurface`, a fixture-backed
   offline stand-in -- not the real product.** Confirmed by reading
   `mcp_surface.py`: `McpSurface` is an abstract interface with exactly one
   concrete implementation anywhere in the repo (`StubSurface`). A
   whole-repo search for `ClientSession|mcp.client|stdio_client|
   StdioServerParameters` outside `backend/app/local_agent/` returns zero
   hits. This harness is **not used** by this protocol.
2. Inspected `backend/app/local_agent/runner.py` (`LocalAgentRunner`) in
   full. **Finding: this IS a real, non-stubbed, production implementation**
   -- real MCP client (`mcp.client.streamable_http` + `ClientSession`), real
   unified local+global retrieval with real embeddings, real
   `check_applicability`, real `Agent`+`RepoSandbox` tool-calling execution,
   real evidence reporting, real ad-hoc fallback when nothing matches. This
   is what the experiment uses for arm B, and (via its own internal
   `_run_local_node` primitive, reused directly with no MCP layer) for arm
   A too -- guaranteeing both arms run the identical underlying agent
   mechanism and model.
3. Inspected `backend/app/mcp_server/server.py`'s real tool surface (29
   `@server.tool()`-decorated functions, matching this session's earlier
   confirmed count) -- this is the real, currently-supported interface arm
   B's retrieval path calls into.
4. Queried the live database directly for the 3 admitted procedures'
   real state. **Finding: all 3 are `verification_state='candidate'`, and
   `require_verified=True` is the confirmed real default on both
   `LocalAgentRunner.run()` and `find_best_way`** -- so under
   production-default settings, none of the 3 admitted procedures are
   currently reachable through the real, unmodified, default retrieval
   path. See `environment.json`'s `B_default` vs `B_unverified` split and
   `hypotheses.md` for how this shapes the predictions.
5. Designed 8 tasks (below), each checked against `candidates.jsonl` and
   `admission_review.md` for filename-level leakage (one real leak was
   caught and avoided: candidate C02's claim text names
   `product_model.py` verbatim -- no task in this set targets that file).
   None of the 8 reuse a gold-benchmark scenario, a Better-Ways experiment,
   or a source-resolution seed's exact content.
6. Wrote this frozen protocol plus `tasks.jsonl`, `environment.json`,
   `hypotheses.md`.

**No stop condition was triggered for the CORE mechanism** (a real,
non-simulated Stealth arm is genuinely reachable via `LocalAgentRunner`).
**A narrower, honestly-scoped gap WAS found and is not hidden:** no existing
file orchestrates "run N trials of task T through both arms, isolated
per-trial worktrees, capture the full metrics list" -- this orchestration
script does not exist yet. Writing it is the concrete next step (a new
scratch file under `.scratch/final_agent_experiment/`, not production code),
explicitly out of scope for this design-only pass. See
`environment.json`'s `harness_infrastructure_gap_found`.

---

## 2. Task set (frozen -- see `tasks.jsonl` for full structured detail)

| ID | title | primary Stealth hypothesis |
|---|---|---|
| T1 | Orient on an unfamiliar large file without full reads | structural-summary-before-full-read |
| T2 | Locate and fix a real injected bug, verify via test | locate+fix+verify (control-leaning) |
| T3 | Rename an internal helper across its real call sites | structural-summary-before-full-read |
| T4 | Two independent small edits, agent's choice of sequencing | git-worktree-isolation (WEAK single-agent proxy, flagged) |
| T5 | Repo-structure discovery: find a function by description | structural-summary-before-full-read |
| T6 | Add missing test coverage for a real uncovered function | verification discipline (control-leaning) |
| T7 | Composition task: efficient multi-file audit | **primary composition-effect test** |
| T8 | Verification discipline: run real tests before claiming success | reliability control, not Stealth-specific |

8 tasks (within the requested 5-10 range). T4's fit for the
git-worktree-isolation hypothesis is explicitly weak (documented in
`tasks.jsonl`) -- single-agent architecture cannot fully exercise a
multi-agent-only technique; its result on that specific hypothesis should
be weighted low.

---

## 3. Model, configuration, budgets

- **Model: `gpt-oss-120b`** via GENERAL_COMPUTE (`USE_GENERAL_COMPUTE=true`,
  real, non-empty API key confirmed present in `backend/.env`; base URL
  `https://api.generalcompute.com/v1`) -- the first-listed of the 3 real,
  already-configured panel models (`gpt-oss-120b`, `deepseek-v3.1`,
  `minimax-m2.7`). Chosen because it is already wired into
  `LocalAgentRunner`'s own real code path (`_run_local_node` hardcodes this
  provider), guaranteeing the SAME provider/model for both arms without a
  code change.
- **Model version**: record the provider's own returned `model` field on
  every real API response into each trial's raw log -- do not assume it
  stays static across the whole matrix; a version drift mid-experiment is
  itself a fact to report, not to paper over.
- **max_steps per Agent turn**: 8 (LocalAgentRunner's own current default
  -- kept as-is for both arms, not tuned per task).
- **Per-trial time budget**: 10 minutes wall clock (generous margin above
  T7's expected longer runtime; a trial exceeding this is recorded as a
  `budget_exceeded` failure, not silently extended).
- **Total budget ceiling (pilot, see section 6)**: real cost NOT confirmed
  (see `environment.json`'s `pricing_note` -- `llm_spend` table has 0 rows,
  no real historical cost to ground an estimate; real current pricing must
  be fetched from GENERAL_COMPUTE's own pricing source before real spend).
  Using a labeled ESTIMATE range only (open-weight-tier aggregator pricing,
  typically $0.05-$0.60 per million tokens) purely to size the pilot:
  - Pilot (recommended start, section 6): 3 tasks x 2 trials x 2 arms (A,
    B_default) = 12 real trials, ~4-8k tokens/trial estimated (small
    code-navigation/edit tasks) => ~50-100k tokens total => **estimated
    $0.003-$0.06 total** at the stated price range. Negligible either way,
    but the pilot's PURPOSE is to validate the mechanism works end-to-end
    (see the smoke-test recommendation in `environment.json`) before
    committing to the full matrix, not to save money on a genuinely tiny
    sum.
  - Full matrix (all 8 tasks x 3 trials x 3 arm-configs [A, B_default,
    B_unverified] = 72 trials): estimated **$0.02-$0.4 total** at the same
    price range -- still small in absolute dollars, but 72 real trials is a
    meaningfully larger time/review commitment than the pilot, which is why
    a pilot-first, review, then continue structure is recommended rather
    than running the full matrix in one shot.
- **Trial count: 3 per task per arm-config** (not 5) for the initial run.
  Chosen to keep the first real-money run small and fully human-reviewable
  before any larger commitment, per the task's own request not to assume
  unlimited budget -- 3 is enough to see a paired delta's rough direction
  and spread without yet claiming statistical confidence (see
  `hypotheses.md` and the eventual `final_report.md`'s uncertainty
  section).
- **Isolation**: one disposable `git worktree add --detach <tmp> bd768e62...`
  per trial, removed immediately after that trial's metrics/artifacts are
  captured. A and B never share a worktree, process, cache, or prior
  conversation state. Order of A vs B execution counterbalanced (alternate
  which arm runs first per trial index) so environmental drift (e.g. a
  provider warm/cold state) does not systematically favor one arm.
- **Stopping condition per trial**: the underlying `Agent.run()`'s own
  `stop_reason` (`finished` or otherwise) at `max_steps=8`, or the 10-minute
  wall-clock ceiling, whichever comes first -- identical rule both arms, no
  per-arm tuning.
- **Success criterion**: task's own `verification_method` (deterministic,
  automatable, defined per-task in `tasks.jsonl`) -- never a subjective
  "looks right" judgment.
- **Environmental-failure handling**: a trial that fails for a reason
  unrelated to the agent/product (server didn't start, network blip,
  provider 5xx with no retry budget left, worktree setup itself failed) is
  recorded with an explicit `environmental_failure: true` flag and reason,
  excluded from the primary success-rate comparison, but never silently
  dropped from `trials.jsonl` -- the row stays, tagged.
- **No mid-experiment parameter changes.** If a genuine infrastructure bug
  is found mid-run (not a product/agent behavior question), the correct
  response is to STOP, fix the infrastructure, and note the exact trial
  index where the fix was applied -- never to quietly adjust and continue
  as if nothing changed.

---

## 4. Metrics (task section 8, collected per trial)

Primary: `task_success` (per each task's deterministic verifier),
`deterministic_correctness`, `total_tokens` (input+output, from the real
provider response), `model_calls`, `tool_calls`, `wall_clock_seconds`,
`retries`, `files_touched`.

Also collected: `verification_quality` (did the agent actually run its own
claimed verification, per T8's specific design; boolean + evidence),
unnecessary-work indicators where measurable (e.g. full-file reads on files
irrelevant to the task, counted from the tool-call log), Stealth
retrieval/use decision (`matched: bool`, `source: local|global|none`,
`procedure_id` if matched, whether the agent's final trajectory actually
followed the matched procedure's steps or diverged), implementation
selected (if any), evidence/provenance actually consulted (raw
`session.call_tool` journal), failure category (per task section 5's
6-way scheme, reused for trial-level failures: product / harness /
environment / accepted-limitation / contamination / insufficient-evidence).

---

## 5. Analysis method (frozen in advance)

Paired per-task, per-arm-config comparison table (success rate,
tokens/successful task, cost/successful task, latency/successful task,
model calls, tool calls, retries, files touched) -- both **raw** and
**success-normalized** efficiency reported side by side per task section
10's dominance rule: a lower-token run that fails is never reported as a
win. Report `n`, mean, median (where useful), per-task paired deltas, and
spread -- explicitly do NOT compute or claim a p-value or "statistical
significance" from n=3 per cell; state the sample is underpowered for
that, plainly, in `final_report.md`.

---

## 6. Recommended execution plan (a recommendation, not a unilateral start)

1. **Smoke test** (near-zero cost, not part of the scored matrix): one
   trivial task, one trial, arm B only, confirms the real MCP server
   starts, the real client connects, one real tool call round-trips, one
   real Agent turn completes -- because `test_real_mcp_client_live.py` and
   `test_graph_executor_coding_live.py` (the historical live-proof tests
   `runner.py`'s own docstring cites) no longer exist anywhere in the repo
   tree, this path has not been independently re-confirmed live by this
   design pass.
2. **Pilot**: T1, T3, T7 (one from each of: single-mechanism/file-orient,
   single-mechanism/multi-file, composition) x 2 trials x arms {A,
   B_default} = 12 real trials. Review results, confirm the mechanism
   behaves as this protocol predicts (`B_default` ≈ A, per
   `hypotheses.md`) before spending more.
3. **Full matrix**, only after pilot review: all 8 tasks x 3 trials x
   {A, B_default, B_unverified} = 72 real trials.

Every step above requires separate, explicit authorization before any real
model spend occurs -- this protocol does not authorize its own execution.

---

## 7. What this protocol does NOT do

Does not modify `main`, `evaluation-suite`, `better-ways-candidate-results`,
or `better-ways-admission`. Does not modify `C:/Users/user/stealth-lab`.
Does not modify any frozen gold-benchmark case file. Does not spend any real
money (zero trials run to produce this document). Does not modify
production code -- the one identified infrastructure gap (a trial
orchestration script) is scoped to live under `.scratch/final_agent_experiment/`
as a new scratch file, not `backend/app/**`, when it is eventually written.
