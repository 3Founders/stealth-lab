# Final frozen task set (final pre-score remediation, section E)

**3 tasks: T1, T3, T7-v2.** T7 (original) is retired — see `t7-review.md`.
This set is frozen as of this document; no task may be added, removed, or
altered after any scored result exists for it.

## T1 — MCP tool-surface + shared-resolver identification

- **Statement:** "In this repository at commit `bd768e62...`, examine
  `backend/app/mcp_server/server.py` and answer: (a) how many functions are
  decorated with `@server.tool()`, listing their exact names, and (b) which
  single function is the SHARED resolver that `get_procedure`,
  `check_procedure`, `check_applicability`, `report_execution`, and
  `decide_procedure` all use to accept either a `procedures.procedure_id`
  family handle or a `procedures.id` row key. Write the answer to
  `answer.md` as a short numbered list with exact function names and line
  numbers."
- **Initial repository state:** fresh disposable worktree at `bd768e62`.
- **Expected outcome / deterministic grading:** real ground truth
  (`ground_truth.py::t1_ground_truth`) independently derives the tool-count
  (29) and the real shared resolver name via AST + text scan of the pinned
  file; grader does an exact match against the agent's reported numbers/name.
- **Relevant Stealth capability:** structural-summary-before-full-read
  (one large file, many decorated functions — an outline-first approach
  should need far fewer tokens than reading the whole file).
- **Fair A/B test:** neither arm has this file's content memorized from
  training in a way that differs between arms; both start from the same
  frozen commit, same file, same question.
- **Approximate complexity:** small-to-medium (one target file, ~600 lines).
- **Realistic chance to finish:** both arms completed real work in prior
  calibration at 25 steps (though neither reached a final natural stop —
  see `step-budget-calibration.md`); this task is the smallest of the 3.

## T3 — cross-file rename with real verification

- **Statement:** "`backend/app/services/procedure_extraction/capability.py`
  defines a function used by at least one other module in
  `backend/app/services/`. Find every real call site of that function
  across the whole `backend/app/` tree, rename it to
  `compute_wilson_lower_bound` everywhere (definition and every call site),
  and confirm the existing test suite for that area still passes."
- **Initial repository state:** fresh disposable worktree at `bd768e62`.
- **Expected outcome / deterministic grading:** grader independently
  identifies the real candidate function(s) (`ground_truth.py::
  t3_candidate_functions` — 5 real candidates found this session:
  `wilson_interval`, `band_for_p`, `route_for_p`, `compute_capability`,
  `capability_trajectory`, each with real, AST/import-verified external
  callers) and checks whether the agent's `answer.md`/diff genuinely
  renamed a real candidate consistently across its real call sites, and
  whether the reported test-run command/output is real (re-run and
  compared, same discipline as T8's own design).
- **Relevant Stealth capability:** structural-summary-before-full-read
  (locate the right function among several in one file) + a genuine
  multi-file, safe-edit task (the product hypothesis's "modifying multiple
  files safely" criterion).
- **Fair A/B test:** same file, same starting commit, same instructions,
  both arms.
- **Approximate complexity:** medium (cross-file edit + real verification
  step).
- **Realistic chance to finish:** not yet demonstrated at any tested budget
  (0/2 cells reached a natural stop at 25 steps — see
  `step-budget-calibration.md`); the max_steps decision in that document is
  load-bearing for whether this task is realistically completable at all.

## T7-v2 — composition: largest function across a bounded subtree

- **Statement:** "Across every top-level `.py` file directly under
  `backend/app/services/` (NOT subdirectories), find the single function or
  method (including methods defined inside a class) with the largest
  number of physical lines in its body, counted from its `def` line through
  its last body line inclusive. Report in `answer.md`: the function/method
  name, its containing file (relative path), and its exact line count. Use
  as few full-file reads as you reasonably can; prefer targeted
  search/outline tools over reading every file in full."
- **Initial repository state:** fresh disposable worktree at `bd768e62`.
- **Expected outcome / deterministic grading:**
  `ground_truth_t7v2_largest_function.py` independently re-computes the
  answer via pure AST line-span measurement (no cross-file call-graph
  inference — see `t7-review.md` for why the original T7's grader class was
  retired). Real answer: `record_execution_outcome` in
  `backend/app/services/procedures.py`, 247 lines, a clean 10-line margin
  over the second-place candidate (237 lines).
- **Relevant Stealth capability:** structural-summary-before-full-read
  (75 files to survey — a genuine opportunity for outline-first navigation
  to show a real token/tool-call difference vs. reading every file in
  full) + tool efficiency generally (many repeated search/read tool calls
  across a real, moderately large search space) — the closest honest
  approximation of a composition opportunity this single-agent harness can
  exercise (see `t7-review.md` and `final-readiness-review.md` for why
  git-worktree-isolation cannot be meaningfully tested by a single-agent
  trial).
- **Fair A/B test:** neither arm has any special knowledge of this specific
  measurement; the 75-file scope is large enough that brute-force full
  reads is a real, costly baseline strategy either arm could fall into.
- **Approximate complexity:** the largest of the 3 (75 files), by design —
  this is intentional, to give retrieval/context-efficiency mechanisms room
  to matter.
- **Realistic chance to finish:** UNPROVEN as of this document's own
  writing — this is a brand-new task with zero prior trials of any kind.
  `step-budget-calibration.md`'s fresh 40-step calibration trial for this
  task is the first real evidence either way, and is the actual gate for
  whether this task set is realistically completable — see that document
  for the outcome and this document's own honesty: **do not treat this
  task as validated by its ground-truth's clean margin alone; a clean
  margin proves the GRADER is trustworthy, not that either arm can reach
  it within budget.**

## Diversity check

3 tasks, spanning: single-file read+identify (T1), cross-file edit+verify
(T3), broad-survey+measure (T7-v2). Not a one-task anecdote, though a small
n=3 is honestly still a small sample — see `final-readiness-review.md`'s
uncertainty-discipline section for how this is treated in analysis.
