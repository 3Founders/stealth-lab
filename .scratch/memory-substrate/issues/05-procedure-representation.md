# Procedure representation

Type: grilling
Status:
Blocked by: 01, 03

## Question

How is a first-class procedure represented, and what happens to the existing method library?

spec.md is emphatic that a procedure is **not** a previous trajectory: it must be parameterized (`edit(target_module)`, not `edit auth/middleware.py`), and instantiated against the current task/state. Required fields include `procedure_id`, `family_id`, name, goal, parameter schema, preconditions, required state, actions/steps, expected effects, postconditions, invariants, failure conditions, scope, exclusions, version, lifecycle status, verification statistics, evidence references, source episodes, provenance, timestamps.

The relevant existing facts:

- Procedures already exist as a **tagging convention over `task_nodes`**, not a table: `backend/app/services/method_library.py` writes rows with `created_by='htn_method_library'` and `provenance='prior_library'`, stuffing the decomposition into `io_schema` as `{"kind": "htn_method", "decomposition": [...]}`, with verification stats (`attempts`, `successes`, `mean_steps`, `times_reused`) in `success_criteria` JSONB.
- The read path (`find_reusable_plan` → `_pending_seed_plan` → `_seed_plan` → re-validated through `parse_dag`) and write path (`persist_plan`, gated on all subgoals done and none failed) are both real and both tested.
- **Neither is exercised by any default configuration.** `ResearchHTNAgent` is the only consumer and is not wired into the experiment runner; every recorded run shows `seeded_from_library: false`.
- Stored decompositions are concrete goal strings, not parameterized templates. There is no parameter schema anywhere in the repo.
- `reuse_detection.py` (thresholded cosine, 0.90 full / 0.70 partial) and `subtask_reuse.py` (shrink-only ChangeSet rewriting) are adjacent reuse mechanisms operating at different granularities.
- `task_nodes` already carries `io_schema`, `success_criteria`, `skill_ref`, cost/latency/PERT estimates, embeddings, bi-temporal columns and provenance.

Decide:

- Dedicated `procedures` table, or generalize the `task_nodes` convention into something explicit? Note that `task_nodes` currently means two different things — an execution/planning node in one run, *and* a stored reusable method — and spec.md's TARGET SEMANTIC SEPARATION says those are distinct concepts (TASK NODE vs PROCEDURE) that must not be collapsed. They are currently collapsed.
- What is the parameter schema's format, and where does it come from? Nothing today produces one.
- What is the relationship between a procedure and a procedure *family* (`family_id`)? spec.md wants families to capture invariant structure (locate → inspect → modify → validate → iterate) while concrete procedures capture domain realization.

Grill these:

- Parameterization is the whole thesis and the thing least supported by existing code. **What actually parameterizes a stored plan?** If it is an LLM pass over a successful trajectory, that is a semantic extraction step with all the replay/versioning obligations of the observation layer. If it is deterministic, by what rule?
- The existing library stores plans that are never used. Before designing a richer procedure model, what evidence is there that stored plans get reused *usefully*? Is a ticket needed to measure that first, or does the current non-wiring make the question unanswerable until the substrate exists?
- Does splitting `task_nodes` into procedure-vs-runtime-node break the existing hierarchy tree, method-library retrieval, and `subtask_reuse.py`, all of which query `task_nodes` directly? What is the compatibility story?
- spec.md forbids silently overwriting verified procedures. Does versioning ride the existing bi-temporal + `SUPERSEDES` machinery, or does a procedure need its own version chain? (Ticket 13 owns the lifecycle; this ticket owns whether the *representation* can carry it.)
