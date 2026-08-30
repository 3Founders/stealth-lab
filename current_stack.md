# Current Systems — Implementation Analysis

Per-subsystem paragraphs answering: what exists today, how it is actually
implemented (technology, process, data flow), and an honest read of strengths,
weaknesses, and scale implications. Companion to `trial_implementation.md`
(Part A summarizes status; this document carries the implementation depth).

---

## 1. Graph store — local PostgreSQL + pgvector

Everything durable lives in a **single local PostgreSQL instance** with the
pgvector extension. There is no ORM: all data access goes through raw `asyncpg`
pools (`app/db/session.py`) with hand-written SQL and a custom connection
initializer that registers codecs (the same initializer the τ² bridge reuses).
Schema management is a checksummed migration ledger (`scripts/migrate.py`
sorts filenames lexically, refuses edits to applied files) — twenty migrations
deep. The graph itself is three tables (`knowledge_nodes`, `task_nodes`,
`edges`) with bi-temporal columns (`t_valid`/`t_invalid`) on every row,
JSONB `properties` for extensible payloads, `VECTOR(1024)` embedding columns
with HNSW indexes, a constant-default `tenant_id`, and a `provenance` enum.
Claims are rows of `knowledge_nodes` with `node_type='claim'`.

*Analysis:* the choice buys transactional richness no graph database offers at
this size — recursive-CTE traversals with visibility predicates inline,
bi-temporal updates as plain UPDATEs, real ACID around the debate apply-path.
The costs are the ones we have catalogued: claims-as-JSONB blocks indexing on
predicate/object without expression indexes (only subject has one, migration
13), UUIDv4 keys guarantee index fragmentation under load, and the single
instance is a hard ceiling somewhere past hundreds of millions of rows.
Everything Phase III depends on (append-only discipline, scope keys) is absent
at the storage layer today.

## 2. API layer — FastAPI, thin over services

`app/main.py` boots a FastAPI application exposing the v1 surface (trace
ingest, admin scan, approvals, graph, chat, decompose, agents) as thin routers
delegating to service modules. Configuration is a Pydantic `Settings` object
loaded from `backend/.env` with a `require()` helper that fails loudly on
missing secrets. No authentication beyond an unverified `X-Viewer-Id` header;
visibility predicates are compiled into SQL wherever reads happen
(`services/access.py`).

*Analysis:* the layer is deliberately boring and that is correct — every
business rule lives in services, so transport can be swapped (the MCP server
already proves it). The weak point is identity: until the header is replaced,
multi-user anything stays blocked, which is why Phase II gates publication
features on the identity decision.

## 3. MCP server — the substrate's external face

`app/mcp_server/server.py` exposes the substrate over Model Context Protocol:
`find_best_way` (a full retrieval-grounded coding agent — RepoSandbox and the
tool-calling Agent reused verbatim from `experiments/swebench_pro/agent.py`),
`retrieve_precedent`, debate tools, and decomposition. After a successful run
it feeds the episode back through `extract_procedure()` — the first live
caller extraction ever had outside tests. Structural context comes from
`local_retrieval.retrieve_local_first()`.

*Analysis:* this is where the product boundary actually sits, and its known
rough edges are documented rather than hidden (system-prompt wording still
SWE-bench flavored; memory_block carries pointers, not the full trajectories
Experiment 4 validated; repo_path trust accepted-for-now). The event-loop
discipline matters here: one prior fix (commit 7203418) established that
nothing synchronous may block this loop — a rule the z3 path currently breaks
and the extraction LLM call skirts.

## 4. Retrieval — hybrid fusion plus local-first tiers

`services/retrieval.py` implements dense (pgvector cosine, HNSW) + sparse
(Postgres FTS) retrieval fused by reciprocal-rank fusion, followed by bounded
graph expansion that pulls knowledge nodes one hop from matched task nodes —
with hard exclusions for hierarchy aggregator nodes and PARENT_OF edges (both
fixes earned by measured failures: 321,821 nonsense conflict pairs; 193/324
expanded nodes lacking direct edges). `services/local_retrieval.py` layers
structural/temporal/semantic tiers with union fusion and token-budgeted fill.
Two embedding columns exist per node family (`embedding`,
`embedding_joint`); joint wins on sign test and is default.

*Analysis:* retrieval quality is the most rigorously validated part of the
codebase (leave-one-out holdouts via `t_invalid`, gold-set precision@1 runs,
sign-test evidence). Its weaknesses are operational: N+1 `project_state()` in
the applicability cascade, candidate_pool truncation quirks when preconditions
tie at zero, and the fact that ranking quality now silently depends on a
single embedding provider staying warm.

## 5. Embeddings — provider chain with budget governance

`services/embeddings.py` wraps a provider chain tried in order:
**Gemini** (`gemini-embedding-001`, up to 3 rotating API keys, task-type
mapping RETRIEVAL_QUERY/RETRIEVAL_DOCUMENT, MRL output-dimensionality 1024)
falling back to **Voyage** (`voyage-3-large`), then optionally local Ollama.
A process-wide cache (SHA-256-keyed, FIFO-bounded 20K entries, thread-locked)
serves repeats at ~0.03 ms. A cross-process TPM governor
(`embed_tpm_budget=25000`, rolling-minute window persisted in
`backend/logs/gemini_bucket.json`) keeps all callers — sweeps, backfills,
tests — inside the free tier's 30K tokens/min. Dimension mismatches fail at
the source against the schema's VECTOR(1024).

*Analysis:* this subsystem absorbed the worst production failure of the
experiment series (phaseH's Voyage starvation) and turned it into layered
defense: rotation, cache, fallback, pacing. What it lacks is structural:
vectors carry no model identifier in the database, so a provider switch is an
operational event coordinated by scripts and memory rather than by schema —
the reason `embedding_model_id` stamping leads Phase I.

## 6. Extraction pipeline — deterministic core, one bounded LLM call

`procedure_extraction/` runs evidence collection → deterministic derivation
(preconditions from `project_state()`, scope, slots via registered binders,
failure conditions from observations, run-length-encoded step skeleton) → a
single small LLM call producing only capability statement and step phrasing →
validators V1–V5 → `capture_procedure()` persistence plus a follow-up UPDATE
for migration-20 columns. Extractors are registry-versioned
(`procedure_extractors`, UNIQUE(name,version)) with an offline
`evaluate_extractor()` golden-set dry-run mode. Degradation is explicit: any
LLM failure falls back to `DeterministicExtractor` marked `used_fallback`.

*Analysis:* the derive-vs-assert split is the design's spine — derived fields
cannot fail V1 by construction, which converts validator coverage into a
guarantee about the generative surface only. The known defects are specific:
derived preconditions over-constrain (self-obsoleting library), invariants
have no authoring-time validation, and the follow-up UPDATE means two writes
where one schema-aware insert would do.

## 7. Applicability — non-compensatory filter cascade

`services/applicability.py` evaluates candidates cheapest-first (ordered by
precondition count) through a fixed cascade: temporal validity → staleness →
availability → verification_state → approval_status → scope match →
exclusions → structured preconditions (fail-closed under CWA via
`project_state()`) → numeric invariants last (z3, undecidable ≠ disqualified).
A cold-start gate (`should_disable_procedure_retrieval`) returns [] below one
verified+approved procedure. Short-circuit on first failure is deliberate.

*Analysis:* the cascade is the spec's ticket-12 made executable and its
ordering logic (filter before rank; rank never compensates) is defensible in
review. Its gaps are cost and tenancy: precondition checks trigger N+1 state
projections per request, z3 runs synchronously on the calling loop, the
cold-start gate counts globally rather than per tenant, and nothing bounds
solver time on adversarial stored expressions.

## 8. Trust layer — debate, conflict detection, mechanical grounding

`services/loop.py` orchestrates the panel/judge pipeline (models served via
General Compute / OpenAI-compatible endpoints) with Layer-1 groundedness
scoring and optional Layer-2. Conflict detection creates proxy task nodes +
CONFLICTS_WITH edges (`knowledge_conflict.py`) and injects *mechanically
computed* facts into debate prompts: date-overlap arithmetic
(`temporal_conflict.compute_overlap`), sentence-level content diffs, and the
target properties-schema — each added after a confirmed real failure it
prevents. Application flows through reversible ChangeSets
(`models/change.py`, `knowledge_update.KnowledgeUpdater`).

*Analysis:* the "compute in code, let the panel judge meaning" pattern is the
strongest idea in the codebase and generalizes far beyond banking. Weaknesses
are provenance hygiene (proxy nodes stamped `'company_debate'` before any
debate ran; seeded corpora stamped `'company_ingested'`), the absence of gold
labels for resolution correctness (groundedness ≠ correctness), and ChangeSet
reach not extending beyond the claim graph.

## 9. Execution — HTN DAG agent

`execution/htn_agent.py` plans a JSON DAG (2–6 nodes, explicit deps,
validated on parse: cycles broken, self-loops dropped, prose rejected) and
executes topologically with fresh message lists per node, localized replanning
(MAX_METHODS=2), failure containment to transitive dependents, and zero-budget
blocked nodes. It shares `RepoSandbox` and the `AgentRun` shape with the flat
agent so the harness cannot distinguish them — the control that makes
flat-vs-HTN comparisons meaningful.

*Analysis:* this is spec §24's Task DAG already built, minus persistence —
plans die with the run, so executions cannot reference an exact plan version.
Its telemetry (nodes done/failed/blocked, replans) is exactly the stream the
credit-assignment research (TRIAL/EFCA) wants fed into it.

## 10. Benchmark harness — τ²/τ³-bench integration

A separate editable install at `vendor/tau2-bench` (Python 3.13) drives the
substrate through retrieval variants: `resolve_variant()` → `build_tools()`
composes base banking tools with retrieval MixIns into one toolkit per
simulation thread. The StealthLab bridge crosses the sync/async boundary with
`asyncio.run()` per call over a bare asyncpg connection; `SubstrateSessionMixin`
overrides `use_tool()` (the toolkit's single dispatch point) to feed a
mechanical step-tracker, and enum values are extracted from the domain's own
tool schemas. Runner-level patches make failures deterministic-friendly:
BadRequestError skips retries, empty assistant messages become placeholders.
Phase launchers (`run_phase*.ps1`) pin tasks/models/concurrency and pull
credentials from the backend `.env`; results land as `results.json` per sweep.

*Analysis:* five phases of controlled comparison came out of this harness
(G 0.27 → K 0.417 on the 12-task smoke), and the instrumentation lessons are
real (retry math defeats naive timeouts; shared embedding quotas starve under
concurrency). Its fragility is environmental: Windows encoding quirks, free-tier
quota cliffs, and provider-side 400s all surfaced mid-sweep — which is exactly
why the runner now treats determinism as a first-class property.

## 11. Frontend & operations

The Next.js frontend (`frontend/`, workbench/approvals/archive/tasks/
visualize routes) consumes the API read-mostly; a `render.yaml` exists but the
README is explicit that nothing is public-deployable (identity blocker).
Operations today are local-first: Windows dev machine, local Postgres, General
Compute GPU endpoints for model serving, `.env`-driven configuration, no CI,
no metrics stack — observability is pytest (345+ passing per TEST.md),
structured logs, and the phase-launcher logs. `backend/logs/` (gitignored)
holds runtime sidecars including the TPM bucket.

*Analysis:* appropriate for the stage — premature ops infrastructure would be
cost without signal. The two things worth pulling forward out of order are a
minimal CI gate (pytest + the migration ledger applying cleanly to a scratch
database) and the metrics habit around retrieval latency/quota burn, because
both protect Phase I work from silent regression.

---

## Cross-cutting summary

Three properties recur across every subsystem and should be treated as the
codebase's constitution: **failures are loud and named** (dedup keys, require(),
EmbeddingError chains, loud failover logs); **measurement precedes mechanism**
(every guard in the cascade traces to a numbered test and usually a quoted
incident); **determinism where judgment isn't needed** (mechanically computed
facts injected into prompts, whitelisted parsers, template libraries). The
implementation medium — one local Postgres, raw asyncpg, FastAPI services, a
separate benchmark install, free-tier cloud APIs governed by budgets — was
chosen to keep every experiment reproducible at near-zero marginal cost, and
every scaling decision deferred until a measurement demanded it.
