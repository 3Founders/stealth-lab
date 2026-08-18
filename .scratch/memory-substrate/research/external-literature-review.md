# External literature review: agent memory, TMS, procedural memory, provenance, retrieval

Not a Wayfinder ticket deliverable — no ticket asked this question, so it isn't indexed in
the map's Decisions-so-far. It's a standing reference, gathered via Perplexity against the
prompt in this session, for any ticket touching claim/procedure/provenance/retrieval design
(currently: 02, 03, 04, 05, 09, 10, 12, 13, 14, 18). Cited from `map.md`'s Notes so every
session picks it up the same way `inventory.md` already is.

Caveat carried over from the source: several 2026 items are arXiv preprints or evolving
specs, not settled research — flagged inline where that matters.

## The core pattern across the strongest sources

> Preserve immutable traces and episodes; derive mutable observations, claims, and skills
> from them; attach explicit provenance, confidence, validity intervals, and verification
> outcomes; retrieve only within a task-appropriate scope.

This is the same shape as the map's Destination and the six invariants already governing
this effort — the literature isn't introducing a new direction, it's corroborating and
sharpening the one already chosen.

## 1. Agent memory architectures

Core systems/papers: Generative Agents (Park et al. 2023 — memory stream → reflection →
retrieval), MemGPT/Letta (managed working-memory hierarchy with archival storage),
Reflexion (textual lessons stored separately from execution trace, revisable not
unquestionable), Voyager (skill library, promotion only after execution feedback), ExpeL
(AAAI 2024, post-task experience extraction without parameter updates), Mem0 (extraction
separate from consolidation/retrieval; reports ~91% lower p95 latency, >90% token savings
vs. full-context — authors' own benchmark, not a universal law), A-MEM (memories as linked,
revisable notes), MIRIX (six-way memory decomposition incl. a procedural store distinct from
episodic/semantic), **MemP (ACL 2026 Findings — peer-reviewed, most directly relevant)**:
distills trajectories into both fine-grained instructions and script-level abstractions, with
explicit build/retrieve/update/correct/deprecate loops.

**Promotion model** (synthesis, not from one source):

```
raw trace → episode → observation → candidate claim/procedure
  → verified claim/procedure → scoped durable memory
  → revalidated | superseded | deprecated | retired
```

Promotion criteria beyond salience: repetition across independent episodes, outcome quality
(task objective met, not just plausible), evidence quality (tied to tool/test/compiler
output), generalization across irrelevant-detail variation, contradiction retained (not
silently overwritten), explicit scope (repo/branch/language/deps/OS/user/session), and
verification via concrete tests/postconditions, not prose.

Key schema point: don't make "memory type" the only discriminator — track **epistemic
status** (candidate/verified/stale/superseded/retired) and **operational status**
independently. Directly actionable for ticket 13 (Procedure lifecycle/versioning).

## 2. Truth maintenance and belief revision

Classics: Doyle's JTMS (1979 — justification-based node/reason/retraction model, dependency-
directed backtracking), de Kleer's ATMS (1986 — beliefs supported under explicit *assumption
environments*, not globally true/false), Hansson (belief revision: consistency,
contraction/expansion), Brachman & Levesque (*Knowledge Representation and Reasoning*, book).

**Practical partial-TMS schema** (no theorem prover needed):

```
claim {
  id, proposition, status: active|unsupported|contradicted|superseded
  support_sets: [{node_ids, assumptions, rule_id}]
  contradiction_sets: [{node_ids, assumptions, rule_id}]
  depends_on, derived_from, confidence, valid_time, transaction_time
}
justification {
  conclusion: claim_id
  premises: [claim_id | observation_id]
  rule: observed | derived | tested | llm_inferred
  support_type: necessary | sufficient | defeasible
  assumptions: [...], evidence: [...]
}
```

Propagation rules: retracting a premise invalidates every derived claim whose justification
*requires* it; a claim with multiple independent sufficient justifications survives losing
one; a claim supported only under assumption set A is queried as "believed under A," not
globally true; both p and ¬p can be simultaneously supported — preserve both, mark the
conflict, resolve only via explicit policy/scope/verification, never silent overwrite; prefer
dependency-directed invalidation over global recomputation; keep retracted claims +
justifications for audit, never hard-delete.

This is close to `claims.py`'s existing design (`truth_state` IN/OUT without deleting
`t_invalid`) — the assumption-environment idea (de Kleer) is the piece not yet present: a
claim like "the migration command is safe" should be conditional on
`{repo, commit, branch, python_version, dependency_lock_hash, platform, tool_version}`, not
asserted unconditionally. Relevant to ticket 03's scope.

## 3. Provenance and justification graphs

Cheney/Chiticariu/Tan (2009 survey) — distinguishes *why-provenance* (which source tuples
witnessed an output) from *how-provenance* (the algebraic derivation path); Green/
Karvounarakis/Tannen (2007, provenance semirings) — compositional provenance through joins/
unions/filters; **W3C PROV-DM/PROV-O** (de facto interoperable standard) — model as Entity/
Activity/Agent with `wasDerivedFrom`/`used`/`wasGeneratedBy`/`wasRevisionOf`/
`wasInvalidatedBy`; PROV-O's qualification pattern attaches metadata (model, rule, timestamp,
confidence, role) to the edge itself, not just the node.

**Recommended graph shape** — four node classes (Artifact, Activity, Assertion, Agent), edge
types: `used, generated, derived_from, supports, contradicts, depends_on, revises,
supersedes, invalidates, verified_by, applies_to_scope`.

Key point for ticket 03/04: a claim shouldn't point directly to a source artifact — it should
point to a **justification activity** (e.g. `test_result --used--> procedure_version;
--generated--> verification_assertion; --supports--> claim`). This is what makes "a test
result was later found invalid" propagate correctly: invalidate the verification activity,
not the raw episode underneath it.

Versioning derived facts: never mutate a claim version in place — `C1v2` is
`revision_of C1v1`; mark the earlier version superseded/invalidated, preserve transaction
history. Matches the bitemporal pattern `knowledge_nodes`/`task_nodes`/`edges` already use.

## 4. Procedural memory, verification, staleness

**This is the section most load-bearing for ticket 05.** Voyager (addressable, independently
retrievable executable skill objects), Agent Workflow Memory (extract reusable workflows from
prior trajectories — precedent for procedure *induction* from traces rather than hand-
authoring), ExpeL, **MemP (ACL 2026 Findings)** — step-level instructions *and* script-level
abstractions, continuous correction/deprecation, POLYSKILL (generalizable hierarchical
skills), MemOS (procedural memory as a managed resource distinct from the planner's internal
decomposition), classical HTN literature (planning formalism — should *consume* procedures,
not define the only representation of them).

**The planner-neutral procedure object** — directly answers what ticket 05 has to decide:

```
Procedure {
  procedure_id, version, name, intent
  inputs, outputs
  preconditions, invariants, postconditions
  ordered_steps, optional_steps, branching_conditions
  failure_modes, recovery_hints
  required_capabilities, scope_constraints
  evidence_refs, verification_refs
  confidence, status
}
```

An HTN decomposition, a ReAct policy, a shell script, or a GUI action sequence is an
**execution binding** *of* a procedure, not the procedure itself:

```
Procedure
  ├── HTN binding
  ├── ReAct binding
  ├── tool-call binding
  └── human-readable binding
```

This is the concrete fix for the defect ticket 01's inventory already flagged:
`method_library.py` today makes a procedure *structurally identical* to an HTN decomposition
(`{id, goal, deps}` stuffed into `task_nodes.io_schema`) — no binding indirection at all.

**Verification status** — richer than a boolean: `candidate → observed_success → reproduced
→ verified_in_scope → verified_across_variants → stale → contradicted → superseded →
retired`. A verification record: `procedure_version, environment_snapshot, input_class,
test_oracles, observed_trace, postcondition_results, failure_observations,
verifier_model_or_human, verified_at, valid_until`.

**Staleness predicate** — directly actionable for ticket 12 (Applicability function) and
ticket 13 (Procedure lifecycle):

```
stale(p) = changed(D_p) ∨ failed(V_p) ∨ expired(T_p) ∨ contradicted(E_p)
```
where D_p = dependency set, V_p = verification evidence, T_p = validity interval, E_p =
evidence graph. Response options on drift: revalidate (scope still applies, dependency/env
changed), version (intent stable, implementation changed), retire (goal/tool/assumptions no
longer relevant), quarantine (contradictory/insufficient evidence — don't retrieve for
autonomous execution), repair (keep prior version, create a candidate successor from the
failure trace).

## 5. Local retrieval, temporal modeling, observability

**Retrieval**: ContextBench (2026, preprint) and Agent Retrieval Bench (2026, preprint) —
both directly relevant benchmarks for repo-scoped coding-agent retrieval (recall/precision/
efficiency over trajectories, not static similarity). Recommended cascade — matches
`hierarchy.py`'s existing beam descent plus `retrieval.py`'s RRF closely, worth checking
ticket 14 against this explicitly:

```
1. Hard scope filter (repo, commit range, branch, language, deps, session, user, tool)
2. Structural expansion (imports, callers/callees, tests, changed files, symbols)
3. Lexical + semantic retrieval (BM25, embeddings, graph proximity, recency)
4. Epistemic filter (verified, applicable, non-stale, non-contradicted)
5. Budgeted reranking (expected utility per token)
6. Context assembly (claims first, evidence on demand, raw traces last)
```

Retrieval unit shouldn't always be a raw chunk — often better as `claim + scope + confidence
+ one-line justification` or `procedure signature + preconditions + postconditions`.

**Bitemporal modeling**: Snodgrass (*Developing Time-Oriented Database Applications in SQL*,
book, canonical), Date/Darwen/Lorentzos (*Temporal Data and the Relational Model*, book),
XTDB docs (system/transaction time vs. valid/business time, retroactive + future-dated
changes), Datomic (immutable datoms, transaction identity). Recommended fields:
`valid_from, valid_to, recorded_at, superseded_at, last_verified_at,
next_revalidation_at, environment_hash` — note **verification time is distinct from valid
time** (a procedure can be valid Jan–Mar while last verified in Feb); don't collapse them
into one column.

**OTel GenAI observability**: corroborates ticket 06's already-closed decision almost exactly
— store OTel identifiers (`trace_id, span_id, parent_span_id, operation_name`, etc.) as
**provenance references**, map spans into this repo's own immutable entities (`OTel span →
raw event → episode → extraction activity → observation/claim/procedure`), and the
conventions remain actively developed/not fully stable as of 2026 — matches ticket 08's
finding that the GenAI semconv repo split out untagged and pre-1.0 in June 2026.

## Suggested implementation order (for reference once the map closes)

1. Immutable event/episode layer (ticket 06 — done)
2. Observation/claim layer with scope + confidence + contradiction edges (tickets 03, 04)
3. Lightweight justification graph — supports/contradicts/depends_on/revises/supersedes/
   invalidates, before attempting full ATMS behavior (tickets 03, 09 spillover)
4. Planner-neutral procedure layer (ticket 05)
5. Verification/drift layer (tickets 12, 13)
6. Scoped retrieval, measured on recall/precision/token cost/task success (ticket 14)
7. Temporal persistence refinements once graph semantics are stable

The literature's strongest warning: don't let "memory" collapse into a single vector index.
The target shape is a versioned evidence-and-skill graph with scoped projections — raw
traces immutable, claims/procedures derived versions, verification first-class, retrieval
returning only currently-applicable projections. This restates the map's Destination in
different words, which is the point of gathering it.
