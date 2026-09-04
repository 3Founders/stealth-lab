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
