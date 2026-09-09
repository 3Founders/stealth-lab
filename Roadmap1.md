# Roadmap1.md — System-by-System Scope, Explanation & Future Roadmap

Companion to `ROADMAP.md` (band plan). This file scopes each subsystem of
StealthLab, explains how it works today, where it is going, and cites the
external research landscape gathered via Exa (web) and OpenAlex (scholarly)
on 2026-08-26.

---

## S1 · Contracts boundary — `v0_gate.py`
**Scope:** every producer (hooks, HTTP ingest, seed, capture, debate, extraction)
crosses one validator before persistence.
**Today:** `validate_scope()` enforces the 10-vocabulary scope rules;
`validate_provenance()` enforces attribution (+ extractor version for derived
objects); `V0Violation` is the canonical rejection. Storage columns are nullable
only so THIS module owns error messages.
**Future:** per-org policy profiles (what scopes an org's keys may write);
quota-aware rejection taxonomy; rejection telemetry feeding the failure queue.

## S2 · Procedures lifecycle — `services/procedures.py`
**Scope:** versioned procedure objects: capture → candidate → evidence →
verified → superseded/retired; utility accounting.
**Today:** `capture_procedure` (V0-gated, UUIDv7, embedding stamps),
`record_execution_outcome`, `check_quarantine_and_disable` (auto-quarantine on
repeat failure = capability decay), `compute_utility` +
`retire_negative_utility_procedures` (§23b), approve/reject human gates.
**Future:** capability bands per implementation (D1 ratified tiers wired into
routing), cross-domain capability statements (migration 20's registry), A/B
supersede workflows with ChangeSet receipts on every mutation.

## S3 · Execution plans & HTN — `execution/plans.py`, `execution/htn_agent.py`
**Scope:** instantiation produces frozen ExecutionPlans bound to exact procedure
versions; HTN planner schedules nodes under structural/distributional budgets.
**Today:** canonical hashing (`hash_procedure_version`), `validate_graph`
(acyclic, scope-narrowing-only), born-frozen tables + engine triggers,
Sequential/ConcurrentBatch schedulers, `ProcedureRef` exact-version binding.
**Future:** SLM-vs-frontier per-node routing driven by measured capability
(D1 tiers at node granularity), verification-gate placement at fan-out points,
plan replay/rebind determinism proofs against live engines, scheduler strategies
learned from outcome streams.

## S4 · Truth maintenance — `state.py`, `claims.py`, `knowledge_conflict.py`
**Scope:** bi-temporal claims graph; tombstone projection; conflict detection.
**Today:** `project_state` (OUT claims never reach prompts), `capture_claim`
(SPO shape + belief), `find_conflicting_knowledge` → triggers → debate loop;
bi-temporal columns keep history queryable while projections stay clean.
**Future:** full dependency fan-out on revision (Band 4.5 — currently claim-only
propagation), RippleEdits-style ripple % as a standing metric, contradiction
dominance + effective-n bounding in aggregation, freshness-decay scheduling (§37).

## S5 · Retrieval & applicability gating — `retrieval.py`, `applicability.py`, `precondition_gate.py`
**Scope:** hybrid recall (RRF over lexical + pgvector HNSW + graph expansion),
then fail-closed applicability cascade producing ALLOW or structured WOULD_REFUSE.
**Today:** `HybridRetriever.fuse_rrf`, `_belief_filter` (stale claims excluded on
every leg — Band 2.7), memoized `project_state` cascade (1.8c),
`check_hard_constraints`, tenant/visibility predicates in every path.
**Future:** D1 tier thresholds applied to retrieval ranking (offer vs auto),
leave-one-out sanity as CI gate [T-24 pattern], learned fusion weights,
cold-start tenant scoping hardening, abstention quality metrics.

## S6 · Extraction pipeline — `services/procedure_extraction/*`
**Scope:** traces → candidate procedures via registered extractor versions;
strategy-level induction only (task-level skills harm agents — AFTER finding).
**Today:** schema-first Pydantic shapes, `DeterministicExtractor` +
`GroundedHybridExtractor`, load-bearing precondition filter (1.8a), validators
V1–V6 (V6 = authoring-time invariant authoring check, z3-backed), registry with
versioning/approval/stats enabling multi-model pooling (AFTER: 73.1% cross-model).
**Future:** domain packs (QuantAsm asm kernels; MechRankings fact pipelines after
ingest instrumentation), extraction gold-set harness before corpus scaling
(contracts-before-corpus rule), error-floor instrumentation (MEASURE lane started),
ablation of strategy vs grounded arms.

## S7 · Ingestion pipeline — `trace_collector/trace_redaction/trace_worker/ingestion_jobs/failure_capture`
**Scope:** client-side O(1) append (locked, redacted, deduped, bounded) →
worker drain → durable [H] rows; job queue with quarantine + requeue;
failures become routable first-class objects.
**Today:** `append_event` lock-timeout backpressure, drop counters surfaced into
`agent_traces.collector_drop_count`, per-line quarantine, stuck-job requeue,
`capture_failure` feeding Band 2.4 route handlers (capability_demotion,
applicability_narrowing, dependency_queue, requires_review) with ChangeSet-ledger
idempotency.
**Future:** OTel GenAI span export (commLLM §7 sinks), multi-agent session
correlation, hook SDK parity across Claude Code/opencode/Codex, backpressure
dashboards, redaction regression corpus.

## S8 · Embeddings — `embeddings.py`, `embed_cache.py`
**Scope:** text → vectors with provider resilience + cost governance.
**Today:** Gemini 3-key rotation → Voyage fallback chain, cross-process TPM
bucket (rolling-minute ledger), content-keyed cache, `to_pgvector` serialization.
**Future:** model-migration stamps already in place (migration 21) → routine
re-embed campaigns, matryoshka/truncated-dim experiments, local embed tier via
FreeToken-class runtimes, drift detection between stored vs current model.

## S9 · Governance — `governance.py`, `access.py`, migrations 28/29
**Scope:** rate limits, spend ceilings, tenancy, visibility, RLS backstop.
**Today:** token-bucket limiter with buffered ledger writes (H3), CostGovernor
with static price-table estimates, org/user/membership/api_keys identity (28),
RLS on [H] truth tables with fail-closed GUC policies (29), visibility predicate
builder choke point.
**Future:** per-org policy profiles, spend attribution dashboards, partition/
TTL retention automation for telemetry tables, RLS extension to remaining [V]
tables, SSO/OIDC subject mapping into memberships, audit-log export.

## S10 · Product surface — `mcp_server/server.py`, packaging, frontend
**Scope:** what users touch: MCP tools, installable package, UI shell.
**Today:** retrieve_precedent / ingest_trace / check_procedure(WOULD_REFUSE audit
mode) / debate trio / solve_task(RepoSandbox); apply_change_set opt-in-gated;
stealthlab-connect console entry points (P1 shipped); Next.js thin client.
**Future:** explain_failure/explain_decision receipts (blocked on execution-graph
consumers), multi-client conformance matrix, registry/distribution listings,
demo fixture as scripted acceptance test (per demo.md C1–C5).

## Cross-cutting future roadmap (bands)
| Band | Theme | Status |
|---|---|---|
| 1 | Contracts born-correct (gate, plans, evidence, changesets) | **CLOSED by review** |
| 2 | Failure→capability loop (routes, TMS-in-retrieval 2.7, 2.4 handlers) | largely landed |
| 3 | Measurement: gold-set extraction harness, skill-file variants (**phaseO collapse alarm stands** — no variant sweeps until post-mortem) | open |
| 4 | Full dependency fan-out on any object revision; invalidation storms | open |
| 5 | Deletion = crypto-shredding w/ visible shells (D4); residency | open |
| P | MEASURE+SHIP: scoreboard w/ discordant-pair stats, P2 status surface | partial |

---

# External Research Annex (Exa + OpenAlex, 2026-08-26)

## → S6 Extraction (Exa)
- **Skill-DisCo** (arXiv 2606.26669) — skills as parameterized FSM subgraphs distilled
  from traces + compiled into *verifiable* callables; spec includes pre/postconditions.
  Validates our step-group skeleton + V-validator design; suggests PFSM-style control-flow
  clustering as a future strategy class.
- **Trace2Skill** (2026) — parallel analyst patches from success/failure trajectories,
  many-to-one consolidation into SOPs; beats ReasoningBank-style episodic retrieval;
  cross-scale transfer (35B→122B +57.65pp). Supports multi-model pooling thesis (AFTER).
- **SKILL-KD** (arXiv 2607.28048) — contrastive teacher/student skill distillation with
  trace-linked edit histories and drift-aware consolidation — direct prior art for our
  registry supersede/approve lifecycle.
- **Memp** (ACL Findings 2026) — step-level + script-level procedural repository,
  continuously updated/deprecated; memory built by stronger models transfers to weaker.

## → S4 Truth maintenance (Exa + OpenAlex)
- **TEPA** (arXiv 2608.07429) — revocable keyed precedents; append-only memory falls
  BELOW no-memory under world reversal (0.21 vs 0.31); revocation restores 0.95.
  Strongest external validation yet for tombstone-over-overlay (our A1 assumption).
- **Quipu** (arXiv 2608.16813) — governed bitemporal store: gate-before-write, signed
  verdicts on denials, rules as bitemporal facts; "start strict; agents bear the cost of
  strictness." Directly mirrors V0-gate philosophy; their audit-as-query is a roadmap idea.
- **Kumiho** (arXiv 2603.17244) — AGM belief-revision semantics mapped to graph memory;
  immutable revisions + Supersedes edges + tag pointers ≈ our procedure/claim versioning.
- OpenAlex: *TraceGraph* (2026, versioned provenance-linked evidence graphs),
  *Temporal Data Management overview* (2018) anchor the bi-temporal lineage lineage.

## → S5 Retrieval/refusal (Exa + OpenAlex)
- **Evidence-calibrated RAG** (JTIE 2025) — calibrated evidence sufficiency cuts
  hallucination-proxy 77.8%→37.5%; coverage alone insufficient. Maps to our
  precondition_gate needing its own sufficiency calibration, not just thresholds.
- **AB-RAG** (2606.29090) / **Know Before You Fetch** (2606.29959) — confidence-gated
  retrieve-or-abstain budgets; calibrated probability as the reusable interface.
  Future hook: D1 offer-tier could gate *retrieval depth*, not just routing.
- OpenAlex anchors: *Survey of Confidence Estimation & Calibration in LLMs*
  (NAACL 2024), *CalibJudge* (SIGIR 2026).

## → S1/S9 Contracts & governance (OpenAlex)
- Fulltext search surfaced scale literature only (agent surveys 1.4k+ cites);
  governance-specific scholarly coverage remains thin — consistent with our moat claim:
  nobody publishes write-gate + RLS + spend-governance as one substrate contract.

## → S3/S7/S8/S10 (coverage note)
Exa sweep for execution-routing/ingestion/embeddings deferred this pass (budget);
nearest neighbors already captured in `commLLM.md` §3A/D (AgentTrace, eIRWR, Log-Insight)
and §2 (OTel GenAI conventions). Next research pass should target:
verification-gated SLM routing, append-only ingestion at scale, embedding model migration.
