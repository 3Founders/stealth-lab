# Map: Memory Substrate

## Destination

A locked architecture specification for milestone 1 — a general experiential + procedural memory substrate, with Claude Code agentic-trace ingestion as its first domain on top — grounded in the existing `backend/` code, with representation, schema, migration strategy and implementation order settled well enough that implementation can begin with no major unknowns.

## Notes

**Source of requirements:** [spec.md](../../spec.md) at the repo root. Read it before resolving any ticket.

**Domain:** Python 3.13, FastAPI, asyncpg, pgvector. Raw SQL migrations in `backend/db/`, no ORM, no Alembic. Pydantic models are hand-written mirrors of the SQL, bridged by explicit `from_row()`.

**Every session calls:** the `grilling` and `domain-modeling` skills. Research tickets call `research`; prototype tickets call `prototype`.

**Research findings, all four briefs answered:** [research/answers1.md](research/answers1.md) (temporal fact representation → ticket 10, resolved), [answers2.md](research/answers2.md) (trace segmentation + semantic extraction → tickets 04, 11), [answers3.md](research/answers3.md) (reuse economics → tickets 12, 13, 15), [answers4.md](research/answers4.md) (retrieval + resource allocation → tickets 14, 15). Each open ticket carries a `## Research findings` section summarising what its own sub-questions came back with. Two things worth knowing before working any of them:

1. **A cross-ticket contradiction that is only apparent.** Brief 3 says applicability *must* use a strict, non-compensatory filter cascade; Brief 4 says a strict cascade is an *antipattern* for retrieval. Both are right because they govern different things — **hard constraints filter, soft relevance signals fuse**. A violated precondition is a disqualification (ticket 12); a low structural-locality score is a weak signal (ticket 14). These two mechanisms must not be unified.
2. **The utility problem is confirmed real and unaddressed by the LLM-agent field.** Minton's `utility = (application_frequency × average_savings) − match_cost` means a *correct* procedure can still be worth deleting because matching it costs more than it saves. The 2026 skill-library survey does not mention it or negative transfer at all. This adds a retirement criterion to ticket 13 orthogonal to any failure, requires `verification_stats` to record match cost and realised savings (ticket 05), and requires a matched no-transfer control arm to measure (ticket 12).

Recurring caveat across all four briefs: **almost nothing is measured for LLM-coding-agent systems specifically** — findings are transferred from process mining, CBR, classical planning, IR, flaky-test practice and speedup learning, and each brief says so per-question. Treat numbers as calibration, not as validated constants.

**Open research prompts + per-decision triage:** [research/research-prompts-open-questions.md](research/research-prompts-open-questions.md) — all 51 decisions across the 7 open tickets, each classified as externally-researchable (31), answerable from this repo alone (17), or needing this repo's own data (3), plus 4 consolidated Perplexity briefs covering the 31. **Before working any open ticket, check its sub-questions in that triage** — it tells you which parts of the ticket are worth researching versus just deciding, and names 12 specific prior-art leads a generic search misses (most importantly **the utility problem**, which is a named classical threat to the whole procedural-memory premise: stored plans can make a system net *slower* because match cost grows faster than planning savings).

**Standing reference #2:** [research/external-literature-review-2-open-questions.md](research/external-literature-review-2-open-questions.md) — targets the 7 remaining open tickets specifically, and unlike review #1 it *changes* things: ticket 10 needs a materialization escape hatch (projection-by-query is only cheap for as-of-now queries); ticket 12's fail-open-vs-fail-closed question has a principled answer (fail closed, structured predicates not string tags); ticket 13 has near-exact prior art worth fetching; ticket 14 should likely defer reranking. Also flags that **five of the seven open questions have no directly applicable literature** — those are engineering judgement calls, and the affected tickets should say so rather than implying more grounding than exists. Citations relayed from a literature search, not independently verified — check before load-bearing use.

**Standing reference:** [research/external-literature-review.md](research/external-literature-review.md) — an external literature review (agent memory architectures, TMS/belief revision, provenance graphs, procedural memory, retrieval, bitemporal modeling) gathered mid-effort. Not tied to one ticket; most load-bearing for 02, 03, 04, 05, 09, 10, 12, 13, 14, 18. Corroborates the map's existing direction rather than redirecting it — in particular, the "procedure = planner-neutral object + separate execution binding" pattern it documents (MemP, Voyager, Agent Workflow Memory) is the concrete literature grounding for the Procedure≠HTN invariant ticket 05 has to resolve.

**Standing preferences for this effort:**

- Cite exact existing files/classes/functions in every resolution. Do not invent existing functionality.
- Preserve existing data and IDs; prefer compatibility links over rewrites.
- Do not rename an existing concept merely because a greenfield design would name it differently.
- Keep these distinct — never collapse into one generic "memory node": event · observation · episode · claim · state · procedure · procedure execution · trace · evidence · agent · task node · HTN/DAG · motif.
- Provenance and justification are canonical; confidence is derived from evidence, never the other way round.
- Prefer deterministic local processing over LLM calls. The synchronous agent execution path must never depend on the memory compiler.

**Scoping decisions already made** (2026-08-17, with the repo owner):

1. **Substrate first, then Claude Code on top.** Whether the SWE-bench experiment survives is a question to ask *after* implementation — it is not a driver.
2. **The HTN/DAG engine moves into `backend/`**, and its structural flaws get fixed as part of the move — not just relocated.
3. **Auth/isolation is a discussion, not a default.** Supabase and WorkOS are explicitly on the table.

**The map plans; it does not build.** Tickets resolve decisions. Ticket 01 is the one exception — it produces the inventory document the other tickets cite.

## Decisions so far

<!-- one line per closed ticket: gist of the answer, then the link -->

- [Architecture inventory](issues/01-architecture-inventory.md) — full inventory written to
  [inventory.md](inventory.md); key surprises: `claims.py` already implements a working
  claim/truth-state/supersession primitive on top of `knowledge_nodes` (not a greenfield
  gap), procedure representation is structurally HTN-shaped today (`method_library.py`
  tags decompositions onto `task_nodes`), and `hierarchy.py`/`retrieval.py` already cover
  most of the target locality/retrieval hierarchy. Four concrete defects confirmed
  (`embedding_joint` ghost column, `public_generated` enum/model drift, pool
  double-management bug, undocumented migrations 06–10).
- [Claude Code hook schema](issues/07-claude-code-hook-schema.md) — full findings written to
  [research/claude-code-hook-schema.md](research/claude-code-hook-schema.md); spec.md's 13
  named hook events are all real (not speculative), but the official surface is actually 31
  events, omitting whole families spec.md never scoped (`Notification`, `ConfigChange`,
  `FileChanged`, `Elicitation*`, etc.); hooks are documented as best-effort delivery only, with
  no retry/crash guarantees and non-deterministic ordering, directly confirming spec.md's
  instinct that memory correctness must not depend on hooks firing perfectly; the JSONL
  transcript's per-line schema is explicitly undocumented and stated to change between
  releases, so its completeness relative to hooks is an open empirical question, not a
  documented fact; no schema-version field exists anywhere in hooks or the transcript.
- [OpenTelemetry GenAI semantic conventions](issues/08-otel-genai-semconv.md) — full findings
  written to [research/08-otel-genai-semconv-findings.md](research/08-otel-genai-semconv-findings.md);
  key surprise: the GenAI conventions were split out of the main semantic-conventions repo
  into an untagged, pre-1.0 `semantic-conventions-genai` repo in June 2026, making this
  repo's `OTEL_SPEC_VERSION = "1.30.0-experimental"` pin and `OTEL_ACTION_MAP` key strings
  both stale/mismatched; coverage is bimodal — LLM-call/tool-call fields (tool id/name/
  input/output, model invocation, token counts, error info) are well covered, but
  Identity/Intent/Environment fields (episode_id, actor_id, user_goal, cwd, repo/branch/
  commit, permission state, provenance) have no semconv equivalent at all, and semconv's
  span parent-child nesting does not model this repo's episode/trace/event grouping;
  evidence points to a hybrid derive-for-LLM-shaped-fields/diverge-for-session-shaped-fields
  outcome, left for ticket 06 to decide.
- [Substrate/domain seam](issues/02-substrate-domain-seam.md) — a second domain is a design
  constraint, not a scheduled build (no adapter gets built now). Representation mechanism:
  uniform `domain TEXT` + `domain_payload JSONB` on every concept holding concrete
  domain-shaped facts (episode, trace-header, `trace_events`, state, procedure), validated at
  write time against a single `(concept, domain) → PydanticModel` registry in the service
  layer — not a database constraint, no ORM/Alembic in this repo. Claim/observation/evidence/
  procedure-execution stay fully generic, no domain split. This resolves ticket 06's
  "own namespace, mechanism TBD" fields directly: they're `domain_payload` under
  `domain='coding'`. Package boundary named now (`backend/app/services/coding/` for the 7
  unambiguous files), physical move deferred as implementation work, not a further ticket.
- [Claim representation](issues/03-claim-representation.md) — ratified `claims.py`'s existing
  approach (`node_type='claim'` on `knowledge_nodes`), not a dedicated table: identical access
  pattern to knowledge_nodes, and nothing produces a claim yet so this was a clean-slate call.
  Fix carried with it: `capture_claim()` should set `embedding` (currently omitted, so claims
  are invisible to retrieval today). `node_type` counter-pressure gets a real but narrowly-
  scoped fix — a `NODE_TYPE_SCHEMAS` write-time registry for `claim` only, not retroactive to
  the other 6 virtual types. Subject/predicate/object as three JSONB keys plus one expression
  index, not real columns — no index exists on `properties`/`node_type` today regardless.
  Observations (04) and procedures (05) aren't bound to the same representation, only to the
  same test (shared access pattern vs. distinct shape/volume/lifecycle) that produced this
  answer.
- [Procedure representation](issues/05-procedure-representation.md) — dedicated `procedures`
  table, resolving spec.md's forbidden TASK NODE/PROCEDURE collapse (today a procedure is
  just a tagged `task_nodes` row). A stored plan is an **execution binding** *of* a procedure,
  not the procedure itself — the concrete mechanism for Procedure≠HTN: `steps` is
  planner-neutral, an HTN-specific binding (ticket 15) translates to a concrete DAG at
  instantiation time. Parameterization: both a deterministic extractor (tool-call-argument
  slot inference, no LLM) and an LLM-pass extractor get built, caller picks which runs;
  `parameter_schema` stamps `extraction_method`/`extractor_version` per ticket 06's
  versioned-extraction discipline. No blocking "measure reuse first" ticket — chicken-and-egg,
  so `verification_stats` is baked in from day one instead. `hierarchy.py`/`subtask_reuse.py`
  untouched (different concern). Existing `htn_method_library`-tagged `task_nodes` become
  legacy candidate procedures, migrated by ticket 17 with a `migrated_from_task_node_id`
  pointer. Versioning rides existing bitemporal + `SUPERSEDES` (procedures added as a valid
  `edges` source/target table), not a new mechanism. `family_id` self-references `procedures`
  itself (abstract family row, same table) — explicitly distinct from motifs (out of scope).
- [Canonical trace model](issues/06-canonical-trace-model.md) — two new tables (`trace_events`,
  a trace-header table), not a rework of `traces`, but *not* because `traces` is event-shaped —
  reviewed and corrected: `traces` already has `parent_trace_id` (a real, live causal tree) and
  is span-shaped, closer to spec.md's TRACE concept than first claimed. The real reasons are its
  `NOT NULL` `task_node_id` FK (confirmed the most common real rejection cause in `ingest.py`),
  its 3-value `action_type` CHECK vs. Claude Code's 31 real events, and its `NOT NULL` outcome
  CHECK — all three would have to weaken for existing consumers (`triggers.py`, `layer2.py`) to
  accommodate the new shape. New tables carry both `owner_id` and `visibility` (see ticket 09
  correction) from row one — `traces`/`episodes` currently have neither column, a real gap
  independent of this decision. Hybrid OTel-semconv adoption per ticket 08. Existing `task_node_id`
  FK on `traces` untouched.
- [Isolation and auth posture](issues/09-isolation-and-auth.md) — stay local-first,
  single/few-trusted-owner for milestone 1; do not adopt Supabase Auth or WorkOS yet, consistent
  with `mcp_server/server.py`'s own already-shipped single-tenant, loopback-only posture.
  Corrected on review: new tables need **both** `owner_id` and `visibility` (not `owner_id`
  alone — `access.py`'s `visibility_predicate()` requires `visibility` in every non-unrestricted
  branch; `owner_id`-only would recreate the exact `embedding_joint` ghost-column failure shape
  ticket 17 warns about). `db/03_access.sql` already adds the pair together on four tables;
  `traces`/`episodes` currently have neither. Project isolation is its own axis. Encryption
  cross-referenced to ticket 18. If a provider is adopted later, identity only — `access.py`
  stays the sole authorization authority, never Supabase RLS.
- [Migration mechanism and data migration](issues/17-migration-mechanism.md) — keep raw SQL, add
  a `schema_migrations` ledger + one documented runner script + a CI test diffing code against
  schema (the two known drifts were schema-vs-code consistency failures a test would have caught,
  not something a migration framework addresses). Sharpened on review: `embedding_joint` isn't
  just unreachable at query time — `graph_ingest.py` writes it and `compare_embeddings.py` reads
  it, meaning Stage 2's real, published result (p=0.0066, n=400) was produced through a column no
  version-controlled DDL creates; the CI check must diff against the *live* database, not just
  the DDL files, or it validates the drift instead of catching it. No migration needed for
  `episodes`/`traces`/`edges`/existing `knowledge_nodes` types. `claim`-type rows blocked on
  ticket 03. Method-library rows becoming procedures get a fresh UUID plus an explicit link edge.
  SWE-bench corpus stays untouched, out of scope.
- [Privacy and redaction](issues/18-privacy-and-redaction.md) — redaction happens client-side at
  the collector, before transmission (real dependency on ticket 16), on the parsed JSON structure
  not raw text — a best-effort floor for *detected* patterns only, never a guarantee; the hard
  local-only default is the real backstop. Path/tool exclusion as two configurable axes with real
  shipped defaults. Corrected on review: deletion tombstoning `episodes` is a genuine **schema
  addition** (`episodes` has no `t_invalid` today), and `episode_links` is currently declared
  `ON DELETE CASCADE` — the opposite behavior, needing an explicit fix, not an automatic
  consequence of adding a column. `truth_state` is not a column — it's a JSONB key inside
  `knowledge_nodes.properties` (`claims.py:131`), with real query-cost implications for ticket 03,
  which owns the actual claims schema. Vault encryption only helps if its key is stored separately
  from the shared `DATABASE_URL`. Sampling means whole-episode accept/reject, a real trade-off,
  not a solved problem — whatever episodes are dropped are lost entirely.
- [Ingestion pipeline shape](issues/16-ingestion-pipeline-shape.md) — a Postgres job table polled
  with `SELECT ... FOR UPDATE SKIP LOCKED` by an in-process asyncio worker: a broker contradicts
  local-first, and in-memory-only repeats `InMemoryTaskStore`'s known restart/replica limitation
  in the one place spec.md's replayability requirement makes it unacceptable. Collector appends to
  a local file rather than POSTing per event — hooks are best-effort with tight timeouts (30s on
  `UserPromptSubmit`, 10s on `MessageDisplay`) and silent drops, so network latency must stay out
  of the user's editing loop; the local append is also where ticket 18's client-side redaction
  runs. Idempotency reuses `/v1/traces`' per-record + `ON CONFLICT DO NOTHING` pattern at raw
  persistence only, with a collector-computed composite key (hooks carry no native event ID);
  downstream stages key by *job*, which is what distinguishes replay from duplicate delivery.
  Bounded local file, drop-oldest, with a recorded drop counter — never block, never unbounded.
  Ordering is assembly-time only. Honest limit: nothing structurally prevents calling the compiler
  on a request path (`admin.py:92-94` proves discipline alone fails) — a job-row-only entry point
  is a speed bump, not a guarantee. Also found while verifying: `main.py:27`'s pool and
  `session.py`'s `close_pool()` operate on different objects, which an in-process worker would
  inherit.

- [State model](issues/10-state-model.md) — **no state table**: `state_before`/`state_after` are
  one read-time projection over the claim graph evaluated at two timestamps, `state_delta` a set
  difference computed on demand. Confirmed a recognized stance, not a novel bet — it is the
  event-calculus frame axiom as a query, with Datomic as production precedent. Granularity and
  domain-neutrality both dissolve (state is a function of scope+timestamp; claims are already
  generic). Three corrections made against the research findings: (1) **GiST, not btree** — a
  composite partial GiST expression index over `(properties->>'subject', tstzrange(t_valid,
  t_invalid))` with `btree_gist`, requiring zero schema change, superseding an earlier btree
  draft; (2) **no exclusion constraint** — it would make the contradiction case unrepresentable,
  inverting the required preserve-both-and-mark behaviour; (3) **the ~100-250-events
  materialization threshold does not transfer** — it exists because event sourcing replays O(n),
  while this design does indexed containment lookup O(log n); XTDB's narrower warning about
  full-history and temporal-range queries does stand, and neither is needed in milestone 1.
  Closed-world assumption adopted (absence = empty result, no NULL sentinel), with a
  non-foreclosing escape hatch: if "unknown" is ever needed it becomes an explicit status value,
  never a NULL. **Adds `epistemic_status` (`observed` | `inferred`) to ticket 03's
  `ClaimProperties`** — required because unifying durable belief with transient world-state
  demands the two be distinguishable; this extends ticket 03's registry rather than reopening it,
  and ticket 04 owns how the value is assigned. Artifact references are a typed union
  (`GitSha | BlobUri | DbId`) naming their addressing scheme, not a bare string.
- [Observation layer](issues/04-observation-layer.md) — dedicated `observations` table, diverging
  from claims: observations are *immutable* (re-derived under a new extractor version, never
  superseded) so bitemporal machinery goes unused, and they are the highest-volume object in the
  system, which after ticket 10 made claims high-volume too would compound a risk rather than
  share infrastructure. Source-event link is a dedicated `observation_events` join table (reverse
  traversal is load-bearing for provenance); rejected extending polymorphic `edges` at the
  system's highest write volume. Both extractors built, deterministic and model-based — and that
  split **is** ticket 10's `epistemic_status` (`observed` / `inferred`), giving that field a
  well-defined producer. Extraction version stamps components, not one hash. **No confidence field
  at all**: raw logprobs are uncalibrated and verbalized confidence is uncorrelated with accuracy,
  so storing a float would invite downstream code to trust it — consistent with the map's
  standing preference that confidence is derived, never asserted.
- [Episode assembly](issues/11-episode-assembly.md) — strict precedence with hierarchical nesting
  (`prompt > subagent > commit/test > idle`); voting, union and intersection all rejected. Idle
  gaps kept as the weakest signal with the threshold **fitted** (two-component Gaussian mixture on
  log-scaled inter-event times, valley lands ~1h for developer workflows) rather than the
  30-minute convention, which the literature calls "dodgy maths done on 1995 browsing behaviour" —
  independently vindicating spec.md's refusal of a single timeout rule. Semantic segmentation
  subdivides within deterministic boundaries, never across. **Episode is the right unit for memory
  and the wrong one for procedure mining** — HRL option-discovery shows description-optimal and
  extraction-optimal boundaries genuinely differ; M1 builds episodes only and the option-span
  segmenter goes to fog, unowned. Evaluation targets 70–80% precision, not >90%, because human
  inter-annotator agreement on task boundaries is itself only ~70–80%.
- [Applicability function](issues/12-applicability-function.md) — preconditions are structured
  predicates over the claim graph, **derived deterministically from the source episode's
  `state_before`** (ticket 10 supplies the producer, which is what makes predicates viable rather
  than aspirational); tags demoted to coarse filtering only, since Jaccard-over-tags is a
  *similarity* measure being used as an *applicability* test — a category error per CBR, not just
  imprecision; LLM for soft ranking only (~F1 65%, disqualifying for a hard gate). Nine factors
  combine as a **non-compensatory filter cascade** — the pathology has a name, **criterion
  compensation**, and weighted linear scoring across mixed hard/soft criteria is the named
  antipattern. Unknown preconditions **fail closed**, free under ticket 10's CWA;
  **rejected brief 3's three-valued-logic recommendation** because it would partially reopen
  ticket 10 for no M1 gain. Cold start solved by *disabling reuse*, not weakening the gate.
  Scope narrowing: record evidence now (failure cases, CBR pattern), automate later — it needs
  ticket 13's classifier as a hard prerequisite. "False reuse rate" is properly **negative
  transfer**, and measuring it requires a matched no-transfer control arm.
- [Procedure lifecycle](issues/13-procedure-lifecycle-versioning.md) — **three orthogonal axes**
  (`verification_state` evidence-driven, `staleness` dependency-driven, `availability`
  circuit-breaker-driven), not the survey's single enum: a procedure can be verified *and* stale
  *and* quarantined, and an enum would let a dependency event overwrite hard-won verification
  evidence. Verification bar is **≥10 successes / 0 failures / ≥3 distinct contexts** — 3/3 gives
  a 95% CI of [0.48, 0.99] and the rule of three bounds its failure rate at 100% — but the bar
  gates *automatic* retrieval only; candidates stay explicitly invocable. Failure classification
  collapses spec.md's six categories to **four automated** ones (automated classification tops out
  ~F1 65%) with the finer distinction retained as evidence. Staleness uses package-version
  granularity accepting 20–30% over-invalidation, justified by asymmetry: over-invalidation costs
  revalidation, under-invalidation means reusing a stale procedure as verified. Circuit breaker
  and quarantine adopted with published parameters, as config. **Utility-based retirement added**:
  `utility = (freq × savings) − match_cost` retires correct-but-net-negative procedures, which
  nothing failure-driven would catch — this amends ticket 05's `verification_stats`.
- [Local retrieval hierarchy](issues/14-local-retrieval-hierarchy.md) — locality signals split by
  **derivation precision**, not uniformly: symbols, open files, import-derived dependency edges
  and related tests filter; recency, recent failures, and **name-resolved call-graph edges** rank.
  `call_graph.py` becomes a ranking feature, not a filter — at 75–85% precision a filter would
  permanently discard 15–25% of relevant results, while a ranking feature only misorders. Tiers
  **union and fuse**, not strict cascade (early-stage recall loss is the named antipattern for
  code retrieval); `RRF_K=60` kept — convex combination reportedly beats it but switching without
  measurement would trade a working default for an unvalidated one. Reranking **out of M1**
  (~5% NDCG@10 when the first stage is already hybrid+RRF+graph, at ~50ms p95 hot-path cost).
  Three query paths over one shared index; meta-ranker deferred since no M1 caller asks for
  cross-type ranking. Context budget in **tokens, hard cap, 8k default** with priority-ordered
  fill — this is what makes local-first enforceable rather than aspirational. Property-based tests
  for RRF invariants (monotonicity, idempotence, commutativity, stability) land before any
  extension of the untested `retrieval.py`.
- [HTN relocation](issues/15-htn-relocation-and-hyperparameters.md) — engine moves to
  `backend/app/execution/` with one entry point (procedure + bound params + state → execution
  record). **Sync core, async shell**, all I/O pre-fetched at the boundary — which turns today's
  `_pending_seed_plan` hack into the deliberate pattern. Restructured, not just relocated, per the
  owner's standing instruction: the three-class inheritance chain becomes one engine with a
  pluggable scheduler strategy, and per-run state moves to a `RunContext`. Hyperparameters split
  by **transferability** — structural limits (`MAX_DEPTH`, parallelism) transfer and carry over;
  budgets/thresholds (`MAX_SUBGOALS`, retries, step budgets, `MIN_VIABLE_SUBGOAL_BUDGET`) are
  distribution-sensitive, so they become config marked provisional/unvalidated for this domain.
  Planner is **phase-dependent**: default while cold (ticket 12), fallback once procedures verify
  — switchable, not baked in. **Match-cost-aware ordering** adopted as the utility-problem
  mitigation (cheapest-to-match first, fail-fast precondition ordering).

## Not yet specified

- **Option-span segmenter for procedure mining — currently unowned.** Ticket 11 establishes that
  episode boundaries (description-optimal) and procedure-extractable spans (extraction-optimal)
  genuinely differ. M1 builds episodes only. Procedure *mining* will need its own segmenter over
  subgoal/bottleneck/change-point boundaries; no ticket owns it, and ticket 05 settled procedure
  representation, not extraction.
- **Automated scope narrowing.** Ticket 12 records failure evidence (failure cases, CBR pattern)
  but defers automated version-space narrowing — it needs ≥3 failures, MDL, hysteresis, and
  ticket 13's failure classifier as a hard prerequisite.
- **Hosted-model drift detection.** Ticket 04's compound extraction key is necessary but not
  sufficient: a hosted model can change behaviour without changing its advertised ID. Detecting
  that needs output-distribution monitoring over a fixed calibration set.
- **Calibrated confidence via conformal prediction.** Ticket 04 stores no confidence field at all.
  If confidence is ever needed, the mechanism is conformal prediction against a calibration set,
  storing prediction-set size rather than a float.
- **Empirical-Bayes shrinkage for execution budgets.** Ticket 15 defers adaptive per-procedure
  budgets: they need ~30–50 executions per procedure type, which M1 will not supply.
- **Meta-ranker for cross-type retrieval.** Ticket 14 uses separate query paths per object type;
  cross-type ranking ("this claim matters more than that procedure") has no M1 consumer.
- **Convex-combination fusion as an RRF alternative.** Ticket 14 keeps `RRF_K=60`; the research
  reports convex combination outperforms it, but switching requires measurement this repo cannot
  currently do.

- **Materialization for temporal-range and full-history queries.** Ticket 10 established that
  point-in-time projection is an indexed lookup needing no materialization, but XTDB's warning
  about full-history reconstruction and temporal *range* joins stands. Milestone 1 needs neither
  query shape, so nothing is built. Resurfaces only if such a query shape becomes required, or if
  p95 projection latency breaches 100ms.

- **Claim write-volume risk against ticket 03's premise.** Ticket 03 placed claims in
  `knowledge_nodes` partly because "nothing produces a claim yet." Ticket 10's state-as-claims
  design changes that materially: potentially one claim per file edit and per test run, per
  episode. If that volume materializes, splitting claims into a dedicated table later means
  migrating rows *plus* every `edges` row referencing them via `source_table='knowledge_nodes'`.
  Cheap to revisit before production data exists; expensive after. Revisit when the first real
  claim producer (ticket 04) is wired.
- **Justification granularity is episode-level, not activity-level.** `claims.py` points a
  claim at an *episode* via `episode_links`, not at the specific extraction *activity* (PROV-O's
  `Activity` pattern, per the literature review §3). Answers "which episode produced this" but
  not "which extraction produced this, and was that extraction any good." Retrofitting once
  claims exist means backfill or two permanent provenance granularities.
- **Claim predicates have no namespacing.** `predicate` is a free string (`content_hash`,
  `last_run_outcome`). Fine with one domain; a second could collide (`status` meaning two
  different things). Cheap now as a naming convention (`coding.content_hash`), expensive once
  claims exist and need renaming across every row.
- **The ATMS "assumption environment" gap.** `claims.py` currently asserts a claim
  unconditionally; de Kleer's ATMS models a claim as believed only under an explicit
  assumption set (`repo, commit, branch, dependency_lock_hash, ...`). Deliberately deferred by
  ticket 03 since nothing produces a claim yet — no real usage pattern to design the shape
  against. Resurface once ticket 04 (Observation layer) actually starts producing claims.
- **`node_type` registry cleanup beyond `claim`.** Ticket 03 added a write-time schema
  registry for `node_type='claim'` only. The other 6 existing virtual types (`failure_mode`,
  `hierarchy_group`, `code_location`, `policy`, `policy_document`, `fact`) remain unvalidated
  TEXT-tagged rows. Real cleanup, deliberately out of this ticket's scope — revisit once it's
  clear which of those 6 are still live vs. dead weight.

- **Trace-header naming and the exact trace-boundary rule** — ticket 06 deliberately left the
  new trace-header table unnamed and treated "what starts a new trace" (one turn vs. one
  subagent run) as a multi-heuristic question rather than settling it, the same way spec.md
  treats episode boundaries. Sharpens into a ticket once episode assembly (ticket 11) is
  worked, since the two boundary questions are coupled.
- **The 6 mixed-classification services** (`agent_decision.py`, `agent_promotion.py`,
  `agent_review_orchestrator.py`, `content_diff.py`, `execution.py`, `method_library.py`) —
  ticket 02 deliberately left these unclassified against the coding/generic seam rather than
  guess. May sharpen once ticket 05 (procedure representation) lands, since
  `method_library.py` is central to that decision.
- **Evidence as a first-class thing.** No evidence table exists today; citations are resolved at query time and never persisted. Whether evidence becomes its own record, an edge kind, or a property depends on how claim/observation/procedure representation land.
- **Provenance/justification graph shape** — how "why do we believe this" is actually queried, and what makes it TMS-ready without being a TMS. Sharpens once claim and observation representations are fixed.
- **Procedure execution records** — the shape of what every reuse writes back (procedure version, bound parameters, initial state, concrete plan, deviations, cost). Depends on the procedure model and the HTN relocation together.
- **The reuse feedback loop** — how execution outcomes update reliability and scope without auto-rewriting a verified procedure after one failure.
- **Revalidation harness** — spec.md wants it internal-only initially, exposed as a test harness rather than an autonomous process.
- **API surface** — the concrete routes. spec.md sketches paths but says integrate with existing conventions (`/v1/...`, 8 existing routers).
- **Testing strategy** for the new layers — and what to do about the untested core that already exists (`retrieval.py`'s RRF arithmetic has zero coverage; `graph_store.traverse_from` is untested and marked NOT LOAD TESTED in-source).
- **Motif layer** — spec.md wants it eventually, non-executable, hypothesis-only, with supporting episodes and contradiction counts. Too far past the frontier to phrase sharply.

## Out of scope

- **The SWE-bench Pro experiment and its A/B/C/D arms** — the owner's call: build the substrate and the Claude Code layer, then revisit whether the benchmark is still wanted. Returns as a fresh effort if so, not as a resumption of this map.
- **RDF/OWL or any heavyweight ontology** — spec.md, explicit.
- **A complete TMS** — spec.md, explicit. Design so future retraction is possible; do not build propagation.
- **Mobile / personal-device state** — spec.md, explicit. The state model must merely not preclude it; that constraint lives in ticket 10.
- **Executable motifs** — motifs stay hypotheses; spec.md forbids making abstract motifs directly executable initially.