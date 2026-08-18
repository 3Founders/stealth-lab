# Procedure representation

Type: grilling
Status: resolved
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

## Answer

**Dedicated `procedures` table.** Resolves the collapse spec.md's TARGET SEMANTIC SEPARATION
forbids: TASK NODE (execution/planning node in one run) and PROCEDURE (reusable, verified
capability) are currently the same `task_nodes` row distinguished only by tag convention
(`created_by='htn_method_library'`). Procedures need structure `task_nodes` was never shaped
for — parameter schema, verification statistics, lifecycle status, family grouping,
invariants — and cramming that into `io_schema`/`success_criteria` JSONB is exactly the
"procedure is not a previous trajectory" anti-pattern spec.md names. Matches the literature's
planner-neutral `Procedure` object (external-literature-review.md §4) and the test ticket 03
established (distinct shape/volume/lifecycle → dedicated table, not tag-based).

`procedures` carries: `procedure_id, family_id, name, goal, parameter_schema, preconditions,
required_state, steps, expected_effects, postconditions, invariants, failure_conditions,
scope, exclusions, version, lifecycle_status, verification_stats, evidence_refs,
source_episode_ids, provenance, created_at, updated_at`, plus the standard bitemporal columns
(`t_valid/t_invalid/t_created/t_expired`) every other core table has, and — per ticket 02 —
`domain`/`domain_payload` for the coding-specific realization of `scope`/`required_state`
(procedure is one of the concepts ticket 02 named as eventually needing this split).

**Execution binding, not embedded plan.** A stored HTN decomposition (or any other planner's
output) is an **execution binding** *of* a procedure, not the procedure itself — the same
indirection the literature review's §4 documents (`Procedure → HTN binding | ReAct binding |
tool-call binding | human-readable binding`). This is the concrete mechanism satisfying the
Procedure≠HTN invariant: `steps` on the procedure is planner-neutral (ordered/optional
steps, branching conditions), and an HTN-specific binding table (owned by ticket 15, HTN
relocation) translates a procedure into a concrete `Node`/DAG at instantiation time — the
procedure itself never contains `deps`/`requires` HTN scheduling fields.

**Parameterization: both extraction methods get built, not deterministic-only.** Two
pluggable extraction strategies:

- **Deterministic** — any file path/test path/symbol name that appeared as a tool-call
  argument in the source episode becomes a named parameter slot, role (edit-target vs.
  test-target) inferred from which tool touched it. No LLM call.
- **LLM-based** — a model pass over a successful trajectory proposes the parameterized
  template directly.

The caller picks which strategy runs at extraction time — no automatic heuristic selection.
Because the LLM path is a real semantic-extraction step, `parameter_schema` carries the same
versioned-extractor discipline ticket 06 already established for `trace_events`:
`{"slots": {...}, "extraction_method": "deterministic_v1" | "llm_pass_v1", "extractor_version": "..."}`
— satisfies the replayability invariant regardless of which method produced a given
procedure's parameters, and makes the two strategies' actual usefulness comparable later
without a schema change.

**No blocking measurement ticket.** Reuse can't be measured without a substrate to reuse
from — chicken-and-egg. Instead, `verification_stats` (`attempts, successes, times_reused,
mean_steps` — carried forward from `method_library.py`'s existing fields) makes effectiveness
measurable once wiring happens, without redesigning the schema later. Matches spec.md's own
A/B/C/D benchmark design, which already expects this instrumentation.

**Compatibility: additive, not a breaking replacement.** `hierarchy.py` and
`subtask_reuse.py` keep operating on `task_nodes`/`knowledge_nodes` unchanged — that's runtime
dedup/similarity, a different concern from procedure applicability (ticket 12's job, reading
from `procedures` instead). Existing `htn_method_library`-tagged `task_nodes` rows become
**legacy candidate procedures**, migrated into `procedures` by ticket 17 with a
`migrated_from_task_node_id` provenance pointer — never silently dropped, per spec.md's
MIGRATION section.

**Versioning rides the existing pattern, not the existing table.** `procedures` reuses the
same bitemporal columns plus `procedures` added as a valid `source_table`/`target_table` on
the polymorphic `edges` table, so `SUPERSEDES` works identically to claims (ticket 03). Ticket
13 owns the actual lifecycle state machine; this just confirms the representation carries it
without inventing a new mechanism.

**`family_id`: self-referencing column on `procedures` itself**, not a separate table. A
family is just another `procedures` row (flagged non-directly-executable via
`lifecycle_status`), and concrete procedures point to it via `family_id`. Mirrors
`hierarchy.py`'s existing precedent (abstract grouping nodes live in the same table as
concrete leaves). Explicitly distinct from Behavioral Motifs (out of scope this milestone,
per the map): a family is same-domain structural grouping; a motif is the later,
cross-domain, hypothesis-only pattern.
