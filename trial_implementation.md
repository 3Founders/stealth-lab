# Trial Implementation — from `schema.md` to a working system

Companion documents: `schema.md` (data structures + connection map),
`verified_procedural_experience_system_ideal_specification_v4.md` (spec).

This document answers three questions in order: what do we already have,
what is broken or missing in it, and what systems we must add — organized as
four phases whose single organizing constraint is **minimizing migration cost**.
Every structural decision that is cheap today and brutal at scale is front-loaded
into Phase I; every expensive subsystem arrives as late as possible, arriving as
configuration of shapes rather than rewrites.

Status markers used below: **shipped**, **partial**, **absent**.

---

## Part A — What exists today

### Experience layer
Trace ingestion is real and battle-tested against live sessions: `agent_traces`
and `trace_events` (`db/12_trace_ingestion_pipeline.sql`) carry a canonical event
model with deterministic `dedup_key` idempotency, raw-payload pointers for large
outputs, and a SKIP LOCKED job queue (`ingestion_jobs`). Episodes exist as a table
with bi-temporal tombstones and project/session/parent columns
(`db/17_episode_project_columns.sql`), but episode *assembly* is still the ticket-11
prototype in `experiments/episode_assembly/` — three rule sets measured over 36
real sessions, findings recorded, no production segmenter shipped. **Partial.**

### Knowledge layer
The graph core is a bi-temporal polymorphic store: `knowledge_nodes`,
`task_nodes`, `edges` with `t_valid`/`t_invalid` on everything
(`db/01_ontology.sql`). Retrieval over it is genuinely hybrid — pgvector + FTS
fused by reciprocal-rank fusion plus bounded graph expansion
(`services/retrieval.py`) — extended by local-first structural/temporal tiers
(`services/local_retrieval.py`). The state projection works and is honest about
its one known limitation (`services/state.py`; superseded claims vanish from
historical projections rather than showing stale values).

The gap sits in the Claim shape itself. Claims are `knowledge_nodes` rows with
subject/predicate/object inside JSONB `properties`, keyed by UUIDv4, with no
proposition typing, no belief field, no status machine, and no scope column.
`ChangeSet` (`models/change.py`) exists but only serves the debate flow over
task/knowledge nodes — it is not the universal mutation record `schema.md §19`
now requires. Observations have a table and writer (`14_observations.sql`,
`observations.py`) but revisions do not propagate anywhere. Evidence has **no
first-class existence**: verification statistics accumulate directly on procedure
rows, which is why the banking seed's synthetic outcomes silently contaminate any
capability claim made from them. ClaimFamily resolution does not exist at all.
Reviews exist only inside the debate pipeline.

### Procedure layer
This is the strongest layer. The `procedures` table (`18_procedures.sql`) plus
migration 20 carries versions, lifecycle state, approval status, capability
statements, preconditions, scope/exclusions, failure conditions, and an
`invariants` column now wired into retrieval. Extraction runs end-to-end:
evidence collection → derive (deterministic) → one bounded LLM call for exactly
two generalization fields → validators V1–V5 → capture
(`procedure_extraction/`). Applicability is a non-compensatory filter cascade
with fail-closed preconditions under CWA, cold-start gate, approval gating, and
numeric invariant checking via a whitelisted AST→z3 bridge (`applicability.py`,
`invariants.py`). Slot binders give steps structural tool bindings.

What is missing here is correctness polish, not structure: derived preconditions
are over-constrained (every live claim at extraction time becomes a gate, so the
library self-obsoletes as environments drift); z3 runs synchronously on the MCP
event loop with no solver timeout; malformed invariants have no authoring-time
validator (V6) and so permanently disqualify their row at retrieval time; and
`find_applicable_procedures` re-queries `project_state()` per candidate row.

Implementation routing (§23 of the spec — choose the cheapest implementation
that clears capability) does not exist; the README already lists it as unbuilt.
Capability today is success/failure counters on the procedure row, not the
conditional P(outcome | …) with levels 0–5 the spec defines.

### Execution layer
The HTN planner/executor (`execution/htn_agent.py`) implements the Task DAG
exactly as spec'd — planner-neutral steps, DAG-local scheduling, fresh context
per node, localized replanning — but plans are in-memory per run and not
persisted as ExecutionPlan/TaskGraph objects, so executions cannot reference an
exact plan version. `record_execution_outcome()` closes the outcome loop for
procedures but nothing binds outcomes to evidence records with independence
groups. The τ²-bench integration (`vendor/tau2-bench`: `stealthlab_bridge.py`,
`substrate_tracker.py`) proves the substrate serves external agents end-to-end.

### Governance layer
Visibility predicates and tenant scoping run through access control
(`access.py`) and governance tables, debate approval provides human-in-the-loop
for claims, and ChangeSets exist for that flow. But identity is an unverified
header (README's own blocker list), policies are not versioned rules, and the
TMS is claim-centric conflict detection (`knowledge_conflict.py`,
`temporal_conflict.py`) rather than the typed-edge dependency queue §20 now
specifies.

---

## Part B — Problems, prioritized

**P0 — trust-correctness (fix before any corpus grows).**
Provenance is mislabeled at the source: `Onboarder.seed()` hardcodes
`'company_ingested'` for third-party benchmark corpora, and auto-created proxy
nodes carry `'company_debate'` before any debate ran. No graph table has a scope
column, while `schema.md §3` now mandates scope on every entity — this is both a
correctness gap and the shard key our entire scale path depends on. The claim
object diverges from its own specification (JSONB SPO vs proposition/belief/
status). Synthetic verification evidence sits in the same lineage as real
outcomes with nothing distinguishing them. Identity is an unverified header.

**P1 — scale-correctness (fix before row counts grow).**
Over-constrained derived preconditions quietly shrink the reusable library.
z3 blocks the event loop, has no solver timeout, and accepts malformed
expressions that then kill their procedure forever. N+1 `project_state()` calls
per retrieval request. Cold-start gate ignores tenant scoping. UUIDv4 primary
keys guarantee index bloat under insert load. Embedding vectors carry no model
identifier, making model transitions an operational gamble (the migration-11
lesson, still unstructuralized).

**P2 — missing subsystems.**
Dependency queue with typed edges (TMS). ClaimFamily resolver. Capability
computation from outcome streams. Universal ChangeSet application across all
versioned objects. Production episode segmenter. Versioned policy objects.
Real identity.

---

## Part C — Four phases, each with a migration-cost ledger

Ordering principle: **one-way doors first, expensive machinery last.** Anything
that is an `ADD COLUMN` on a small table today happens in Phase I even if the
logic that uses it ships later — because the identical change at tens of
millions of rows is a table rewrite.

Effort markers: **S** ≤ a session · **M** days · **L** weeks.

---

### Phase I — Contract hardening
*Now. Single Postgres. Current corpus (~700 procedures + small graph).*

1. **Scope columns everywhere** — add `scope_type` / `scope_entity_id` to
   knowledge_nodes, task_nodes, edges, procedures, observations, episodes,
   agent_traces (traces already have project_id). Ingestion adapters reject
   scope-less payloads (the V0 validator). Entities managed: all of them —
   this is the §3 mandate and the future shard key. **S–M.**
2. **Real predicate/object columns alongside JSONB** — extracted from
   properties on write, dual-read during transition, JSONB retained for
   extensibility. Never drop the JSONB. Entities managed: Claim, State.
   **S.**
3. **UUIDv7 for new rows only** — default changes forward; existing ids stay.
   Entities managed: every table. **S.**
4. **Embedding provenance stamping** — `embedding_model_id`, `embedding_dim`
   columns; backfill current rows as `gemini-embedding-001@1024`. Entities
   managed: Claim, Procedure embeddings. **S.**
5. **Provenance as parameter + P0 fixes** — seed paths pass explicit
   provenance; proxy-node stamping corrected to a neutral value until review
   completes. Entities managed: Source, Review, Claim. **S.**
6. **Extraction correctness bundle** — precondition relevance filter (derive
   gating facts, not every live claim); V6 authoring-time invariant validator;
   z3 moved off the event loop with a solver timeout; memoized
   `project_state()` inside the applicability cascade; tenant-scoped
   cold-start gate. Entities managed: Procedure, ApplicabilityRule,
   Capability inputs. **M.**

**Migration-cost ledger:** six additive migrations over ~700-row tables;
minutes each. This phase purchases the cheap path for Phases II–IV: scope
makes later sharding mechanical, append-only discipline makes partition
activation movement-free, stamped models make embedding transitions routine.
Skipping it is how every one of these becomes a rewrite instead.

*Exit criteria:* zero scope-less writes accepted; retrieval precision unchanged
or better after precondition filter (gold-set A/B); all migrations applied to a
production-shaped copy in <1 hour.

---

### Phase II — Trust completion
*First real corpus and users. Still one Postgres.*

1. **Universal ChangeSet** — extend `models/change.py` reach to observations,
   procedures, implementations, applicability rules, states; observation
   revisions enqueue dependents per §20. Entities managed: every `[V]`
   object. **M.**
2. **Evidence as a first-class table** — typed rows with independence groups;
   procedure stats become *views over evidence*, ending synthetic/real
   commingling; backfill marks banking-seed outcomes with a synthetic flag.
   Entities managed: Evidence, Capability inputs. **M.**
3. **Capability computation** — levels 0–5 derived from outcome streams with
   bidirectional demotion on failure; replaces raw counters as the routing
   input when Phase IV needs it. Entities managed: Capability, Implementation.
   **M.**
4. **Production episode segmenter** — promote the prototype's Rule-A +
   merge/subdivide rules into `trace_worker`. Entities managed: Episode.
   **M.**
5. **ClaimFamily resolver v0** — scoped-per-project blocking + proposition
   match; cross-project families deferred. Entities managed: ClaimFamily.
   **M.**
6. **Identity decision gate** — pick real authN (even OIDC-only) before any
   multi-user exposure. Entities managed: User/Agent, Permission. **L** if
   done properly, and deliberately scheduled here rather than Phase IV
   because permissions semantics block publication features.

**Migration-cost ledger:** purely additive — new tables and new services over
Phase I columns; no backfills of existing data beyond flagging synthetic
evidence. Nothing here will be undone by Phase III or IV.

*Exit criteria:* every mutation of a `[V]` object produces a ChangeSet record;
capability scores traceable to non-synthetic evidence; two episodes assembled
end-to-end from real traces without manual boundary fixes.

---

### Phase III — Scale posture
*Tens of millions of rows. Postgres remains the system of record.*

1. **Partition activation** — declarative partitions on `(scope, t_valid)` for
   the big tables; ATTACH-based, no data movement, enabled by Phase I's
   append-only discipline and scope columns. Entities managed: Claim, Event,
   Evidence storage. **M.**
2. **Read replicas** — retrieval reads fan out; writes stay primary.
   Entities managed: read paths for all layers. **S.**
3. **OLAP rollup store beside Postgres** — ClickHouse or DuckDB-over-Parquet
   fed incrementally from append-only evidence/outcomes; capability analytics
   and family clustering leave OLTP entirely. Entities managed: Capability,
   Evidence analytics. **M–L.**
4. **TMS dependency-queue service** — typed-edge index (`depends_on`,
   `derived_from`, `supported_by`, `requires`) with lazy mark-stale /
   validate-on-touch; the lazy design is chosen *because* eager propagation
   is unbounded at this tier. Entities managed: propagation for all `[V]`
   objects. **L.**
5. **ClaimFamily LSH blocking pipeline** — scales family resolution past
   project boundaries. Entities managed: ClaimFamily. **M.**
6. **Ingestion worker fleet + Parquet cold tier** — batched idempotent workers
   consume the job queue; logical replication exports cold history outward
   without touching the hot path. Entities managed: Event/Trace/Evidence
   retention. **M.**

**Migration-cost ledger:** the theme is *activation, not migration* —
partitions attach, replicas replicate, rollups compute incrementally outward,
cold tiers copy from replicated streams. Zero historical-data movement is the
design constraint, inherited from Phase I's shape.

*Exit criteria:* p95 retrieval latency stable under 10× row growth; TMS
re-evaluation queue drains faster than it fills; OLTP CPU share of analytics
workloads ≈ 0.

---

### Phase IV — Distribution
*Hundreds of millions of rows and beyond.*

1. **Shard-by-scope activation** — Citus-class distribution or federated
   per-project clusters; mechanical *only because* every row has carried its
   scope key since Phase I. Entities managed: all layers, physically. **L.**
2. **Distributed edge-store decision gate** — evaluate NebulaGraph-class
   engines for multi-hop traversal *only if* cross-scope traversal becomes a
   measured hot path (current workloads say it is not); otherwise federated
   relational continues. Explicitly a decision point with a measurement
   requirement, not a default adoption. Entities managed: Claim graph edges.
   **L** (evaluation) / deferred (adoption).
3. **Dedicated ANN cluster** — global-tier vector search splits from OLTP
   storage entirely; selective-embedding policy governs what earns a slot.
   Entities managed: Claim/Procedure embeddings at global scope. **M–L.**
4. **Streaming ingest backbone** — Kafka-class buffer between collectors and
   workers; absorbs burst and decouples regions. Entities managed: Event
   ingestion. **L.**
5. **Policy engine + authorization service** — versioned policies evaluated
   at plan-time and execution-time; identity from Phase II extended with
   delegation rules ("a procedure never grants more authority than its
   invoking user"). Entities managed: Policy, Permission, Procedure
   execution. **L.**
6. **Multi-region posture + residency** — scope-aware placement (residency is
   just scope type `organization`+region taken seriously). Entities managed:
   Permission, storage layout. **L.**

**Migration-cost ledger:** every item configures a shape that has existed
since Phase I/II — sharding activates keys, streaming buffers a queue that
already exists, the ANN cluster serves stamps that were always present, the
policy engine consumes ChangeSets and scopes that were already universal.
The phase ordering itself is the cost-reduction strategy: nothing here can
be built "early" without building it twice.

*Exit criteria:* shard rebalancing demonstrable without downtime; cross-region
scope enforcement test passing; ingestion sustained above peak collector
burst with bounded lag.

---

## Sequencing summary

Phase I buys cheap futures with minute-sized migrations. Phase II completes
trust semantics additively on those shapes. Phase III activates scale posture
without moving history. Phase IV distributes configuration, not rewrites.
The single biggest risk to this plan is starting Phase III work before Phase I
lands — every skipped contract-hardening step converts a later `ATTACH` or
`CREATE DISTRIBUTION` back into a rewrite.
