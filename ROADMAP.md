# Master Work Plan — Verified Procedural Experience System

Consolidated, prioritized list of all work remaining to take this repository from its
current state to the system described by `verified_procedural_experience_system_ideal_specification_v4.md`
("spec v4") and `schema.md`.

Companion documents:

- `verified_procedural_experience_system_ideal_specification_v4.md` — ideal spec (source of truth)
- `schema.md` (+ `schema.html`) — canonical data structures & connection map
- `trial_implementation.md` — current-state audit + four-phase migration-cost plan
- `done.md` — evidence base and fellowship milestones M1–M4
- `0xAlphaplan.md` — τ³-Banking campaign (second-domain evaluation track)
- `experiments/episode_assembly/FINDINGS.md` — episode segmentation findings

## How to read this

Tags used throughout:

| tag | meaning |
|---|---|
| 🔒 | **one-way door** — cheap today on ~700-row tables, a rewrite at scale; front-load |
| [M1–M4] | maps to a fellowship milestone in `done.md` §6 |
| S / M / L | effort: ≤ a session / days / weeks |

Ordering logic: **paper seams first, trust-correctness before corpus growth, measurement
in parallel from day one, expensive machinery as late as possible** — consistent with
`trial_implementation.md`'s organizing constraint (minimize migration cost; one-way doors
first). Bands 0–2 and 5–6 are sequential; Band 3 runs continuously and gates the exit
criteria of Bands 4–5.

---

## Band 0 — Spec & schema reconciliation

*Paper only, ~a session. Unblocks everything below; zero code risk.*

| # | Work | Why it exists |
|---|------|---------------|
| 0.1 | One canonical Procedure object. schema.md wins; add lifecycle/status/evidence-reference fields it currently lacks (already implemented in `backend/db/20_procedure_extraction.sql`); delete spec v4 §13's bare `trust score: int` — it contradicts the closed decision in `done.md` §4.6 ("no confidence field is stored; confidence is derived from evidence") unless explicitly computed | Three documents currently carry three different Procedure definitions |
| 0.2 | One scope vocabulary. §3 lists `branch` but not `entity`; §7 lists `entity` but not `branch`. Pick one set, everywhere | Scope is the universal shard key (§3 mandate); ambiguity here is structural 🔒 |
| 0.3 | Adopt three mutability classes `[V]`/`[H]`/`[D]` in the spec; fix §19's "exactly two mutability classes" | Derived-frozen objects (ExecutionPlan, TaskGraph) fit neither of the two classes; schema.md already operates with three |
| 0.4 | Unify capability into one representation: ladder levels 0–5 defined as **banded P(required outcome \| state, procedure, implementation)**; specify that §23 routing thresholds apply to P, not to the ordinal level | Spec uses continuous P (§16, §23 examples), schema.md uses discrete levels; no bridge defined. Routing semantics are undefined until this is settled |
| 0.5 | Give failure classification a structural home: `failure_class` on Evidence or a dedicated FailureRecord carrying §36's six causes (procedure-wrong / implementation-wrong / environment-changed / input-abnormal / verification-wrong / external) | §36 requires classification-driven updates; neither Outcome nor Evidence has anywhere to record the classification |
| 0.6 | Freshness mapping table: knowledge category (§37: preference/policy/software-version/location/procedure) → decay class → default revalidation triggers. Map it onto proposition types or declare them orthogonal | §37 keys decay on a typology disjoint from Claim proposition types; unimplementable as written |
| 0.7 | Belief-aggregation contract: inputs (evidence direction/strength/freshness/scope/source-reliability), independence-group capping, required invariants (monotonicity, contradiction dominance, effective-n bounding), pluggable `method` string | This is the mathematical heart of "verified". Ideal-spec hand-waving is acceptable only if the contract names what any aggregator must satisfy |
| 0.8 | New spec section: **Utility & Retirement** — match-cost model, retrieval-overhead budget, retirement criterion; add computed utility fields to Procedure | The utility problem (Minton; `done.md` §4.6) is claimed as the signature novel insight but appears nowhere in spec v4 or schema.md. A system that can only grow is precisely the failure mode the project says it avoids |
| 0.9 | Resolve deletion-vs-append-only: §19 forbids editing history; §34 requires deletion/revocation. Choose and specify a mechanism (crypto-shredding keyed per scope; tombstone-with-payload-eviction) | Direct contradiction between two mandated sections; GDPR-shaped exposure for any real deployment. Solving it well is itself differentiating |
| 0.10 | Add replayability + extractor-versioning to §39 minimum invariants; fix section numbering (§17 and §21 do not exist; §18 is a two-line fragment with a typo) | The redesign brief (`spec.md`) calls replayability "a major design requirement"; v4 dropped it entirely |

---

## Band 1 — Trust-correctness contracts

*Single Postgres, ~700 procedures + small graph. Additive migrations, minutes each.
Before any corpus grows.*

1. **Characterization tests first.** Gold-set retrieval A/B harness + provenance-behavior
   pins. Every later item in this band mutates trust machinery; freeze current behavior so
   regressions are detected rather than argued about. *(This harness also serves Band 3.)*
2. Provenance P0 fixes: `backend/app/onboarding/seed.py:167,179,198` hardcodes
   `'company_ingested'` for third-party benchmark corpora; auto-created proxy nodes carry
   `'company_debate'` before any debate ran. Seed paths pass explicit provenance; proxy
   stamping becomes a neutral value until review completes. **[S]**
3. **Scope columns on every table** — `scope_type`/`scope_entity_id` on knowledge_nodes,
   task_nodes, edges, procedures, observations, episodes, agent_traces; ingestion adapters
   reject scope-less payloads (V0 validator). §3 mandate; future shard key. 🔒 **[S–M]**
4. Claim shape upgrade: real subject/predicate/object columns extracted alongside JSONB,
   dual-read transition, JSONB retained; add proposition-type, status-machine, and belief
   `{score, method}` columns per schema.md. 🔒 **[S]**
5. UUIDv7 defaults for new rows only; existing ids untouched. Index bloat under insert
   load otherwise guaranteed. 🔒 **[S]**
6. Embedding provenance stamps — `embedding_model_id`, `embedding_dim`; backfill as
   `gemini-embedding-001@1024`. Model transitions become routine instead of gambles. 🔒 **[S]**
7. **Persist ExecutionPlan / TaskGraph `[D→frozen]` tables and bind every execution to an
   exact plan version.** Absent from `trial_implementation.md`'s plan entirely.
   Executions recorded without plan references can never be retrofitted — the purest
   one-way door in this list. Repairs spec invariants #1–2. 🔒 **[M]**
8. Extraction correctness bundle: precondition relevance filter (derive gates only
   load-bearing facts, not every live claim — stops the library self-obsoleting);
   V6 authoring-time invariant validator (malformed invariants currently permanently
   disqualify their row at retrieval time); z3 off the MCP event loop with solver timeout;
   memoized `project_state()` inside the applicability cascade (kills the N+1);
   tenant-scoped cold-start gate. **[M]**
9. Quick win pulled forward from Phase II: backfill a `synthetic` flag onto banking-seed
   outcomes now so capability claims stop commingling simulated and real evidence ahead of
   the full Evidence table. **[S]**

*Exit criteria:* zero scope-less writes accepted; retrieval precision unchanged-or-better
on the gold set; all migrations applied to a production-shaped copy in <1 hour; plan
persistence demonstrated end-to-end.

---

## Band 2 — Trust completion

*Still one Postgres. Purely additive over Band 1 shapes. ≈ Milestone M1 ("close the loop").*

1. Universal ChangeSet — extend `backend/app/models/change.py` reach to observations,
   procedures, implementations, applicability rules, states; observation revisions enqueue
   dependents (§20). Every `[V]` mutation produces a ChangeSet record. **[M]**
2. Evidence as a first-class table — typed rows with independence groups; procedure
   statistics become views over evidence, ending synthetic/real commingling (Band 1.9's
   flag retires). This is M1's provenance join key. **[M]**
3. Capability computation v1 — levels-as-banded-P derived from outcome streams with
   bidirectional demotion on failure; replaces raw counters as the routing input. Uses
   0.4's unified representation. **[M]**
4. Failure-classification pipeline — classify outcomes into §36's six causes (home built
   in 0.5) and route updates: applicability narrowing vs scope/exclusion change vs stale
   marking vs procedure revision vs no-op. **[M]**
5. Production episode segmenter — promote the prototype's Rule-A (prompt boundaries) plus
   merge/subdivide rules into `trace_worker`; idle-gap signal dropped per FINDINGS.md
   (bimodality does not exist in machine-paced transcripts; 18% trivial-prompt rate makes
   merge rules mandatory, >200-event prompts make subdivision mandatory). **[M]**
6. ClaimFamily resolver v0 — project-scoped blocking + proposition match; cross-project
   deferred to Band 4. **[M]**
7. Make TMS readable at minimum: `truth_state` is currently write-only (`done.md` §5 audit)
   — OUT/stale claims must vanish from retrieval results. Cheapest trust win available. **[S]**
8. Replayability end-to-end — extractor id+version stamped on every derived object;
   deterministic regeneration test proves observations/claims/procedure candidates can be
   rebuilt from raw traces. **[M]**
9. Identity gate — real authN (OIDC-only acceptable) before any multi-user exposure,
   because permission semantics block publication features. Paired with an explicit frozen
   posture asserted in code (single-tenant refused to boot once private visibility is on)
   so it cannot silently slip to Band 5. **[L]**

---

## Band 3 — Measurement & evaluation *(parallel track — starts NOW, never stops)*

*Every exit criterion in Bands 4–5 references p95s, drain rates, and precision deltas.
Nothing else builds the instrumentation those numbers require. Also the only path from
"novel framework" to "demonstrated": spec §40's comparison is design-only today.*

1. Instrumentation baseline: retrieval p95, ingestion queue drain rate, capability-score
   distributions, reuse rate, false-reuse rate, stale-procedure detection rate. **[S]**
2. **§40 evaluation harness** — arms A (frontier agent solo) / B (+conventional memory) /
   C (+verified procedural experience) on SWE-bench Pro and τ³-banking; metrics per §40
   including false reuse and capability-prediction quality. The strongest-result claim
   ("solves new tasks better and cheaper while correctly refusing stale procedures") is
   falsifiable only through this. **[L]** — start early, iterate forever.
3. Extraction-quality floor — labeled-sample precision/recall for observation→claim
   extraction. The whole trust story inherits extraction's error floor; nothing measures
   it today. **[M]**
4. Utility accounting — per-procedure match cost + retrieval overhead measured and stored
   (computed fields from 0.8). Feeds Band 4.8's retirement criterion; confronts the
   utility problem in practice, not just prose. **[M]**
5. Outcome→capability traceability test = M1's falsifiable gate ("statistics change
   because of an execution outcome, traceable end-to-end"). **[S]**
6. τ³-Banking campaign remainder under this umbrella — second domain for all of the
   above: A2 doc→procedure compiler, A3 HTN-in-conversation, A4 cascade gating, A5 RRF +
   expansion retrieval, A6 learning-loop curve; B1–B3 paired baselines. Status per
   `0xAlphaplan.md`: A1 partial, Phase 1a done, A2–A6 unstarted.

---

## Band 4 — Scale posture

*Tens of millions of rows. Postgres remains the system of record. ≈ enables M2–M3 at scale.*

1. **Append-only discipline formalized.** `trial_implementation.md`'s Phase I ledger
   credits "append-only discipline" for movement-free partition activation, but no item
   establishes it. Make it explicit: no UPDATE paths on `[H]` objects, enforced by tests.
   **[S]**
2. Partition activation on `(scope, t_valid)` for the big tables — ATTACH-based, zero data
   movement, enabled by Band 1.3 + 4.1. **[M]**
3. Read replicas — retrieval reads fan out; writes stay primary. **[S]**
4. OLAP rollup store beside Postgres (ClickHouse or DuckDB-over-Parquet) fed incrementally
   from append-only evidence/outcomes; capability analytics and family clustering leave
   OLTP entirely. **[M–L]**
5. TMS dependency-queue service — typed-edge index (`depends_on`, `derived_from`,
   `supported_by`, `requires`) with lazy mark-stale / validate-on-touch; lazy *because*
   eager propagation is unbounded at this tier. Full §20 fan-out: claim supersession,
   observation revision, procedure version, implementation change, rule change, source
   reliability change. **[L]**
6. ClaimFamily LSH blocking pipeline — scales family resolution past project boundaries. **[M]**
7. Ingestion worker fleet + Parquet cold tier — batched idempotent workers on the existing
   job queue; logical replication exports cold history outward. **[M]**
8. **Utility-based retirement live in routing** — retirement criterion from 0.8 executes:
   procedures whose expected utility goes net-negative are demoted out of candidate sets
   automatically. The claimed novelty becomes enforced behavior. Depends on Band 3.4 data.
9. Belief-aggregation v1 implemented behind 0.7's contract; belief fields stop being null. **[M]**

*Exit criteria:* p95 retrieval latency stable under 10× row growth (readable because Band
3.1 shipped long before); TMS queue drains faster than it fills; OLTP share of analytics ≈ 0.

---

## Band 5 — Distribution & governance

*Hundreds of millions of rows. Configuration of Band 1–4 shapes, not rewrites.*

1. Shard-by-scope activation — Citus-class distribution or federated per-project clusters;
   mechanical only because every row has carried its scope key since Band 1.3. **[L]**
2. Distributed edge-store decision gate — evaluate NebulaGraph-class engines for
   multi-hop traversal **only if** cross-scope traversal becomes a measured hot path
   (current workloads say it is not). Explicit decision point with a measurement
   requirement, not default adoption. **[L]** evaluation / deferred adoption.
3. Dedicated ANN cluster — global-tier vector search splits from OLTP; selective-embedding
   policy governs what earns a slot. Enabled by Band 1.6 stamps. **[M–L]**
4. Streaming ingest backbone — Kafka-class buffer between collectors and workers; absorbs
   burst, decouples regions. Buffers a queue that has existed since migration 12. **[L]**
5. Policy engine + authorization service — versioned Policy objects evaluated at plan-time
   and execution-time; delegation rule enforced ("a procedure never grants more authority
   than its invoking user", §34). Identity extends from Band 2.9. **[L]**
6. Deletion/erasure mechanics implemented per 0.9's chosen design — crypto-shredding or
   payload-eviction, exercised against the append-only guarantee. **[M after 0.9]**
7. Multi-region posture + residency — residency is scope type `organization`+region taken
   seriously; scope-aware placement. **[L]**
8. Motif layer / cross-domain transfer experiment (M4): abstract behavioral motifs as
   hypotheses with supporting episodes and contradiction counts — never silently inferred,
   never directly executable initially. **[L]**

*Exit criteria:* shard rebalancing demonstrable without downtime; cross-region scope
enforcement passing; sustained ingest above peak collector burst with bounded lag.

---

## Band 6 — Hygiene *(whenever convenient; never blocks anything)*

- Stale pointers: root README run instructions reference nonexistent `backend_v2/backend_v2`;
  `0xAlphaplan.md` cites `vendor/tau2-bench` (actual location
  `experiments/tau3_bench/_tau2_bench_src/`).
- `trial_implementation.md` and other docs cite backend paths without the `backend/` prefix.
- `backend/README.md` still describes the debate-platform framing and a "Not built" list
  that the services tree has outgrown; rewrite around the substrate reality.
- Regenerate `schema.html` after Band 0 lands.

---

## Critical path

```
Band 0 ──► Band 1 ──► Band 2 ──► Band 4 ──► Band 5
              │          │    ▲
              ▼          ▼    │
           Band 3 (parallel, continuous; gates 4/5 exit criteria)

Band 6 sprinkled anywhere.
```

The single biggest risk remains unchanged from `trial_implementation.md`'s own summary:
starting Band 4 work before Band 1 lands converts every later `ATTACH PARTITION` /
`CREATE DISTRIBUTION` back into a rewrite. Two additions to that risk statement:

1. **Band 1.7 (plan persistence) joins Band 1 despite being absent from the original
   phase plan** — executions recorded without exact-plan references are unrecoverable
   data loss w.r.t. invariants #1–2, and no later band can repair history.
2. **Band 3.2 (the A/B/C harness) starts during the Band 1 timeframe** — every week it
   slips is another week the SOTA claim stays architecture-plus-rhetoric while published
   agent-memory work keeps moving.

---

## Appendix A — Consistency audit (what Band 0 fixes)

Findings from cross-reading spec v4 ↔ `schema.md` ↔ backend code ↔
`trial_implementation.md`:

1. Scope type sets differ between §3 (has `branch`, no `entity`) and §7 (has `entity`,
   no `branch`); schema.md follows §3.
2. Mutability classes: spec says exactly two; schema.md correctly needs three
   (`[D]` derived-frozen has no home in §19).
3. Capability appears as discrete ladder (schema.md) and continuous conditional
   probability (§16/§23) with no bridge; routing thresholds ambiguous between them.
4. Spec numbering broken: §17 and §21 missing; §18 is a typo-bearing fragment.
5. `trust score: int` on Procedure (§13) contradicts the closed no-stored-confidence
   decision recorded in `done.md` §4.6.
6. Three divergent Procedure definitions across spec v4 §13, schema.md, and the backend
   migrations (which are ahead of schema.md: lifecycle/status exist there, absent in
   schema.md).
7. Failure classification (§36) and freshness decay (§37) have no structural home in any
   schema; both are prose-only requirements.
8. Replayability/extractor-versioning: required by the redesign brief, absent from v4's
   invariants and from the implementation plan.
9. Deletion/revocation (§34) directly conflicts with historical append-only (§19);
   unresolved anywhere.

## Appendix B — Position relative to published work (why the order above)

Ahead of shipped agent-memory systems (Mem0/Letta/Zep-Graphiti class; Voyager/AWM/ExpeL
academic line): bi-temporal invalidate-and-append everywhere, typed propositions with a
status machine, evidence with independence groups feeding computed capability with
bidirectional demotion, non-compensatory fail-closed applicability cascades with
solver-checked invariants, implementation pluralism with cheapest-capable routing, and
utility-aware retirement as a designed-in criterion rather than an afterthought.

Commodity (deliberately standard, not differentiating): hybrid RRF retrieval, HNSW, FTS.

Behind or unbuilt (hence Bands 2–3): episode assembly still prototype-grade, extraction
error floor unmeasured, evaluation harness unbuilt, identity/auth minimal. The framework
is novel in composition; only Band 3's harness converts that into a demonstrated claim.
