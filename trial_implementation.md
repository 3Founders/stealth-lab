# Trial Implementation from `schema.md` to a working system

> **SUPERSEDED by `ROADMAP.md`** — bands replace phases; the fresh-start ruling
> (2026-08-24, see ROADMAP) discards trial-era data and absorbs former Phase II items
> into the initial build. Retained for its current-state audit (Parts A/B) and
> migration-cost reasoning; do not execute its phase sequence directly.

Companion documents: `schema.md` (data structures + connection map),
`verified_procedural_experience_system_ideal_specification_v4.md` (spec).

This document answers three questions in order: what do we already have,
what is broken or missing in it, and what systems we must add — organized as
four phases whose single organizing constraint is **minimizing migration cost**.
Every structural decision that is cheap today and brutal at scale is front-loaded
into Phase I; every expensive subsystem arrives as late as possible, arriving as
configuration of shapes rather than rewrites.

Status markers used below: **shipped**, **partial**, **absent**.

> **Status update (2026-08-24) — fresh-start ruling supersedes parts of this document.**
> All data currently in the system is trial-era and will be discarded, not migrated.
> Part A remains accurate as a code inventory and Part B stands as
> mechanism-correctness findings, but Part C's migration-cost ledgers now bind
> **future growth only**: against an empty database every Phase I change is free;
> transition machinery (dual-read shims, backfills, synthetic flags) is dropped
> entirely; and Phase II's evidence table, universal ChangeSet, and capability
> computation join the initial build. The one-way-door logic still applies — scope
> keys, plan persistence, and embedding stamps must exist before real volume does.
> Plan of record: `ROADMAP.md`.

---



## Part A What exists today

> Implementation depth for every subsystem (technology choices, data flow,
> per-subsystem analysis) lives in `current_stack.md`. This section records
> functional status only.



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

---



## Part D — Research-grounded discussion (2026-08)

External literature mapped onto Parts A–C. Sources: arXiv primary reads,
ACL/EMNLP/ICML proceedings, Anthropic engineering posts, and live
rate-limit measurements against our own serving stack. Each item states
findings, then the mapping to specific phase items. Provenance trail lives
in `docs/READING_LIST.md`.

### D.1 Context engineering — why the render constants exist (anchors Part A execution layer)

1. **Context rot / premature termination**
  (`arXiv:2606.29718`). Under long trajectories, models give up or submit
   uncertain wrong answers long *before* exhausting the context window; the
   give-up rate correlates with trajectory length even when task difficulty is
   controlled. Seven context-management methods studied; all behave as
   test-time scaling, and the right method is model-dependent: strong agentic
   models want isolation (sub-agents), weaker ones want compaction +
   trimming.
2. **Length hurts independent of distraction** (`arXiv:2501.01880`
  lineage; EMNLP 2025 Findings "Context Length Alone Hurts"). With retrieval
   perfect and distractor tokens literally masked out of attention, accuracy
   still drops 13.9–85% as input grows within claimed windows. Sheer input
   length is a causal factor, not just retrieval failure.
3. **Multi-hop fragility under expansion** (`arXiv:2603.15723`). Multi-hop QA
  degrades roughly twice as fast as single-hop under identical irrelevant-
   context growth — compounding across reasoning steps.

*Mapping.* Our observed "step abandonment" failure class in early τ² phases
matches item 1's premature-termination signature. The `_MAX_STEP_CHARS=600`
truncation and top-5 detailed-procedure cap in `stealthlab_bridge.py` are
empirical instances of item 2's mitigation (shorten inputs); the phaseI→J
score improvement after truncation is consistent with its mechanism. Item 1's
model-dependent rule predicts gemma-4-31B sits on the weak-agentic branch —
compaction/trimming will pay off, sub-agent isolation will not. Write-down
decision: keep renders short by construction; do not adopt agentic-isolation
architectures for this agent class. Open question worth a dedicated run:
tracker progress lines add per-turn tokens — measure turns-to-completion
before/after removing them to price the overhead honestly.

### D.2 Procedural memory — external validation of the extraction pipeline (anchors Phase II-2, II-3; Part A procedure layer)

1. **Agent Workflow Memory** (`arXiv:2409.07429`, ICML 2025). Induce reusable
  workflows from past trajectories, selectively re-supply them: +24.6% /
   +51.1% relative success on Mind2Web / WebArena, fewer steps per solve,
   robust generalization as train–test distribution gaps widen; works online,
   supervision-free, inducing only from evaluator-judged successes.
2. **Agent Skill Induction** (`arXiv:2504.06821`). Programmatic skills beat
  AWM's text skills by +11.3pp; execution-based verification at induction
   time is the measured quality lever (+4.2pp from verification alone);
   induced skills pay off only when placed in the action space, not merely
   quoted in memory.

*Mapping.* The procedure layer's pipeline — evidence collection → derive →
one bounded LLM call → validators V1–V5 → capture — is structurally ASI's
verified-induction recipe. The validator gate is not process bureaucracy; it
is the empirically measured quality lever. Two consequences. First, Phase
II-2 (evidence as first-class table) should adopt AWM's online rule: induce
candidate procedures only from outcome streams that pass verification, never
from unreviewed trajectories — this also cleanly separates synthetic banking
seed evidence from real outcomes. Second, ASI's action-space caveat sets an
upgrade path beyond Phase II: applicable procedures should eventually surface
as callable structured steps (slot binders already exist for this), not
procedural prose in the system render.

### D.3 Small-model tool-use reliability — constraints on the τ² bridge (anchors Part A execution layer; Phase I-6)

1. **Constraint Tax / Tool Suppression** (`arXiv:2606.25605`). With JSON
  schema output constraints and tool calling enabled simultaneously,
   open-weight models stop invoking tools entirely — grammar compilation
   masks tool-call tokens out of the decoding distribution. Decoupling
   execution from constrained generation restores invocation without
   retraining.
2. **AgentFloor capability ladder** (`arXiv:2605.00334`; 16 open-weight
  models vs GPT-5, 16,542 scored runs). Open-weight models match frontier
   through multi-step coordination tiers; the surviving frontier advantage is
   long-horizon planning under persistent constraints, where *neither* side
   reaches deployment reliability. Notably, a natural structured-decomposition
   prompt regressed every tested model.
3. **Scaffold effects are family-conditioned** (`arXiv:2606.08529`;
  pre-registered GAIA study). Scaffold choice alone moves accuracy up to
   28pp within one model; effects invert across difficulty levels and vary
   by model family, not just size. Single-scaffold benchmark numbers are
   scaffold-conditional point estimates.
4. **Natural Language Tools replication** (`arXiv:2607.03953`; 14 models,
  8,560 trials). NL tool descriptions beat JSON function calling +14.9pp
   overall and cut critical errors 93%; gains concentrate exactly on smaller
   and non-native-tool-calling models, while heavily RL-tuned frontier
   models converge or reverse.
5. **Tool-schema compilation** (`arXiv:2605.04107`, TSCG). Deterministically
  compiling JSON tool schemas into token-efficient text restores Phi-4
    14B from 0% to 84.4% accuracy at 20 tools; format translation, not
    compression, is the dominant mechanism for small models.

*Mapping.* Risk register entry for the bridge: if any deployment path pairs
structured-output constraints with the substrate tool set on gemma-class
models, item 6 predicts total tool suppression. The enum-footer design
(always-on text menu, no schema constraint) is incidentally the safe pattern
— record why, so a future "modernization" to JSON-schema tool specs does not
silently break small-model deployments. Items 9–10 motivate one cheap A/B
arm: present substrate tool descriptions as compiled text rather than JSON
tool specs for gemma and measure pass-rate delta on the dev-12 set. Item 7
supplies the honest ceiling statement: scaffolds can plausibly close the
mid-tier gap for our agent class; tasks dominated by persistent-constraint
tracking may be structurally out of reach regardless of scaffold quality —
relevant to which τ² task classes we expect never to flip. Item 8 becomes
standing language for every results table we publish: our phase comparisons
are scaffold-conditional estimates under `stealthlab_procedures`, and
cross-scaffold cross-paper comparison inherits that caveat.

### D.4 Evaluation honesty — limitations of every number we produce (anchors Phase II-2; benchmark methodology notes)

1. **Sim2Real gap in user simulation** (`arXiv:2603.11245`) and **Lost in
  Simulation** (ACL 2026). Best LLM user simulator scores 76.0 vs human
    92.9 on behavioral fidelity; simulators are over-cooperative, front-load
    information, and quietly absorb agent errors. Agent success rates swing
    up to ±9pp purely from user-sim choice; τ²'s binary reward is orthogonal
    to human-perceived quality (70.6% of reward=0 interactions judged
    successful by the actual human user).
2. **Reliability vs capability** (`arXiv:2603.29231`). Across 10 models,
  a naive episodic-memory scaffold never improved and often hurt
    long-horizon reliability (overhead tax: extra tool calls + growing
    scratchpad injected every turn). Variance amplification is a capability
    signature, not instability.
3. **Knowledge leakage** (`arXiv:2605.08838`). Benchmarks answerable from
  parametric memory collapse evaluation signal; leakage worsens as corpora
    age into training sets.
4. **The Gold ceiling** (τ-Knowledge paper, `arXiv:2603.04370`). With
  ground-truth documents handed directly in context, the best frontier
    model reaches only 39.69% pass^1 — reasoning, not retrieval, binds the
    frontier ceiling.
5. **Substrate routing** (`arXiv:2608.15008`; controlled harness, 11
  substrates × 7 families). No substrate dominates; optimal choice reverses
    between QA and agentic regimes; retrieving more entries actively harms
    agentic decisions by shifting attention away from action-critical state.
    Design rule: trade read breadth for write depth.
6. **Local finding — serving limits are part of the measurement.** Live
  header probes against General Compute (2026-08-23): 100 requests/min,
    1,000,000 tokens/min, 10,000,000 tokens/day, per key; in-flight requests
    reserve their full potential token budget against both minute windows.
    Consequences measured the hard way: concurrency 4 blew the minute window
    and killed tasks to `RateLimitError` (phaseL); the full-corpus runs
    phaseM/N/O all terminated inside the daily-token wall and are
    **infra-contaminated artifacts, not model results** — 60–78% of their
    simulations end in `timeout` termination with average rewards of
    0.14 / 0.03 / 0.02 respectively, versus 0.417 on the clean 12-task
    phaseK sweep. No full-corpus number currently on disk is board-comparable.

*Mapping.* Item 11 means internal G→M comparisons survive (user sim held
constant) while any absolute claim against the published board carries a
simulator error bar larger than several of our observed deltas — cite it in
every report. Item 12 sets a required check before claiming scaffold wins:
compare trial-pair consistency, not just mean reward; if the tracker raises
means but lowers trial agreement, the overhead tax is live here. Item 13
favors our synthetic-fintech KB design implicitly (fresh, closed corpus) but
warns against evaluating on public KBs the agent may have memorized. Item 14
reframes positioning: procedural scaffolds are reasoning crutches for small
models, so the decisive ablation is gold-docs+scaffold versus gold-docs-bare
on gemma — if the scaffold lifts the gold ceiling itself, StealthLab is a
small-model enablement layer, not a retrieval improvement. Item 15 validates
write-depth distillation (our procedure extraction) as the winning family
for agentic regimes and warns against read-breadth maximization; formalize
routing between procedures-first (action intent) and doc-search (information
intent) at the existing search/get seam. Item 16 mandates the operational
conclusion: full-corpus evaluation requires key-sharded serving (two GC keys
≈ two independent 10M token/day budgets, one tau2 instance each at
concurrency ≤2), with `num_retries ≥ 4` in llm args so transient 429s die at
call level instead of restarting whole simulations — and until that lands,
phaseK's 12-task result remains the only clean number we hold.

### D.5 Knowledge lifecycle — ripple effects and conflict propagation (anchors Phase III-4 TMS; Phase II-1 ChangeSet; Part A knowledge layer)

1. **ChainEdit** (`arXiv:2507.08427`). Editing one fact logically commits
  others ("US President" ⇒ "US First Lady"); baseline logical-
    generalization accuracy on RIPPLEEDITS is ~20%. Mining logical rules from
    graph structure and aligning them with LLM reasoning for chain updates
    lifts it by >30% while preserving edit specificity.
2. **Joint Neighborhood Optimization** (`arXiv:2606.01610`). Propagation
  (related facts should move) and preservation (unrelated facts must not)
    are *coupled* pressures, not separable ones; key-space proximity that
    helps propagation simultaneously exposes preserved facts to corruption.
    Best results come from joint target planning plus a semantic
    **pre-execution gate that abstains** from risky edits.
3. **TRACK benchmark** (EACL 2026,
  `aclanthology.org/2026.eacl-long.273`). Providing updated facts that
    conflict with parametric knowledge can make multi-step reasoning *worse*
    than providing no update at all, and degradation grows with the number
    of supplied updates. Failure splits into non-integration and flawed
    reasoning even after integration.
4. **CLaRE entanglement graphs** (ACL 2026 Findings,
  `aclanthology.org/2026.findings-acl.1469`). Forward-pass activation
    similarity between facts predicts where edits will ripple — cheap
    dependency discovery usable to build preservation sets and audit trails
    before any edit lands.

*Mapping.* This cluster prices Phase III-4 honestly. The typed-edge
dependency queue's lazy mark-stale/validate-on-touch design is the right
shape for item 17's problem: single-fact writes must enqueue their logical
derivatives, and item 20 offers a cheap way to *seed* that edge index where
explicit `depends_on` edges don't exist (entanglement ≈ candidate edge).
Item 18's coupling warning lands directly on the ChangeSet applier (Phase
II-1): when applying an update, plan co-updates and preservation guards
together, and adopt its abstention pattern — a ChangeSet whose blast radius
fails a pre-execution check should queue for review, not apply partially;
this matches the existing fail-closed philosophy in applicability.py. Item
19 is the strongest external argument for the current `state.py` behavior:
projected state must *remove* superseded claims rather than overlay
corrections in-context, because models demonstrably reason worse when forced
to arbitrate old-versus-new inline. Keep bi-temporal tombstones as the only
invalidation mechanism; never ship "here is the correction, ignore the
older row" prompts.

### D.6 Debate and verification — what the governance layer should and should not automate (anchors Part A governance layer; Phase II review flow)

1. **Multiagent debate** (Du et al., ICML 2024). Multiple model instances
  proposing, critiquing, and converging over rounds improves factuality
    and reasoning on black-box models — the founding result behind debate
    pipelines.
2. **Should we be going MAD?** (ICML 2024). Benchmarking debate protocols
  head-to-head: MAD does **not** reliably outperform self-consistency or
    simple ensembling; it is hyperparameter-sensitive, and agreement-level
    tuning matters more than protocol choice.
3. **Demystifying MAD** (ACL 2026 Findings,
  `aclanthology.org/2026.findings-acl.1694`). Under homogeneous agents and
    uniform belief updates, debate preserves expected correctness — it
    cannot beat majority vote in theory or practice. Two lightweight fixes
    restore value: diversity-aware initialization of candidate answers and
    calibrated-confidence-modulated updates.
4. **The Cost of Consensus** (`arXiv:2605.00914`; controlled study, 7–8B
  homogeneous teams). Unguided debate fails three ways — sycophantic
    conformity (up to 85.5% modal adoption), contextual fragility (peer
    rationales destabilize previously correct reasoning, up to 70%), and
    consensus collapse (correct answers present but discarded, oracle gap
    to 32pp) — at 2.1–3.4× token cost versus isolated self-correction for
    equal or worse accuracy. Preliminary scaling suggests conformity worsens
    at 32B.
5. **GAVEL evidence-contract debate** (ACL 2026 Findings,
  `aclanthology.org/2026.findings-acl.1789`). The debate design that
    survives scrutiny binds every atomic subclaim to explicit evidence
    units (Evidence Contract) and runs deterministic mechanical validation
    of cited identifiers and quoted spans before a judge rules.

*Mapping.* Items 22–24 explain why `debate_curation` being dormant is not
accidental debt but accidental wisdom: unguided homogeneous debate at our
model class (gemma-4-31B sits inside the failure regime item 24 measures)
would buy sycophancy at triple token cost. Two standing decisions follow.
First, keep the debate pipeline disabled for routine claim adjudication;
isolated self-correction plus retrieval-grounded re-checks is the better
default at this scale. Second, if debate is ever enabled for high-stakes
reviews, item 25 is the blueprint to copy: require each debater to bind
subclaims to evidence rows (Phase II-2's evidence table supplies exactly
these units) and validate bindings deterministically in code — citation
checks are SQL joins, not LLM judgments — before any judge prompt runs.
Item 23's two interventions (diverse initial candidates, calibrated
confidence in update rules) are the minimum viable configuration if that
day comes.