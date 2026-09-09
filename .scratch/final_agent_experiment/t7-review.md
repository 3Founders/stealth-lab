# T7 review — retired, replaced with T7-v2

**Outcome: T7 is retired from the primary A/B set. Replaced with T7-v2
(bounded scope, deterministic AST-based grader). The original T7 remains in
`tasks.jsonl` unmodified, as historical evidence — not deleted.**

## Investigation

The coordinator's diagnostic question was: is 0/11 ground-truth recall
(unchanged across 8/16/25-step calibration) an agent weakness, or a real
task/grader problem? Two independent, real problems were found — either one
alone would justify retiring the task; together they make repair-in-place
dishonest.

### Problem 1: the original grader's ground truth contains a confirmed false positive

`ground_truth.py::t7_ground_truth` finds every `_`-prefixed function defined
anywhere under `backend/app/services/`, then counts a file as an "external
caller" if the function's bare NAME appears anywhere in that file's text
(`re.search(r"\b" + fn_name + r"\b", text)`) — no check that the name is
actually *imported* from the defining module, just that the substring exists.

Concrete, verified false positive: `_now_iso` is independently, separately
defined in **four** unrelated files —
`backend/app/local_agent/local_claims.py`,
`backend/app/local_agent/local_store.py`,
`backend/app/mcp_server/tasks_extension.py`, and
`backend/app/services/ingestion_scheduler.py` — each calling only its own
local copy (confirmed: `def _now_iso` appears in all four; each caller site
in the first three files calls the name with zero import statement pulling
it from `ingestion_scheduler.py`). The old scanner picked
`ingestion_scheduler.py` as "the" definer (first one `rglob` happened to
visit) and then credited the other three as "callers" of it — a relationship
that **does not exist**. No correct agent could ever discover it, because
it is false.

### Problem 2: even a corrected, import-aware grader has residual ambiguity

A replacement scanner (`ground_truth_t7v2.py`, this pass) requires a real
`ast`-verified import — `from <defining module> import fn_name` or a
qualified `module.fn_name(` call where `module` is itself imported from the
defining file — not a bare substring. Applied to a representative 3-file
sub-scope (`agent_search.py`, `applicability.py`, `reuse_detection.py`), the
originally-claimed 7-entry ground truth for just those 3 files shrinks to
**2** genuinely defensible entries (`_scope_matches`, `_lexical_overlap`).
The other 5 either had no import at all (`_vector_search`, `_lexical_search`,
`_claim_matches_precondition` — confirmed via direct grep for any
`import`/`from...import` referencing them anywhere in the app tree: none
found) or lost callers that merely **import-and-re-export** the name without
ever calling it in that file (`_scope_matches` dropped from 4 claimed callers
to 2 real ones once re-export-only imports were excluded; `_excluded`
dropped from 2 to 1, falling below the "≥2 callers" threshold entirely).
Distinguishing "imports and calls" from "imports and re-exports" from
"imports transitively via `__init__.py`" is itself a genuine judgment call a
static text/AST scanner cannot resolve with full confidence in every case —
this is evidence the underlying static-analysis problem (statically
determining "genuine" private-helper cross-file *usage*, not just textual
co-occurrence, across an organically-grown codebase with re-exports and
package `__init__.py` files) is inherently fuzzy, not a one-line regex bug.

### Problem 3 (separate, independently disqualifying): scale

Independently of grader accuracy: 5 of 6 non-scored calibration trials at
`max_steps=16` already hit the step ceiling attempting the ORIGINAL T7's
full scope (every file under `backend/app/services/`, recursively, cross-
referenced against all of `backend/app/`). This is a real scale problem
that a grader fix alone would not solve — the task's search space was too
large for either arm to complete within any budget this pass tested.

### Ruled out

The task did give Stealth a plausible opportunity (broad navigation +
repeated targeted search is exactly where structural-summary-before-full-
read and tool efficiency should matter) — the intended mechanism WAS
exposed, and the task was not contaminated by Better-Ways implementation
details (confirmed clear in the original task's own `leakage_check`). The
problems are specifically in ground-truth reliability and scope size, not
task relevance to the hypothesis.

## Decision

Per the coordinator's own decision rule ("if it cannot be repaired
honestly... retire T7... replace it with a new task that tests composition
using real, observable behavior"): retired. A grader patch alone (fix the
substring bug, keep the full-tree scope) would still leave problem 2
(residual re-export ambiguity) and problem 3 (scale) unresolved — not an
honest repair, just a narrower version of the same failure mode.

## Replacement: T7-v2

See `tasks.jsonl` (`task_id: "T7-v2"`) and
`ground_truth_t7v2_largest_function.py`. Statement: across every top-level
`.py` file directly under `backend/app/services/` (not subdirectories, 75
files), find the single function/method with the largest physical line
count. Grading is a pure AST `end_lineno - lineno + 1` measurement — zero
cross-file call-graph inference, so none of problems 1/2 above can recur by
construction. Real answer, independently computed this pass:
`record_execution_outcome` in `procedures.py`, 247 lines, a clean 10-line
margin over the second-place candidate (237 lines) — not a near-tie, so a
correct agent has a real, unambiguous target to find. Scope bounded to
top-level files only (not recursive) specifically to address problem 3.

This task was designed BEFORE any scored result exists for it — no
calibration or scored trial has been run against T7-v2 prior to writing this
document.
