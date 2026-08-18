# State model

Type: grilling
Status: resolved
Blocked by: 02

## Question

How are `state_before`, `state_after` and `state_delta` represented?

spec.md requires every meaningful episode to attempt S_before → execution → S_after. For coding, state may include repository, branch, commit, working tree status, relevant files, symbols, tests, build status, dependency state, issue/task state. It explicitly forbids snapshotting the entire world on every event, and requires relevant local state, references to immutable artifacts, deltas, hashes and temporal validity. The architecture must eventually support mobile/personal state, so state must not be hardcoded to code repositories.

The relevant existing facts:

- **No state model exists.** Nothing in the repo represents a before/after world state.
- The nearest analogues are all coding-specific and all ephemeral: `RepoSandbox` byte-fingerprinting in `htn_agent.py` to attribute `files_edited`, and `code_index.py`'s tree-sitter symbol extraction which is deliberately byte-exact and never LLM-summarized.
- Bi-temporal columns already exist on the graph tables and could carry state validity.
- `graph_ingest.py` stores up to 20 KB of raw patch text per row, which is the repo's only precedent for storing large content inline — and ticket 18 flags it as a privacy problem.

Decide:

- Is state a **snapshot** record, a **delta** record, or both? spec.md lists all three (`state_before`, `state_after`, `state_delta`), but storing all three per episode triples the write cost and creates a consistency obligation between them.
- What is the granularity — one state pair per episode, or per procedure execution, or per meaningful segment?
- How is "relevant local state" scoped? The whole point is not snapshotting the world, so something must decide what is relevant. Is it derived from the episode's touched artifacts, from the procedure's declared required-state, or supplied by the domain adapter?
- What is the immutable-artifact reference format — content hash, git object SHA, blob-store URI? Commits and blobs are already immutable and addressable; test output and build output are not.

Grill these:

- **Is a genuine world-state model needed in milestone 1, or is state actually just "the facts an applicability check reads"?** Ticket 12 needs `applicability(P, S_current)`. If the only consumer of state is the applicability check, then state should be shaped by what that check queries — not by an aspiration to represent the world.
- Domain-neutrality is the hardest constraint and the least immediately useful. What is the concrete cost of writing a coding-specific state model now and generalizing when a second domain arrives? Compare against the `tenant_id` precedent.
- Temporal validity on state is strange: state is *by definition* time-sensitive claims about the current situation (spec.md's own definition of STATE). Does that mean state *is* a claim with a short validity window, and there is no separate state table at all? Grill this — it would substantially simplify the model, and it follows directly from spec.md's own definitions.
- What is captured when state is unavailable or partial? spec.md's testing section explicitly names "missing state" and "partial state" as cases, so absence must be representable rather than assumed.

## Answer

Grounded in [research/answers1.md](../research/answers1.md) (Brief 1 findings: projection
viability, state-as-time-scoped-facts, index shape, unknown-vs-false, artifact refs).

**Core representation: no state table. State is a read-time projection over the claim graph.**
`state_before`/`state_after` are the same query evaluated at two timestamps, scoped to a subject
set; `state_delta` is the set difference, computed on demand and never stored.

This is a **recognized modelling stance, not a novel bet** — the findings confirm it directly:
"state at T = facts whose validity interval contains T" is the event-calculus frame axiom
operationalised as a query, and Reiter's successor-state axioms give exactly
`state_after = state_before ∪ adds − deletes` without storing snapshots. Datomic is the
production precedent (immutable datoms, "current state" as a derived view, never a stored table).

**Granularity is not a storage decision.** Since state is a function of `(scope, timestamp)`, an
episode's state and a procedure execution's state are two evaluations of one function. Nothing is
stored redundantly and there is no granularity to lock in.

**Scoping**: episode-level scope comes from the episode's `domain_payload` (ticket 02) — e.g.
`files_touched`; procedure-execution scope comes from the procedure's `required_state` (ticket
05). Ticket 12's `applicability(P, S_current)` calls the *same* function with `P.required_state`
as scope and now as timestamp — one implementation serves both.

**Domain-neutrality is free.** Claims are already fully generic (ticket 03: no `domain` split).
Since state *is* claims, there is no coding-specific state schema to later generalise; domain
specificity lives entirely in what gets asserted, via the episode's `domain_payload`.

### Index shape — correcting an earlier draft of this answer

An earlier draft specified a composite btree on `(subject, t_valid, t_invalid)`. **That is wrong
for this query shape**: btree cannot efficiently serve range *containment*, which is the entire
access pattern. The findings are unambiguous — GiST over a range type is required, with reported
10-50ms as-of lookups where btree alone is inadequate.

But the existing schema stores `t_valid`/`t_invalid` as separate scalar `TIMESTAMPTZ` columns on
`knowledge_nodes`, `task_nodes`, `edges` and `agents`. Migrating those to `tstzrange` would be
large and would reopen ticket 17's resolved migration decision. **Decision: neither migrate nor
add a generated column — use a composite partial GiST expression index, which requires zero
schema change:**

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE INDEX idx_claim_subject_validity ON knowledge_nodes
  USING gist ((properties->>'subject'), tstzrange(t_valid, t_invalid))
  WHERE node_type = 'claim';
```

`btree_gist` is what allows the scalar equality component and the range component in one index.
Two implementation notes for whoever builds this: (a) verify `tstzrange(timestamptz, timestamptz)`
is `IMMUTABLE` in the target Postgres version — if it is not, fall back to a
`GENERATED ALWAYS AS ... STORED` range column, which is still additive and still breaks nothing;
(b) this does **not** replace ticket 03's plain btree on `(properties->>'subject')` — that one
serves "all claims about subject X regardless of time", this one serves time-scoped projection.
Both are wanted.

### No exclusion constraint — it would forbid the case we are required to keep

The findings recommend `EXCLUDE USING gist (subject WITH =, valid_during WITH &&)` to prevent
overlapping validity per subject. **Rejected**, for two reasons:

1. As stated it is simply wrong here — a subject legitimately carries many simultaneous claims
   under *different* predicates (`middleware.py` has a live `content_hash` claim and a live
   `last_run_outcome` claim at once). It would have to be keyed `(subject, predicate)` at minimum.
2. Even correctly keyed it conflicts with a hard requirement: when both `p` and `not p` are
   supported, the system must **preserve both and mark the conflict**, never reject the write. A
   database constraint would make the contradiction case unrepresentable — precisely inverting
   the intended behaviour.

Contradiction detection stays in application code, where it can record the conflict rather than
refuse it. `claims.py`'s existing `relate_claims()` (`CONTRADICTS` edge + `truth_state` flip) is
already the right mechanism.

### The event-sourcing materialization threshold does not transfer as stated

The findings report a practitioner threshold of ~100-250 events per aggregate, or replay >100ms.
**That threshold exists because event sourcing reconstructs state by replaying N events — an O(n)
operation. This design does not replay.** A projection query is an indexed range-containment
lookup: O(log n) in total row count, with fan-out proportional to the number of subjects in
scope, not to the history depth of any one subject. A file with 300 historical `content_hash`
claims costs one index probe to resolve at time T, not 300.

So the aggregate-count threshold is largely inapplicable, and quoting it here would have been
cargo-culting. What *does* transfer is XTDB's narrower warning, which is about query *shape* and
stands unchanged: as-of-now and as-of-T point queries short-circuit cheaply; **full-history
materialization and temporal range joins explode**. Milestone 1 needs neither.

**Decision**: no materialization in milestone 1. Monitor p95 projection latency; the trigger for
revisiting is a latency SLO breach (>100ms p95), not a row count. If materialization is ever
needed it is reserved for temporal-range and full-history queries, not for point-in-time reads.
Aggregate unit, if the question ever becomes live, is `(subject, predicate)` — the pair whose
successive versions form one logical history.

### Closed-world now, with a non-foreclosing escape hatch

Missing state is an empty result set; partial state is some-but-not-all expected subjects
returning claims. No null sentinel, no special-casing. This is the **closed-world assumption**,
adopted deliberately.

The findings warn that CWA is regretted if open-world reasoning is later needed — which is a live
risk here, because the deferred ATMS "assumption environment" idea (a claim believed only under
`{repo, commit, branch, dependency_lock_hash}`) is OWA-flavoured. The escape hatch is the
findings' own recommendation, and it is cheap: **if "unknown" is ever needed it becomes an
explicit fact type — a status value on a claim — never a NULL sentinel.** That is purely
additive (a new value in an already-validated field), not a migration, so CWA now does not
foreclose OWA later.

The SQL three-valued-logic literature is the cautionary tale being avoided: NULL propagation
through boolean expressions produces the well-known paradoxes and is a documented source of
application errors. Two-valued logic with explicit facts sidesteps it entirely.

This is also consistent with ticket 12's fail-closed stance on unknown preconditions: under CWA,
absence is determinate, so "no claim found" and "precondition not satisfied" are the same answer.

### Epistemic status must be tagged — extends ticket 03's registry

The findings' one substantive warning about unifying durable belief with transient world-state:
they have different lifecycle and provenance needs, so a unified store **must tag each fact with
its epistemic status**. This design unifies them, so the warning applies directly.

Ticket 03's claim schema carries `truth_state` and `confidence` but no such tag — meaning
`content_hash = abc123` (read straight off a trace event) and `user runs tests after auth edits`
(a model-inferred generalisation) would be indistinguishable rows. **Decision: add
`epistemic_status` to the claim properties**, values `observed` (deterministically derived from
trace events) vs. `inferred` (semantically extracted, model-derived).

This does not reopen ticket 03. That ticket established `NODE_TYPE_SCHEMAS` as a write-time
validation registry precisely so the claim payload's field list could be defined and extended;
adding a validated field is the intended extension path. `ClaimProperties` gains
`epistemic_status` alongside `subject, predicate, object, truth_state, claim_type, confidence,
extraction_version`. Ticket 04 owns how the value is assigned, since observation-vs-inference is
that ticket's subject.

### Artifact references: typed union, not a bare string

Git-native SHAs for git objects (commits, blobs — already immutable and addressable), reusing
`episodes.content_ref`'s existing storage-pointer idiom for the rest. But per the findings, the
reference must be a **typed union that names its addressing scheme** —
`GitSha | BlobUri | DbId` — rather than an untyped string mixing modes. Nix and Bazel are the
cited precedents (content-address small artifacts; large artifacts to blob storage under a
CAS-style URI with the hash recorded). Mixing addressing modes without a discriminator is what
produces cache-invalidation bugs and unresolvable provenance.

### Provenance of this answer

Literature-grounded: state-as-time-scoped-facts (event calculus, situation calculus, Datomic),
GiST-over-btree for range containment, CWA/OWA tradeoff and the NULL cautionary tale, typed
artifact references, the epistemic-status requirement. Judgement calls, flagged as such by the
findings themselves: whether to unify belief and world-state at all (no empirical study warns
against it — the risk is named, not measured), and the artifact-reference scheme (no comparative
study exists for agent-memory systems). Reasoned departure from the findings: the
materialization threshold, rejected as non-transferable because this design indexes rather than
replays.
