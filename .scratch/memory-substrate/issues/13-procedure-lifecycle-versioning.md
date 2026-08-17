# Procedure lifecycle and versioning

Type: grilling
Status:
Blocked by: 05

## Question

How do procedure lifecycle states, failure classification and versioning work?

spec.md forbids a simple boolean `verified`. It wants CANDIDATE → VERIFIED → STALE → REVALIDATED → VERIFIED, or RETIRED. Failures must be classified into six kinds — transient/contextual, precondition violation, scope violation, environment/dependency change, structural/procedural, and ambiguous — with explicitly different consequences, and a verified procedure must never be automatically rewritten after one failure. Versioning must never silently overwrite: retain the prior version, the reason for revision, the evidence that triggered it, the source execution, the validity interval and the migration relationship. A changed dependency marks a procedure STALE rather than deleting it.

The relevant existing facts:

- The method library's entire notion of verification is JSONB counters in `success_criteria`: `attempts`, `successes`, `mean_steps`, `times_reused`, `internal_proxy`. `_bump_reuse_count` is a counter, not a reward signal — its own source says so.
- The write path is gated on `subgoals_done > 0 and subgoals_failed == 0`, which is a boolean success test with no failure classification at all.
- `ResearchHTNAgent` item 5 — the Beta-Bernoulli bandit that would turn the reuse counter into a real reliability signal — raises `NotImplementedError`.
- There *is* strong existing precedent for non-destructive versioning: `knowledge_update.py` closes the validity window, appends a new row, and writes a `SUPERSEDES` edge, all in one transaction with `SELECT ... FOR UPDATE`. It never deletes.
- `claims.py` establishes the useful orthogonality that lifecycle can reuse: `t_invalid` (deleted / no longer valid in world time) and `truth_state` (`IN`/`OUT`, no longer believed) are independent axes.
- The `agent_review_state_machine.py` is a working precedent for an explicit transition table with row-locked transitions and an immutable append-only event log.

Decide:

- Does the lifecycle ride the existing bi-temporal + `SUPERSEDES` machinery, or does a procedure need its own version chain and status column?
- Is lifecycle status a column, or is it derived from evidence? Deriving it is more honest (spec.md: justification is canonical, confidence derived) but makes every read a computation.
- What is the transition table, and what triggers each transition? Specifically: what *promotes* CANDIDATE to VERIFIED — a count of successes, a count of distinct contexts, an explicit human action?
- How is a failure classified, and by what? Six categories is a rich taxonomy for something with no classifier. Which are determinable deterministically (precondition violation is checkable; environment change is detectable from state deltas) and which need judgement?

Grill these:

- **What marks a procedure STALE when a dependency changes?** That requires knowing the procedure's dependencies — which is the claim/procedure dependency graph spec.md's TMS-preparation section describes. Is that graph in scope for milestone 1, or does STALE start as a manual/explicit state with the automatic path deferred?
- Ambiguous failure must "not automatically mutate durable memory." What happens to it instead — recorded and ignored, queued for human review, or counted toward a threshold? An unhandled sixth category is where this design quietly leaks.
- A verified procedure that fails repeatedly but never in a classifiable way: what is the escape hatch that stops it being reused forever?
- `agent_review_state_machine.py` is a working precedent in this repo. Should procedure lifecycle reuse that pattern outright, or is procedure state fundamentally different from review state because it is evidence-driven rather than actor-driven?
