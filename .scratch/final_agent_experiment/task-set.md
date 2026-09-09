# Final frozen task set (bounded T1/T3 pass, final pre-score remediation)

**3 tasks: T1-v2, T3-v2, T7-v2.** All three original tasks (T1, T3, T7) are
retired from the scored set and preserved unmodified in `tasks.jsonl` as
historical evidence — see `t7-review.md` (T7's retirement, prior pass) and
this document's own "why repaired" sections below. This set is frozen as of
this document; no task may be added, removed, or altered after any scored
result exists for it, per this task's own explicit prohibition on further
iteration after this pass.

## T1-v2 — shared-resolver identification (repaired from T1)

- **Statement:** examine `backend/app/mcp_server/server.py` (fresh
  disposable worktree, commit `bd768e62...`) and identify the single
  function that `get_procedure`, `check_procedure`, `check_applicability`,
  `report_execution`, and `decide_procedure` all share to resolve either a
  `procedures.procedure_id` family handle or a `procedures.id` row key.
  Also report an approximate `@server.tool()` count (informational only).
- **Deterministic grading:** resolver name match against real AST/regex
  ground truth (`ground_truth.py::t1_ground_truth`) is the ONLY gating
  requirement (`verifiers.py::verify_T1_v2`); tool count is checked within
  a +/-30% tolerance band but never gates success.
- **Why repaired, not KEPT or RETIRED:** real evidence from 8 trials of the
  ORIGINAL T1 across 4 step budgets (8/16/25/40), both arms: 100% budget
  consumption every single time (excluding 2 unrelated `api_error`
  outliers), zero convergence trend — the same flat non-convergence
  signature that led to the original T7's retirement. But unlike T7, T1's
  underlying navigation/resolver-identification mechanism is sound and
  bounded (one file, one real shared function, 5 real call sites to trace)
  — the problem was a bundled, mechanically demanding requirement (>=90%
  exact-name recall across 29 items) tangential to the actual
  hypothesis-relevant test. Repair (drop the exhaustive-enumeration gate,
  keep resolver ID as the sole requirement) is the smallest principled
  correction, not a redesign for convenience — the hard part of the task
  (deep reasoning to find what 5 different call sites share) is unchanged.
- **Relevant Stealth capability:** structural-summary-before-full-read.
- **Fair A/B test:** same frozen file/commit, same question, identical
  prior for both arms.
- **Approximate complexity:** medium (one ~700-line file, real call-graph
  reasoning across 5 functions).

## T3-v2 — cross-file rename with corrected grader (repaired from T3)

- **Statement:** unchanged from the original T3 — find every real call site
  of a `capability.py`-defined function used elsewhere in
  `backend/app/services/`, rename it to `compute_wilson_lower_bound`
  everywhere (definition + every real code call site; renaming prose
  comments/docstrings that merely mention the name is explicitly not
  required), confirm the real test suite for that area still passes.
- **Deterministic grading:** `ground_truth.py::_find_real_call_sites`
  (new, AST-based) replaces the original `_find_call_sites` (text-substring
  regex). Only genuine `ast.Name`/`ast.Attribute`/import-alias references
  count as a "call site" — comments are never part of the AST at all, and
  docstrings are `ast.Constant` string nodes, not `Name`/`Attribute` nodes,
  so both are excluded by construction. `verifiers.py::verify_T3` is
  unchanged (reused as-is for T3-v2) — the defect and its fix live entirely
  in the ground-truth data source, not the verifier's own logic.
- **Why repaired, not KEPT or RETIRED:** confirmed, real grader defect —
  both real candidate functions in `capability.py` (`wilson_interval`,
  `band_for_p`) have their names embedded in prose comments/docstrings
  across multiple files (`backend/app/api/implementations.py:170`,
  `backend/app/services/capabilities.py:43-44/268`,
  `backend/app/services/procedure_graph_api.py:29-30/357`,
  `backend/app/services/product_model.py:16` — confirmed directly via
  grep). The original grader's success condition required the OLD name to
  have ZERO remaining textual matches anywhere in the tree; these harmless
  prose mentions made that condition structurally unreachable even after a
  functionally perfect rename. Direct evidence this was a grader bug, not a
  task-design or agent-capability problem: 4 of 8 real T3 trials show the
  agent successfully defined `compute_wilson_lower_bound` in
  `capability.py` (`new_name_defined_in_capability_py: True`), but the
  grader's rename-detection heuristic still reported
  `renamed_from_detected: False` every time. Same class of bug (naive
  substring matching mistaken for semantic/structural truth) as the
  original T7's grader defect, repaired the same principled way (an
  AST-aware scanner), without forcing T7-v2's specific line-count mechanism
  onto a task whose natural shape is call-site discovery, not size
  measurement.
- **Relevant Stealth capability:** structural-summary-before-full-read
  (finding real call sites efficiently rather than reading every file).
- **Fair A/B test:** same task, same commit, same real multi-file edit risk
  for both arms.
- **Approximate complexity:** medium-high (up to 5 real call sites across
  5 files, real test-suite verification).

## T7-v2 — composition: largest function across a bounded subtree

Unaffected by this pass (repaired the prior pass, from original T7). See
`t7-review.md` for the full original repair record. Summary: pure AST
`end_lineno - lineno + 1` measurement across 75 top-level files directly
under `backend/app/services/`, zero cross-file call-graph inference by
construction. Real computed answer: `record_execution_outcome` in
`procedures.py`, 247 lines, a clean 10-line margin over second place (237
lines). Already converges cleanly and naturally at both 25 and 40 steps in
both arms (`no_tool_call`, genuine stop, well under either ceiling).

## Why this final 3-task set is scientifically valid

All three tasks now have: (1) a real, bounded, unambiguous ground truth
independently re-derived by the grader at trial time from the pinned
commit, never trusted/cached; (2) a deterministic, non-subjective success
condition; (3) a confirmed-fair setup for both arms (same commit, same
file(s), same question, no arm-specific hints); (4) a real, disclosed,
evidence-backed relationship to the structural-summary-before-full-read /
composition hypothesis this experiment exists to test. No task was kept,
repaired, or invented because it was likely to favor either arm — every
change in this pass was driven by a confirmed defect (T1's mechanically
demanding bundled requirement; T3's grader bug) with real trial evidence
behind it, not by a desire for convergence. 3 tasks (not 2, not padded to a
larger number) is what survived honest scrutiny; the coordinator's own
instruction explicitly permitted landing on 2 if that's what the evidence
supported, but a principled repair was available for both T1 and T3, so
neither needed to be dropped or replaced.

## Diversity check

3 tasks, spanning: single-file read+identify (T1-v2), cross-file
edit+verify (T3-v2), broad-survey+measure (T7-v2). Not a one-task anecdote,
though n=3 is honestly still a small sample — see
`final-readiness-review.md`'s uncertainty-discipline section for how this
is treated in analysis.

## FINAL TASK-SET DECISION (one-pass revision-or-retirement, this document supersedes the "3-task set" conclusion above)

Per the coordinator's explicit instruction to stop calibrating step budgets
and instead decide, for each of T1-v2/T3-v2, exactly one REPAIR or RETIRE
(no further iteration), real trajectory evidence was read for both tasks'
non-converging trials. Both showed the same root cause: the agent found the
answer-relevant fact early, then burned its remaining budget in a
repeated, non-converging re-search loop rather than stopping -- not a
scope problem, not a grader defect (both graders were already correct from
the prior pass).

**T1-v3 (repair of T1-v2): KEPT.** Fix: task statement now asks for
exactly the one question the grader checks (dropped the non-gating
"also report approximately how many tools" ask that was inviting the
unproductive search loop). Real verification result (non-scored, one
attempt per arm, `bd768e62a887b13a94fdd118693a5c671df1cf95`, max_steps=40):

- Arm A: naturally completed in 14 tool calls / 86.2s (`stop_reason=no_tool_call`),
  wrote `answer.md`. Resolver named incorrectly.
- Arm B_default: naturally completed in 12 tool calls / 59.5s
  (`stop_reason=no_tool_call`), wrote `answer.md`. Resolver named incorrectly.

Both arms now naturally complete, well under budget -- a large improvement
over T1-v2 (which never wrote an answer file and consumed its full budget
in the traced example). The repair fixed the structural non-completion
defect it targeted. Both arms answering incorrectly is a separate,
correctness-axis outcome (this identification question is genuinely hard:
29 candidate `@server.tool()` functions, only one of which is the true
shared resolver) -- not evidence the task fails to complete, and not
grounds for retirement under the coordinator's literal completion rule.
This is disclosed plainly, not smoothed over: a task where both arms
answer wrong in a single non-scored trial does not by itself prove the
task can't discriminate arms across the real scored pilot's multiple
trials: single-trial correctness is not the question this pass was
chartered to answer.

**T3-v3 (repair of T3-v2): RETIRED.** Fix: added one explicit
stopping-condition sentence ("There are only a small, bounded number of
real call sites... proceed directly to making the edits... a single
search before your edits and one test run at the end is sufficient").
Real verification result (non-scored, `bd768e62a887b13a94fdd118693a5c671df1cf95`,
max_steps=40):

- Arm A: 2 consecutive genuine provider `api_error`s (202.3s, then 208.8s)
  -- stopped per this session's established retry discipline (at most one
  environmental retry). No real completion data obtained for this arm;
  this is provider noise, not attributed to the task.
- Arm B_default: ran the full 40/40 steps (`stop_reason=step_budget`,
  `failure_category=budget_exhaustion`), did not complete the rename.

Neither arm demonstrated natural completion in this pass: arm A's true
behavior is unknown (provider-blocked, not task-refuted), and arm B
positively failed to converge even after the stopping-condition repair --
the same non-convergence pattern the repair specifically targeted. Per
the coordinator's own rule ("if a surviving task still cannot naturally
complete in both arms after this single verification attempt plus at most
one environmental retry, retire it") and the standing instruction not to
treat a bigger budget as the default fix, T3 is retired after this, its
one permitted revision. T3/T3-v2/T3-v3 are preserved unmodified in
`tasks.jsonl` as historical record; none are in the final scored set.

## FINAL scored task set (supersedes the 3-task set above): T1-v3, T7-v2

Two tasks, not three. Per the coordinator's explicit standing guidance,
two strong, real, naturally-completing tasks are preferable to a third
that has now twice failed the same non-convergence pattern. T7-v2 was not
re-run in this pass (out of this pass's required scope, which was
T1/T3 only); it is carried forward on the strength of its two prior,
independent clean convergences (`t7-review.md`, `remediation-results.md`)
and is not itself a fresh claim made in this document.

- **T1-v3** -- single-file structural identification (`server.py`,
  29-candidate resolver question). Naturally completes both arms, real
  multi-call-site reasoning requirement, deterministic exact-match grader.
- **T7-v2** -- broad-survey composition (largest function across 75
  top-level files under `services/`). Naturally completes both arms in
  prior passes, pure AST line-count grader (no fuzzy call-graph
  inference), genuinely exercises structural-summary-before-full-read at
  a different task shape than T1-v3 (breadth-survey vs. targeted-lookup).

max_steps stays **40** -- unchanged, no new calibration search run this
pass. All 4 real cells run this pass (T1-v3 x2 arms, T3-v3 x2 arms, minus
the 2 T3-v3-A attempts consumed by provider noise) finished in 59-209
wall-clock seconds; T7-v2's own historical convergence is 20-26 tool
calls. 40 remains a generous, evidence-grounded ceiling for both surviving
tasks with real margin, not a value chosen to force convergence.
