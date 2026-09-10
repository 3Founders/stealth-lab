# StealthLab — Ingestion + Knowledge Hardening Audit (Plan A / Gates G0–G14, G23–G24)

**Lane:** INGESTION + KNOWLEDGE architecture hardening.
**Spec audited against:** `STEALTHLAB_EXTREME_FINAL_HARDENING_V4.md` (Part II Plan A, Part II-A §1–§38, Part IV G0–G14/G23/G24, Part VI, Testing T1–T15).
**Repo revision at audit:** `main` @ `1663c94` for the original audit; Implementation Pass 1 rebased onto `main` @ `301b9bf` (incorporates upstream `976b647` "Global Internet Ingestion admission gate", which took `db/49`, so Pass 1's migrations are `db/50`–`db/55`).
**Environment / DB:** two Postgres targets, switched via `backend/scripts/dbtarget.{py,ps1,sh}` (`DATABASE_URL_LOCAL` ⟷ hosted `DATABASE_URL`; see `backend/DB_TARGETS.md`).
- **hosted** (Supabase, egress-limited): migrations 50–55 applied; `migrate.py --status` → 01–55 all applied.
- **local** (native Postgres): schema built migration-by-migration; **migrations 50–55 verified applied from scratch** — all 6 new tables present, `procedure_implementations` carries all 6 new columns, `ingestion_context_id` back-link on all 9 target tables, ledger = 55 rows. (Pre-existing MISMATCH warnings on migrations 11–34 are cosmetic: ledger checksum written before `migrate.py` added CRLF→LF normalization vs the CRLF checkout — schema is correct.) **T2** (`test_migrations_50_55_t2_e2e.py`, 8 passed) and **T3** (`test_ingestion_canonical_chain_e2e.py`, 2 passed) verified against local; `test_schema_drift.py` → 2 passed (no drift).

Offline-suite progression (`DATABASE_URL` unset): baseline `2556 / 360 / 7` → Pass 1 `2736 / 360 / 7` → Pass 2 `2778 / 360 / 7` → Pass 3 (`035adf6`) + G1 trace `2784 / 361 / 6` (the extra skip is `test_migration_upgrade_e2e` correctly skipping with DATABASE_URL cleanly unset; +228 passing across all passes, **zero regressions**).

**Verdict:** `EXTREME FINAL HARDENING INCOMPLETE` — but **Implementation Pass 1 has landed** (see the next section). The program is 29 gates (G0–G28) + 34 A-phases + 15 test categories; Pass 1 closes or advances 8 of the ingestion+knowledge gaps identified below, as additive migrations (now applied) + writer rewiring + offline proving tests. **No gate is fully CLOSED** because "CLOSED" per the spec requires the *whole* DB/E2E surface (T2–T14) green — T2 and T3 now pass against local; T4/T6/T8–T14 and a corpus-loaded backfill remain. The per-item state below says exactly what remains.

---

## IMPLEMENTATION PASS 1 — what landed (2026-09-10)

**Merged-tree offline suite: `2688 passed / 360 skipped / 7 failed`** (`python -m pytest tests -q`, `DATABASE_URL` unset, 305 s). The 7 failures are byte-identical to the documented baseline-7 (embedder `_embed_via_chain` rename ×3, `test_mcp_six_tool_surface` `fake_find()` signature ×2, `test_injection_adversarial` test-setup bug ×1, `test_migration_upgrade_e2e` needs a DB ×1). **Zero regressions. +132 passing tests** from ~170 new offline tests.

### New migrations (additive + idempotent, `IF NOT EXISTS` throughout, NO in-migration backfill — APPLIED to the hosted DB; T2 tests still owed)

| File | Adds | Gate |
|---|---|---|
| `db/50_sources.sql` | `sources` origin registry (`source_kind` enum, identity `UNIQUE (source_type, locator, publisher)`, `reliability_score`/`_method` kept separate from claim belief); `ingested_artifacts.source_ref` | G2 / B12 |
| `db/51_ingestion_contexts.sql` | `ingestion_contexts` (the §A1 field list — actor/workspace/scope/classification/extractor identity); `ingestion_context_id` back-link column on `ingested_artifacts`, `observations`, `procedures`, `evidence`, `knowledge_nodes` | G1 / B3 |
| `db/52_procedure_claim_refs.sql` | `procedure_claim_refs` typed relation (8-role vocab, `UNIQUE (procedure_id, procedure_version, claim_id, role)`, partial strong-role index) | G8 / B4+B5 |
| `db/53_procedure_implementation_relation.sql` | generalizes `procedure_implementations` — `role`/`implementation_version[_constraint]`/`supported_steps`/`supported_capabilities`/`applicability`/`interface_binding`/`evidence_refs`/`status`/bitemporal; drops the old 2-col UNIQUE for a partial `(procedure_id, implementation_id, role)` identity index | G10 / B6 |
| `db/54_screening_decisions.sql` | `screening_decisions` (`ALLOW`/`QUARANTINE`/`REJECT` + `check_type` + `detector`@`detector_version` + `signals` + `reason`, auditable, not-deleted) | G3 / B14 |
| `db/55_artifact_blocks.sql` | `artifact_blocks` — immutable normalized blocks with char `source_start`/`source_end` offsets into `artifact_content_hash`, heading nesting, stable `h{n}-{slug}` anchors | G2 / B16 |

### New services (offline-tested)

| Module | What | Tests |
|---|---|---|
| `services/sources.py` | `register_source` (identity dedup via `ON CONFLICT`), `get_source` | `test_sources_offline.py` (8) |
| `services/ingestion_context.py` | `open_ingestion_context` / `complete_ingestion_context` / `get_ingestion_context` | `test_ingestion_context_offline.py` (9) |
| `services/procedure_claim_refs.py` | `add_procedure_claim_ref`, `list_procedures_for_claim`, `close_procedure_claim_refs`, `backfill_refs_from_preconditions` (idempotent, explicit — not run by a migration), `STRONG_ROLES` | `test_procedure_claim_refs_offline.py` (15) |
| `services/procedure_implementations.py` | `bind_implementation`, `list_implementations_for_procedure`, `list_procedures_for_implementation`, `close_binding`, `add_evidence_ref`, `ROLES` | `test_procedure_implementations_offline.py` (18) |
| `services/screening.py` | pure `screen_document_text` (reuses existing injection + secret detectors — imported, not reimplemented), `decide`, `record_screening_run`, `get_screening_decisions` | `test_screening_offline.py` (19) |
| `services/artifact_blocks.py` | pure `normalize_markdown` / `normalize_text` (offset-preserving), `persist_artifact_blocks`, `get_artifact_blocks`, `get_block_span` | `test_artifact_blocks_offline.py` (23) |
| `services/runtime_guard.py` | `assert_production_safe` / `is_production_safe` / `RuntimeGuardViolation` — STAGING/PRODUCTION abort startup on a fake embedder / no-op auth / missing mandatory credential / in-memory DB substitute | `test_runtime_guard_offline.py` (19, incl. the T1 proving test) |

### Rewired existing writers

- **`services/skill_ingestion.py`** — `compile_skill_artifact` now runs the canonical chain on the `captured`/`new_version` outcomes: `register_source(type=document)` → `open_ingestion_context` → `capture_procedure` → stamp `procedures.ingestion_context_id` → emit one `document_procedure` **Observation** → emit one `evidence(type='document', strength 0.3, independence_group='skill_md:'+hash)` row → `complete_ingestion_context`. `ingested_artifacts` now carries `source_ref` + `ingestion_context_id`. **B2:** both `_write_task_nodes` call sites removed (grep-confirmed no other callers) — ingestion no longer manufactures task_nodes. `_persist_package_relations` `ON CONFLICT` retargeted to migration 53's `(procedure_id, implementation_id, role) WHERE t_invalid IS NULL`. **Merged with upstream `976b647`'s admission gate** (`classify_admission` — `reject`/`review`/`admit`): the admission decision runs first; the canonical chain runs for `admit` and `review` (a `review` outcome writes the procedure `availability='quarantined'` but still fully provenance-tracked), a `reject` returns `status="rejected"` with an `ingested_artifacts` audit row and no Source/context. `IngestOutcome` carries both feature-sets' fields. Tests: `test_skill_ingestion_offline.py` (chain + admission both asserted); `test_ingestion_admission_offline.py` (upstream's, still green).
- **`services/claims.py::capture_claim`** — **B7:** new kwargs `source_ref` / `ingestion_context_id` / `observation_id`; the claim is written when **any** anchor (task/episode) **or** any provenance ref is present; a truly unprovenanced claim is still a logged silent no-op. Writes `knowledge_nodes.ingestion_context_id` and a `claim_sources` row when given. Existing callers unaffected. Tests: `test_claims_offline.py` (4).
- **`services/claim_impact.py`** — **B5:** `find_procedures_referencing_claim_grouped` returns `{strong, explanatory}` from `procedure_claim_refs` UNIONed with the verbatim legacy `preconditions @> …` scan (compat, de-duped by version row id). `propagate_claim_change` now marks stale **strong-role refs only**, returns explanatory refs untouched (`{marked_stale, explanatory_untouched}`). Flat `find_procedures_referencing_claim` kept for the out-of-lane `claim_graph_api` caller. `claims.py::relate_claims` call site adjusted; `test_claim_impact_e2e.py` assertions updated for the dict return. Tests: `test_claim_impact_offline.py` rewritten (13).
- **`services/claim_evidence.py::record_claim_evidence`** — **B10:** now accepts `independence_group` (was impossible to pass → every claim-evidence row counted as independent, inflating claim corroboration counts). Tests: `test_claim_evidence_offline.py` +3.
- **`services/solution_implementations.py`** — reads the generalized `procedure_implementations` relation, unioned with the legacy `implementation_tasks` path, de-duped by `implementation_id`, each row tagged `source`/`role`. Returns non-empty for a directly-captured procedure with bindings (previously always `[]`). Tests: `test_solution_implementations_offline.py` (5), `test_implementation_api_offline.py` fixture extended.
- **`config.py` / `main.py`** — `settings.environment` is now a computed `TEST|STAGING|PRODUCTION` property (fail-closed: unset/garbled ⇒ PRODUCTION; `PYTEST_CURRENT_TEST` ⇒ TEST); `main.py` lifespan calls `assert_production_safe(settings)` first.

---

## IMPLEMENTATION PASS 2 — what landed (2026-09-10, commits `bb2deba` / `71e0322` / `acbd2f5`)

Wires the Pass 1 building blocks into the live paths and adds the two engines Pass 1 left OPEN. Offline suite on the integrated branch: **see §J** (zero regressions vs the Pass-1 branch baseline of 2736/360/7). New offline tests: ~40.

- **B1 completed — `skill_ingestion.compile_skill_artifact`** (`bb2deba`) now, on `captured`/`new_version`: persists **`artifact_blocks`** (`normalize_markdown` → `persist_artifact_blocks`, stamped `ingestion_context_id`); records a **`screening_decisions`** audit row per finding (`screen_document_text` → `record_screening_run`) *alongside* the existing injection-screen downgrade (unchanged); derives **one document Claim** (`capture_claim` with source/context/observation provenance) linked to the procedure via `add_procedure_claim_ref(role="RATIONALE", ref_origin="derived")` — explanatory role, so a change to it never auto-invalidates the procedure (B5). `IngestOutcome` += `artifact_block_ids`, `screening_decision`, `screening_decision_ids`, `document_claim_id`. `run_skill_ingestion` metrics += `artifact_blocks`, `screening_quarantine`, `screening_reject`, `document_claims`. A screen `REJECT` logs a warning but does **not** abort capture (policy decision deferred, noted in code). **Known limit:** blocks are normalized from raw `artifact.content` (not the redacted `parsed.*`) — a review-tier doc with a secret literal would land that literal in `artifact_blocks.text` (screening rows stay clean). Block-level redaction is a follow-up.
- **B8 — `services/claim_belief.py`** (`71e0322`, new): `compute_belief(evidence_rows)` — pure, evidence-only, mandatory independence de-dup (a shared `independence_group` counts once at its max strength), evidence-class weights (`execution_result`/`reproduction`/`benchmark`/`experiment` = 1.0 ≫ `document`/`external_source`/`human_review` = 0.5 ≫ `observation`/`artifact` = 0.3), a saturating score that never reaches 1, and an **explicit document-evidence ceiling of 0.5** (ten "the author says X" rows still cap at 0.5 — a recommendation is never promoted to experimental truth). Zero evidence → 0.1 floor. **No `confidence`/`model_confidence` input is ever read** (test-enforced). `recompute_claim_belief` writes `knowledge_nodes.belief_score`/`belief_method`/`claim_status` **through a ChangeSet whose detail cites the evidence ids**. Hooks (best-effort, `try/except` → `log.warning`, never roll back the committed write): `record_claim_evidence` (after the evidence INSERT), `relate_claims` (after a CONTRADICTS/SUPERSEDES edge), `list_current_claims` (SELECT now returns the belief columns).
- **B9 — `claim_status` convergence** (`71e0322`): `status_from_belief(belief, has_open_conflict)` makes `claim_status` a **derived projection** of belief + conflict state for the evidence/relation paths. **Known limit:** `procedure_extraction/failure_handlers.py` still writes `claim_status` directly on its failure-routing paths (out of scope) — convergence is partial.
- **B11 / G24 — `services/publication_deps.py`** (`acbd2f5`, new, ~540 lines): `traverse_publication_dependencies` walks procedure → `procedure_claim_refs` → claims → `claim_sources` → observations → `sources` (via claim/context/artifact `source_ref`) → `ingested_artifacts` + `artifact_blocks` → `evidence` (`target_type` procedure *and* claim). Fail-closed: any `private`/`org` object, `PRIVATE_CLASSES` classification, unresolved/low-reliability (`< 0.3`) source, or a hit on the 500-node traversal bound → `blocking`. **"Private evidence ≠ global verification" (A14):** a private evidence row blocks; a lone (non-independent) public `execution_result` is non-blocking but flagged `independent=false` with a `private_evidence_not_global_verification` note; `verification_inherited` is always `false`, `global_verification_required` set unless ≥2 independent public verification groups exist. Wired into `publish_procedure` (every existing gate kept); `publication_records.dependency_report` now carries the full traversal, `classification_report` carries the verification determination. No auto-promotion — the global candidate still starts `candidate`.

### Pass 3 (`035adf6`, ChaitIITB — concurrent) — closed several Pass-2 OPEN items

- **G9 done** — `app/execution/procedure_graph.py::validate_procedure_composition_definition` / `..._in_storage`: a WRITE-time gate on canonical procedure composition — rejects cycles (`ProcedureCompositionCycle`), unresolved pinned sub-procedure refs (`UnresolvedSubprocedureRef`), and `max_depth` overflow. Wired into `capture_procedure` / `supersede_procedure`. Tests: `test_procedure_composition_e2e.py`, `test_procedure_graph_offline.py` (+45).
- **B15 done** — `ingestion_jobs.py`: `_PENDING_EXTRACTION_SQL` now requires a real source-supplied `goal_text IS NOT NULL` (no more `"Recurring engineering task observed in this episode"` placeholder); `handle_extract_procedure_from_episode` raises on a payload missing `goal_text` or with `outcome != "success"`. The fabricated-goal / post-hoc-retire path is gone.
- **B1 residual done** — `screening.redact_document_text` + `artifact_blocks.redact_blocks_for_persistence` redact secret-shaped content from the derived `artifact_blocks.text` **never from the raw Artifact**; `skill_ingestion` calls it before persisting blocks and logs the redacted pattern classes. `skill_ingestion._attach_observation_block_ref` stamps the document Observation's `properties` with `{artifact_id, artifact_block_id}` — the Observation is now cited to a specific block.

### Still OPEN after Pass 3

- ~~**T2**~~ **done** — `test_migrations_50_55_t2_e2e.py` (8 passed against local): additive-only check on all 6 files, idempotent re-run of each `db/5N_*.sql`, representative-row insert + named-CHECK rejection for every new table. `test_schema_drift.py` → 2 passed (no drift). Rollback is intentionally not automated (fresh-start rule 1), documented.
- ~~**T3**~~ **done** — `test_ingestion_canonical_chain_e2e.py` (2 passed against local): a real procedural SKILL.md → all 12 chain rows verified (Source → IngestionContext → Artifact → artifact_blocks → Observation → Evidence(document) → Claim → procedure_claim_ref(RATIONALE) → screening_decision → Procedure(candidate)), every derived row `ingestion_context_id`-stamped, **no task_nodes**; a non-procedural doc → no Procedure.
- **A33 corpus backfill** — `backfill_refs_from_preconditions` still not run against real corpus data (local DB is schema-only). Needs a corpus-loaded DB.
- **DB-backed regression sweep** — the full `dbtarget local -- pytest tests` run (offline + ~360 `*_e2e.py`) has ~35 pre-existing e2e failures from missing env (LLM keys, OIDC auth) / fixture drift / Plan B — none from this lane's work (triaged; only `test_skill_ingestion_e2e`'s B2 assertion was ours, fixed).
- ~~**G1 residual**~~ **done** — `ingestion_jobs.resolve_trace_ingestion_context` opens one `IngestionContext` per trace **session** (`source_type='trace'`, `source_uri='session:<id>'`, resolved from the DB so it is idempotent across workers). `handle_normalize_trace_event` stamps each Observation and threads the id onto the promote job; `handle_promote_observation_to_claim` stamps the derived Claim; `handle_extract_procedure_from_episode` stamps the Procedure version row and its procedure-targeted Evidence. Both ingestion paths now carry `ingestion_context_id`.
- **B9 partial** — `failure_handlers.py` still writes `claim_status` directly (lifecycle-vs-belief-projection boundary not yet settled).
- **G12 / G13 / G14** — local/private sync (A11–A13), `.stealth/` projection contract, retrieval index-lag tracking — none touched.
- **Board note owed** — `schema.md` `Implementation → Procedure` 1:1 vs Pass 1's M:N (`schema.md` frozen).
- **External:** `backend/app/config.py` has an uncommitted `embedding_provider_chain` change from another lane (not part of this work; left untouched).

---

## A. Baseline architecture discovered

### A.1 Ingestion entry paths (two, structurally distinct)

| Path | Entry | Shape | Persists |
|---|---|---|---|
| **Trace / execution-derived** | `trace_worker.process_collector_file` → `ingestion_jobs.process_pending_jobs` → `normalize_trace_event` → `promote_observation_to_claim` → `extract_procedure_from_episode` | `trace_events` → `observations` → claim (`knowledge_nodes`) → `procedures` | Events, Observations, Claims, `claim_sources`, `episode_links`, Procedure (candidate), synthesis (L2) |
| **Document / SKILL.md** | `scripts/ingest_skills.py` / `ingestion_jobs.handle_ingest_skill_package` → `skill_ingestion.compile_skill_artifact` (or `ingest_skill_md` for raw strings) | `SourceArtifact` → `parse_skill_md` → `capture_procedure()` **directly** | `ingested_artifacts` (provenance side-table), `procedures` (candidate), `task_nodes` (one per step), `procedure_implementations`/`procedure_dependencies` (skill-package only), `ingestion_runs` manifest |

### A.2 Canonical objects that exist today

- **Event / Trace / Episode** — `trace_events`, `agent_traces`, `episodes`, `episode_links` (mig 12, 17, 26). Real, with bitemporal columns.
- **Observation** — `observations` + `observation_events` (mig 14, 21). `extractor_kind` CHECK `deterministic|model`; extraction identity stored as components (`extractor_name`, `code_version`, `model_id`, `prompt_hash`, `decoding_params_hash`), **not** one hash. **Deliberately no `confidence` column** (`14_observations.sql:29-33`). Service: `observations.py` (`extract_deterministic_observations`, `extract_model_observation`, `persist_observation`, `promote_observation_to_claim`). `persist_observation` does **not** dedup (immutable-observation design).
- **Claim** — NOT a dedicated table; a `knowledge_nodes` row with `node_type='claim'`. Structured columns `subject/predicate/object/proposition_type/claim_status/belief_score/belief_method/valid_from/valid_until` added by mig 21; `claim_status` CHECK vocabulary `candidate|supported|disputed|uncertain|stale|superseded|invalid|retracted`. Service: `claims.py::capture_claim(...)` — V0-scope-checked (optional), computes an embedding, inserts `node_type='claim'`, `provenance='company_ingested'`. **Anchoring:** resolves `task_ids` against live `task_nodes.skill_ref`; if none resolve **and** no `justification_episode_id` → **returns `None` and silently drops the claim** (`claims.py:183-188,268-269`) — a soft no-op, not a DB constraint, not a raised error.
- **ClaimFamily** — `knowledge_nodes` `node_type='claim_family'` + `edges` `OWNS/FAMILY_MEMBER`. Service `claim_family.py` (resolver v0). Verdicts `same_family|related_family|generalizes|specializes|distinct`. Only `same_family` is **persisted** in v0; the rest are returned, not stored. Claims are **never physically merged**. Contradiction dominates the cascade.
- **Claim relations / TMS** — `claims.py::relate_claims` / `link_claims`. `SUPERSEDES`, `CONTRADICTS` flip the *target's* `properties.truth_state` to `OUT` (row stays live, queryable as history). `SUPPORTS/REFINES/DEPENDS_ON/CONDITIONAL_ON/GENERALIZES/SPECIALIZES/DERIVED_FROM/INSTANTIATES/APPLIES_TO` are side-effect-free edges. `Dependency Index` (schema.md) = typed `edges`. Contradictory claims **coexist** (confirmed).
- **Evidence** — `evidence` table (mig 24; view fix mig 34; verified-requires-evidence trigger mig 30). `evidence_kind` ENUM (9 types incl. `document`, `human_review`, `reproduction`). Polymorphic target `claim|procedure|implementation`; `direction supports|contradicts`; `strength_score [0,1]` + non-blank `strength_method` (both NOT NULL); `independence_group` (NULL = self-grouped, blank rejected); `success_criteria` — a `success` row with `'{}'` is **rejected** by CHECK (`evidence_success_criteria_chk`) — bare self-report unwritable; `failure_class` 7 values incl. `false_reuse`. Append-only: DELETE rejected, only `t_invalid` may change (column-by-column ROW compare). Services: `execution/evidence.py` (pure validators, `assert_verified_requires_evidence`), `claim_evidence.py` (`record_claim_evidence`, `get_claim_evidence`), `procedure_extraction/evidence.py` (episode-outcome bundles — a *different* concept from the `evidence` table).
- **Procedure** — `procedures` (mig 18–20, 44–47). Three orthogonal lifecycle axes: `verification_state candidate|verified|retired`, `staleness fresh|stale|revalidating`, `availability active|quarantined|disabled`, plus `approval_status`. `capture_procedure()` (`procedures.py:94`) is V0-gated (scope + provenance, extractor_version if derived), always born `candidate`. JSONB arrays: `preconditions` (claim-aware — element may carry `{claim_id}`), `expected_effects`, `postconditions`, `invariants`, `failure_conditions`, `exclusions`, `required_state`. `evidence_refs JSONB` + `source_episode_ids UUID[]`. Retrieval representation: `retrieval_document` + version/sha (mig 44).
- **Procedure extraction (trace)** — `procedure_extraction/` — `extract_procedure()` with a V5 pre-gate (`outcome != "success"` or no observations → refuse), strategy registry (`GroundedHybridExtractor` → `DeterministicExtractor` fallback), 6 validators V1–V6 (precondition groundedness, step purity, slot integrity, capability abstraction / no evidence-token leak, evidence sufficiency, invariant z3-satisfiability). `synthesize_procedure()` (L2 generalization) auto-discovered from `ingestion_jobs._maybe_auto_synthesize`.
- **Procedure versions** — `supersede_procedure`, `mark_procedure_stale` (bitemporal chain, family id + version). Claim version chain is analogous but stored in `properties` JSONB (no migration).
- **Procedure↔Claim linkage** — **no typed relation table.** Grep `procedure_claim` / `claim_ref` / `ProcedureClaimRef` in `backend/db` → zero hits. Claims are embedded in `procedures.preconditions` JSONB; `derive.py::precondition_with_claim` sets `claim_id` only when exactly one live claim carries the `{subject,predicate,object}` triple. Only `preconditions` is claim-aware — `expected_effects/postconditions/invariants/failure_conditions/required_state` are not.
- **Implementation** — `implementations` (mig 33): `kind` CHECK `deterministic|tool|slm|frontier|human|wasm|computer_use|api`; separate `status` and `verification_status` axes; `locator/invocation/input_schema/output_schema/requirements/auth_requirements` (credential *ref* only) JSONB; `derived_from` self-FK; `UNIQUE(name, provider, version)`.
- **Procedure↔Implementation linkage** — **two disconnected thin mechanisms:** `implementation_tasks` (mig 33 — implementation ↔ **task_node**, M:N) and `procedure_implementations` (mig 39 — procedure ↔ implementation, M:N, **only payload column is `resource_path`**). `solution_implementations.py` reads *only* via `procedures.migrated_from_task_node_id → implementation_tasks`, ignoring `procedure_implementations` entirely; richer resolution in `app/execution/implementation_registry.py`. No `role` / `applicability` / `evidence_refs` / version-constraint / interface-binding column on either link.
- **Source registry** — schema.md defines `Source [V]`, but **there is no `sources` table**. `evidence.source_id` / `claim.provenance` are text/uuid handles with no backing table. `ingestion_source_snapshots` (mig 39) is the closest real artifact — repo-revision snapshots for corpus jobs only.
- **Ingestion provenance** — `ingested_artifacts` (mig 32/39/40): `(source_type, uri, content_hash, extractor_version)` unique; `procedure_id`/`procedure_row_id` back-links; `first_seen/last_seen`; mig 39 adds `source_id/retrieved_at/license_metadata/bundle_hash/resource_manifest/parsed_metadata/dependencies/requirements`. `ingestion_runs` (mig 32): per-run manifest (`metrics`, `source_spec`, `created_by`).
- **Publication** — `publication.py::publish_procedure` + `publication_records` (mig 46): authority → scope → classification (`classify_procedure_row`, rejects `EXECUTION_SECRET|PERSONAL_DATA|CONFIDENTIAL_DATA|SECURITY_DATA`) → dependency traversal (`_traverse_dependencies` over `procedure_dependencies` — **procedure→procedure only**) → provenance/license → sanitization (`_scrub_value` + `_residual_secret_signals` regex backstop) → `capture_procedure(provenance='prior_library', visibility='public', scope='global')`, fresh row starts `candidate` (no evidence/verification forwarded). `withdraw_publication` consults `procedure_evidence_stats.independent_success_count`.
- **Provider egress policy** — `provider_policy.py::ProviderPolicyService.can_send()` + `model_provider_policies` (mig 46): per `(provider, model)` `allowed_data_classes` whitelist; seeded so external providers get only `PUBLIC_SOURCE|PUBLIC_DERIVED|GLOBAL_PROCEDURE`, `local` gets private classes. API-key existence ≠ authorization.
- **Classification** — `classification.py::DataClass` (10 values), `PRIVATE_CLASSES`/`PUBLIC_CLASSES`, deterministic `classify()` / `classify_procedure_row()`.
- **Data rights** — `data_rights.py` + `data_requests` (mig 46): export/deletion (DPDP/GDPR).
- **V0 gate** — `v0_gate.py`: `validate_scope` (10 scope types; non-global requires `entity_id`; "nothing is implicit-global"), `validate_provenance` (5 values; `derived` requires `extractor_version`; `derived + company_ingested` rejected). Called by `capture_procedure`; `capture_claim` calls it only when `scope_type` is supplied.
- **Access / tenancy** — `access.py`: `scope_predicates()` / `visibility_predicate()` / `tenant_transaction()` / RLS backstop (mig 29). Read-time only.
- **Injection screening (document path)** — `skill_ingestion.py::_screen_untrusted_document` + the `§29` guard block: `_META_DIRECTIVE_RE` / `_TRUST_ASSERTION_RE` over parsed doc text **before** any model call; a hit → `provenance='system_pending_review'`, no capability statement, model never run (fail closed). `_abstract_capability` fences untrusted text in `<untrusted_source>`, strict `CAPABILITY:`/`ABSTAIN` structured output, `_validate_capability_statement` (schema + trust-assertion + meta-directive + concrete-token echo + groundedness). Capability statement is metadata-only (verified downstream: read only by `semantic_projections.py` and `replay.py`).

### A.3 `.stealth/` projection

`local_retrieval.py` / `context_compiler.py` / `semantic_projections.py` exist. Not audited in depth this pass — G13/T11 (`context.md` bounded-router, `run.json`, `meta.json`, atomic regeneration, staleness via `meta.json`) is **OPEN pending its own audit**.

---

## B. Exact problems found (ingestion + knowledge lane)

| # | Problem | Evidence | Spec ref |
|---|---|---|---|
| **B1** | **Document ingestion bypasses Observation → Claim → Evidence.** `compile_skill_artifact` / `ingest_skill_md` call `capture_procedure()` directly from parsed document text. Zero `observations`, zero `knowledge_nodes` (claims), zero typed `evidence` rows are created. Procedure "evidence" is a JSONB `evidence_refs` blob append (`evidence_refs || provenance_entry`). | `skill_ingestion.py:781, 1570`; module docstring `:1-25` ("reuses `capture_procedure()` directly") | A0, A4–A8, §7, §8, §14, G4–G6, G11 |
| **B2** | **`_write_task_nodes` manufactures a `task_nodes` row per parsed step on every SKILL.md ingestion** (fresh + new-version paths). Contradicts mig 39's own header ("does not materialize generic source steps as task_nodes") and `procedures.py:1-7`. The trace path does **not** do this. | `skill_ingestion.py:1185-1224, 1505, 1601` | Rule 8, "NO REUSABLE TASK ONTOLOGY", A34, G11 |
| **B3** | **No `IngestionContext` object.** `ingestion_id / actor_id / workspace_id / scope / environment_id / classification / extractor_id+version / started_at+completed_at` are not bound anywhere. Pieces scattered: `ingestion_runs` (run manifest, no actor/workspace/scope), `ingested_artifacts` (per-artifact), lone `extractor_version` TEXT on 4 tables. No derived object can answer "under what scope / by whom / from exactly what input" in one traversal. | grep `ingestion_context` in `backend/db` → 0 hits | A1, §32, G1 |
| **B4** | **No typed `ProcedureClaimRef` relation with a role vocabulary** (`PRECONDITION / APPLICABILITY / ASSUMPTION / RATIONALE / DECISION / EXPECTED_EFFECT / FAILURE_MODE / VERIFICATION`). Claims live inside `procedures.preconditions` JSONB. | grep `procedure_claim`/`claim_ref` → 0 hits; `derive.py:35-89` | "PROCEDURE ↔ CLAIM", §18, G8 |
| **B5** | **Claim invalidation is a JSON containment scan, not role-aware.** `claim_impact.find_procedures_referencing_claim` = `SELECT ... FROM procedures WHERE preconditions @> [{"claim_id": $1}]`. Only `preconditions` is scanned; `expected_effects/postconditions/invariants/failure_conditions/required_state` carry no `claim_id` and are ignored. Every matching procedure is marked stale regardless of the claim's role. No `PRECONDITION/APPLICABILITY/ASSUMPTION` (force revalidation) vs explanatory-role (no invalidation) distinction. | `claim_impact.py:29-131`; wired from `claims.py:356-363` | "CLAIM INVALIDATION", §12, G8 |
| **B6** | **`procedure_implementations` is a thin link** — only `resource_path`. No `role (primary/supporting/partial/verification)`, `applicability`, `evidence_refs`, `supported_steps_or_capabilities`, `implementation_version_constraint`, `interface_binding`, `status`. Written only for `skill_package` scripts (`ON CONFLICT DO NOTHING`). Disconnected from `implementation_tasks` (mig 33). `solution_implementations.py` ignores it. | `39_structured_skill_ingestion.sql:59-67`; `skill_ingestion.py:1307-1312`; `solution_implementations.py:52-66` | B23, A16/A18, §21, G10 |
| **B7** | **`capture_claim` silently drops unanchored claims.** Requires ≥1 live `task_node` OR an episode; otherwise returns `None`, nothing written, no error. A document-derived claim with only Source/Artifact/Observation/Evidence provenance cannot be created. | `claims.py:183-188, 249-253, 268-269` | "CLAIM CREATION" ("Remove any existential requirement that a Claim must be anchored to a task_node"), A5, T4.1 |
| **B8** | **No claim-belief mechanism.** `knowledge_nodes.belief_score / belief_method / valid_from / valid_until` have **zero readers and zero writers** in `backend/app`. `capture_claim` never sets `belief_score` or `claim_status`. "Current belief" is the binary `properties.truth_state` (IN/OUT) plus a read-time `get_claim_lifecycle_state()` compute. Evidence rows for claims are written (`record_claim_evidence`) but nothing rolls them into a belief number. Observation-confidence vs claim-belief are separate only because *neither exists as a stored number*. | `claims.py:104-111`; `21_band1_contracts.sql:50-53`; `claim_evidence.py:145-164` | §10, §11, A5, T4.5 |
| **B9** | **Split / dead status representations.** `claim_status` column (written only by `procedure_extraction/failure_handlers.py`) vs `properties.truth_state` + read-time compute (used by `list_current_claims`). Two parallel notions of claim status. | `claims.py:678-742`; `failure_handlers.py:330-464` | §10 (single explicit status), G5 |
| **B10** | **`record_claim_evidence` cannot pass `independence_group`** — every claim-evidence row it writes is self-grouped → counts as independent. Repeated evidence from the same real source inflates independent-evidence counts for claims. | `claim_evidence.py:45-142` | §14 ("Never inflate evidence count by counting duplicates as independent"), G6 |
| **B11** | **Publication dependency traversal is procedure→procedure only.** `_traverse_dependencies` walks `procedure_dependencies`; it does not traverse procedure→claims→observations→sources→artifacts→evidence (those rows don't exist for ingested procedures — see B1). | `publication.py:97-122, 188` | A14, §34, G24 |
| **B12** | **No `Source` table.** schema.md's `Source [V]` (identity = normalized locator + publisher + type; reliability separate from claim confidence) is unbacked. `evidence.source_id` / `claim.provenance` dangle. | `24_evidence.sql:110-114` ("→ Source ... have no tables yet") | §2, A2, G2 |
| **B13** | **No structural source classification stage.** Document path decides "procedure vs not" only via `parse_skill_md` (has ordered steps → yes). No `PROCEDURE/REFERENCE/CLAIM/EXPERIMENT/TUTORIAL/ROUTER/OPINION/DISCUSSION/UNKNOWN` classifier; a reference/opinion doc with an incidental numbered list is still eligible to become a procedure. | `skill_ingestion.py:405-417` | §6, G4 |
| **B14** | **No security/policy screening record.** Injection screening exists but its outcome is a provenance downgrade, not a persisted `ALLOW/QUARANTINE/REJECT` decision with detector + version + reason. Secret/credential, PII, license, malicious-executable, source-trust checks are not run as an ingestion-time screening pass (they exist only at publication time). | `skill_ingestion.py:1382-1387` (no screening-decision row) | §5, G3 |
| **B15** | **Hardcoded placeholder goal in the trace→procedure sweep.** `goal_text = "Recurring engineering task observed in this episode"` for every episode-derived procedure; abstention is detected *after* the row is written and then retired. `outcome="success"` is asserted unconditionally into `AgentRunEvidenceSource`, held honest only by the enqueue-time gate. | `ingestion_jobs.py:707, 640-642, 739-751` | §25/§26 (no fabricated outcome), "NO SYNTHETIC FALLBACKS" |
| **B16** | **`ingested_artifacts` is not an immutable block-addressed Artifact.** No normalized addressable blocks (`Block{artifact_id, type, text, source_start, source_end, parent_block_id}`); claims/observations cannot be cited back to exact source offsets. | `32_ingestion_provenance.sql`; `skill_ingestion.py` parse layer keeps no offsets | §3, §4, A2, G2 |
| **B17** | **Frozen-schema discrepancy: Implementation is 1:1 in `schema.md`** (`Implementation.procedure_id: → Procedure`) but the spec + mig 39 want M:N via `ProcedureImplementation`. Per Hard Rule 3 this is a **board note**, not an edit. Recorded here. | `schema.md:208-220` vs spec B23 | Hard Rule 3, B23 |

---

## C. Exact files changed (Implementation Pass 1)

**New services:** `sources.py`, `ingestion_context.py`, `procedure_claim_refs.py`, `procedure_implementations.py`, `screening.py`, `artifact_blocks.py`, `runtime_guard.py`.
**Modified services:** `skill_ingestion.py` (canonical chain + B2), `claims.py` (`capture_claim` B7), `claim_impact.py` (B5 role-aware), `claim_evidence.py` (B10 `independence_group`), `solution_implementations.py` (read generalized relation), `config.py` (`environment` property), `main.py` (startup guard call).
**New tests:** `test_sources_offline.py`, `test_ingestion_context_offline.py`, `test_procedure_claim_refs_offline.py`, `test_procedure_implementations_offline.py`, `test_screening_offline.py`, `test_artifact_blocks_offline.py`, `test_runtime_guard_offline.py`, `test_claims_offline.py`, `test_solution_implementations_offline.py`.
**Modified tests:** `test_claim_evidence_offline.py` (+3), `test_claim_impact_offline.py` (rewritten), `test_skill_ingestion_offline.py` (task-node assertion inverted, chain asserted), `test_implementation_api_offline.py` (fixture extended), `test_claim_impact_e2e.py` (dict-return assertions — DB-gated, skips offline).
Full per-writer detail is in the **Implementation Pass 1** section above.

---

## D. Exact migrations added / modified (Implementation Pass 1)

**Added:** `db/50_sources.sql`, `db/51_ingestion_contexts.sql`, `db/52_procedure_claim_refs.sql`, `db/53_procedure_implementation_relation.sql`, `db/54_screening_decisions.sql`, `db/55_artifact_blocks.sql`. Migration series is now 56 files (upstream `49_ingestion_admission_audit.sql` + mine 50–55).
**Modified:** none (migrations are immutable once applied; `migrate.py` checksums them).
**Applied:** **none** — no Postgres in this environment. `scripts/migrate.py --status` NOT run. All six are additive + idempotent (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, guarded `ADD CONSTRAINT`), carry no in-file backfill (fresh-start rule), and each header states the next free number. **They are unverified against a real engine** — see "What Pass 1 does NOT do" above.
Column/relation detail is in the **Implementation Pass 1** table above.

---

## E. Exact API / schema changes (Implementation Pass 1)

- **DB schema:** 6 new tables (`sources`, `ingestion_contexts`, `procedure_claim_refs`, `screening_decisions`, `artifact_blocks`) + generalized `procedure_implementations` (12 new columns, unique-index swap). New `ingestion_context_id` column on `ingested_artifacts`, `observations`, `procedures`, `evidence`, `knowledge_nodes`; new `source_ref` on `ingested_artifacts`. New enum `source_kind`.
- **Service API (all additive, keyword-only, defaulted — no existing signature broke):** `capture_claim(..., source_ref=None, ingestion_context_id=None, observation_id=None)`; `record_claim_evidence(..., independence_group=None)`; `claim_impact.propagate_claim_change` return shape changed from `list[str]` to `{"marked_stale": [...], "explanatory_untouched": [...]}` (one in-tree caller + one e2e test updated; flat `find_procedures_referencing_claim` preserved for the out-of-lane `claim_graph_api` caller); `solution_implementations.get_solution_implementation_detail` rows gain `source`/`role`/`also_via` keys (task-node rows keep their existing key set).
- **No HTTP/MCP route added or changed.** `frontend/lib/api.ts` untouched.

---

## F. Compatibility paths retained

**All.** Nothing was deprecated or removed. The two ingestion paths, `capture_procedure`, `capture_claim`, `_write_task_nodes`, `procedure_implementations`, `implementation_tasks`, `ingested_artifacts`, and the publication pipeline are untouched.

---

## G. Production fallbacks removed

**None removed.** Fallbacks reviewed and classified (see the two sub-audits). Net finding: this subsystem is disciplined about *not* fabricating — functions return honest empties / `None` / raise typed errors. The reviewable items that remain (not defects, but the surface a hardening pass must decide on):

- `capture_claim` **silent drop** of unanchored claims (B7) — no exception, no telemetry beyond a debug log.
- `ingestion_jobs.py:707` hardcoded placeholder `goal_text` + post-hoc abstention retirement (B15).
- `ingestion_jobs.py:640-642` unconditional `outcome="success"` (B15) — honest only while the enqueue gate holds.
- `skill_ingestion.py:569` broad `except Exception: pass` — silently drops tool/dependency enrichment on a malformed package.
- `publication.py:327` broad `except Exception: independent = 0` — biases toward withdrawal but hides a stats-query error.
- `claim_evidence.py` no `independence_group` parameter → independence inflation (B10).

---

## H. Security / privacy behavior (current state)

| Control | State |
|---|---|
| Scope + provenance on every write | **Enforced** for procedures via `v0_gate`; **partial** for claims (`capture_claim` V0-checks scope only when supplied). |
| Injection in ingested documents | **Screened** (document path) → fail-closed provenance downgrade; model never sees flagged text; capability statement fenced + structured + validated. **No persisted screening decision.** |
| Private → global never implicit | **Enforced.** Only `publish_procedure` crosses the boundary; ordinary sync/retrieval cannot. Fresh global row starts `candidate`. |
| External-provider egress | **Gated** by `model_provider_policies.allowed_data_classes`; private classes → `local` provider only. |
| Publication sanitization | `_scrub_value` (secrets + absolute paths) over 7 fields + `_residual_secret_signals` regex backstop → any hit = denial. |
| Dependency traversal at publication | procedure→procedure only (B11); does not reach claims/observations/sources/artifacts/evidence. |
| Cross-workspace isolation | `access.py` `scope_predicates` / `visibility_predicate` + RLS backstop (mig 29). Not re-verified against a live DB this pass. |
| Data-subject rights | `data_requests` + `data_rights.py` (export/deletion). |
| Secret redaction on trace path | `trace_redaction.py::redact_event` chokepoint (per `api/ingest.py`). |

**Not covered:** ingestion-time secret/PII/license/malicious-executable screening with an auditable `ALLOW/QUARANTINE/REJECT` record (B14); SSRF screening on HTTP source/implementation locators; source-trust scoring.

---

## I. Exact tests run

```
cd backend && python -m pytest tests -q          # DATABASE_URL unset (offline)
```

That is the only runnable proving path in this environment. `*_e2e.py` and `test_schema_drift.py` self-skip (no DB). Migration tests (T2), ingestion golden E2E (T3), claim-graph DB tests (T4), procedure/implementation DB tests (T5/T6), MCP contract tests needing a server (T7), procedure-conditioned E2E (T8), recursive-execution recovery (T9), verification-ladder (T10), `.stealth/` budget (T11), multi-agent coordination (T12), security E2E (T13), load/latency (T14) — **none runnable here.**

---

## J. Exact pass / fail counts

**Backend offline suite** (`python -m pytest tests -q`, `DATABASE_URL` unset; external uncommitted `config.py` change held out of the Pass-2 number):
- baseline (pre-work, `main@1663c94`): `2556 passed / 360 skipped / 7 failed`
- after Implementation Pass 1 (rebased branch): `2736 passed / 360 skipped / 7 failed`
- after Implementation Pass 2 (A+B+C integrated): `2778 passed / 360 skipped / 7 failed` (283 s)
- after Pass 3 (`035adf6`) + G1 trace threading: `2784 passed / 361 skipped / 6 failed` (290 s)

**+228 passed across all passes, 0 new failures.** The failing set is pre-existing on `main` (`test_migration_upgrade_e2e` now correctly *skips* with DATABASE_URL cleanly unset, so 6 not 7). None is in this lane's target files:

| Test | Cause (pre-existing) |
|---|---|
| `tests/evaluation/security/test_injection_adversarial_offline.py::test_untrusted_skill_content_now_reaches_the_llm_prompt_inside_a_fence` | test-setup assertion ("injection payload must land in a fi…") |
| `tests/test_local_agent_runner_offline.py::test_offline_embedder_seam_never_reaches_a_real_provider` | offline seam reached a real provider method |
| `tests/test_local_agent_runner_offline.py::test_offline_runner_flow_never_reaches_a_real_provider` | same |
| `tests/test_mcp_six_tool_surface_offline.py::test_search_procedures_returns_real_matches` | `fake_find()` signature drift (`TypeError`) |
| `tests/test_mcp_six_tool_surface_offline.py::test_search_procedures_threads_invariant_bindings_through` | same |
| `tests/test_migration_upgrade_e2e.py::test_migration_upgrade_path_populated_v1_to_hardening` | `UndefinedColumnError: column "embedding_provider"` — needs DB; environment |
| `tests/test_voyage_embedding_retry.py::test_missing_gemini_key_still_falls_through_to_voyage_unaffected` | `Embedder` has no `_embed_via_chain` (method renamed; stale test) |

Harness suite and packaging suite: **not run** this pass.

---

## K. Remaining PARTIAL / OPEN items — gate matrix

Release-critical unless noted. `CLOSED` = spec requirement met **and** DB/E2E-verified. `CODE-COMPLETE (DB-VERIFIED)` = migration applied + writer wired + offline proving tests + a T2/T3 DB assertion green, but coverage of adjacent concerns (e.g. more detector classes, trace-path parity) still owed. `PARTIAL` = substantial mechanism exists with a named gap. `OPEN` = not built / not started. Migrations 50–55 are **applied** (hosted + local, 0 pending, 0 mismatch); **T2** (`test_migrations_50_55_t2_e2e.py`) and **T3** (`test_ingestion_canonical_chain_e2e.py`) pass against local.

### Plan A gates (state after Implementation Pass 2)

| Gate | Item | State | Note |
|---|---|---|---|
| **G0** | Baseline SHA + schema/API contract + config inventory | **CLOSED** | This document. `main@1663c94`; offline baseline 2556/360/7; post-Pass-2 see §J. |
| **G1** | `IngestionContext` / provenance manifest | **CODE-COMPLETE (DB-VERIFIED)** | mig 51 `ingestion_contexts` + back-links on 6 tables. **Both paths wired:** `compile_skill_artifact` (document, T3-verified end-to-end — every derived row stamped) and `ingestion_jobs.resolve_trace_ingestion_context` (trace, offline-tested — one context per session, stamps observation→claim→procedure→procedure-evidence). Remaining: trace-path DB E2E; the trace context is left `open` (spans many async jobs — documented). |
| **G2** | Source + Artifact normalization + immutable block addressing | **CODE-COMPLETE (DB-VERIFIED)** | mig 50 `sources` + mig 55 `artifact_blocks`; `normalize_markdown` invoked by `compile_skill_artifact`. **T2** (offset + identity CHECKs) + **T3** (blocks in the live chain, `content[start:end]` round-trips) pass. Block-span citation + block-text secret redaction landed (`035adf6`). Remaining: a `Source [V]` for the trace path. |
| **G3** | Security / policy screening (`ALLOW/QUARANTINE/REJECT` + detector provenance) | **CODE-COMPLETE (DB-VERIFIED)** for the record; **PARTIAL** for coverage | mig 54 `screening_decisions` (applied) + `screening.py`; **now invoked** by `compile_skill_artifact` (one row per finding, alongside the existing downgrade). Remaining: a screen `REJECT` does not yet abort capture (policy deferred); PII/license/malware detectors; SSRF check on locators; block-level redaction; T13. |
| **G4** | Observation extraction / validation (+ structural source classification §6) | **PARTIAL** (advanced) | Document path emits one `document_procedure` Observation with `ingestion_context_id`. Still missing: Observations per `artifact_block` / block-span citation; the `PROCEDURE/REFERENCE/CLAIM/…` source classifier (B13). |
| **G5** | Independent Claim normalization, belief, dedup, conflict/family | **PARTIAL** (advanced) | **B7/B10 done**. **B8 done** — `claim_belief.py`: evidence-only belief with independence de-dup + document ceiling 0.5, written via a ChangeSet citing evidence; hooked on `record_claim_evidence` + `relate_claims`. **B9 done** for evidence/relation paths (`status_from_belief`) — but `failure_handlers.py` still writes `claim_status` directly. **Document path now derives one Claim** (`bb2deba`). Still missing: structured `subject/predicate/object` columns unpopulated by `capture_claim`; T4. |
| **G6** | Evidence normalization + independence accounting | **PARTIAL** (advanced) | **B10 done**. Document path writes a typed `evidence(type='document')` row (`independence_group='skill_md:'+hash`) for the procedure; belief engine consumes claim-targeted evidence with independence de-dup. Still: no per-Claim document Evidence row yet. |
| **G7** | Procedure extraction / validation / versioning | **CLOSED** *(trace path, offline)* / **PARTIAL** *(overall)* | **B15 done** (`035adf6`): source-supplied `goal_text` required, no fabricated goal/outcome. Document path still has no groundedness validator. |
| **G8** | Procedure↔Claim typed refs + applicability integration | **CODE-COMPLETE (DB-VERIFIED)** | mig 52 `procedure_claim_refs` + service + **B5 role-aware invalidation** (`claim_impact.py`); legacy scan kept as compat. Document path authors a `RATIONALE` ref (T3-verified in the live chain); T2 verifies the role vocab + identity UNIQUE. Remaining: run `backfill_refs_from_preconditions` (A33); wire `add_procedure_claim_ref` into the *trace* extractors. |
| **G9** | Procedure composition validation (cycle rejection, version-pinned child refs, runtime≠canonical) | **CODE-COMPLETE (DB-VERIFIED)** | `procedure_graph.validate_procedure_composition_{definition,in_storage}` (`035adf6`) — write-time gate rejecting cycles / unresolved pinned refs / max-depth; wired into `capture_procedure` + `supersede_procedure`. `test_procedure_composition_e2e.py` (DB) + `test_procedure_graph_offline.py`. |
| **G10** | Implementation Registry validation + Procedure↔Implementation M:N relation metadata | **CODE-COMPLETE (DB-VERIFIED)** | mig 53 generalizes `procedure_implementations` (T2: the new columns are usable + role vocab enforced against the live DB); `procedure_implementations.py`; `solution_implementations.py` reads it unioned with the legacy path. Remaining: converge `implementation_tasks`; board note for the `schema.md` 1:1↔M:N discrepancy (B17); a real 3-adapter T6. |
| **G11** | Static/global ingestion refactor + corpus migration/backfill | **PARTIAL** (advanced) | **B1 done**: document path runs Source→IngestionContext→Observation→Evidence(document)→Claim→Procedure, plus `artifact_blocks` + `screening_decisions`; admission gate unioned. **B2 done**: no task_nodes at ingestion. Still: **corpus backfill un-run**; trace-path Source table; T3 golden E2E. |
| **G12** | Local schema-aligned learning + scope / private sync | **OPEN** | Not touched this pass. |
| **G13** | `.stealth/` projection service | **OPEN** | Not touched this pass. |
| **G14** | Global hierarchical retrieval + index freshness | **OPEN** | Not touched this pass. |
| **G23** | `report_execution` → Observation / Evidence / Claim-candidate learning | **PARTIAL** | Unchanged (Plan B lane). |
| **G24** | Publication / privacy / license / IP dependency traversal | **CODE-COMPLETE (offline-only)** | **B11 done** (`acbd2f5`): `publication_deps.traverse_publication_dependencies` walks procedure→claims→observations→sources→artifacts→evidence, fail-closed (private/org, `PRIVATE_CLASSES`, unresolved/low-reliability source, or traversal-bound hit → blocking); "private evidence ≠ global verification" enforced (`verification_inherited` always false; `global_verification_required` unless ≥2 independent public verification groups). Wired into `publish_procedure`; `publication_records` carries the full traversal + verification determination. Remaining: independent *global* re-verification is recorded-as-required but not executed; license/IP checks are visibility/classification-based only; DB E2E. |

### Testing categories (state after Implementation Pass 1)

| T | State |
|---|---|
| T1 (TEST/STAGING/PROD separation; staging/prod fail-startup on a fake provider; proving test that prod cannot activate a test adapter) | **CODE-COMPLETE (DB-UNVERIFIED)** — `config.py` `environment` tri-state (fail-closed) + `runtime_guard.assert_production_safe` called at `main.py` startup + `test_runtime_guard_offline.py` (19, incl. the proving test). Was OPEN. Remaining: exercise against a real STAGING/PRODUCTION boot. |
| T2 (per-migration fresh + representative-row + idempotency + rollback) | **DONE** — `test_migrations_50_55_t2_e2e.py` (8 passed, local): additive-only, idempotent re-run of each file, representative row + named-CHECK rejection per table. `test_schema_drift.py` → 2 passed. Rollback intentionally not automated (fresh-start rule 1). |
| T3 (golden ingestion E2E) | **DONE** — `test_ingestion_canonical_chain_e2e.py` (2 passed, local): full 12-row document chain + no-Procedure for a non-procedural doc. |
| T4–T10, T12–T14 | **OPEN** — T4/T6 partially covered by existing `*_e2e.py`; the rest need server / load rig / more adapters. |
| T11 (`.stealth/` budget) | **OPEN** — depends on G13. |
| T15 (this matrix) | **CLOSED** — delivered + updated here. |

### The 20 required ingestion tests (spec "TESTS" list)

All **OPEN** as production-path DB tests. Offline analogues exist for a few (non-procedural-prose rejection is covered structurally by `parse_skill_md` unit tests; injection fencing has an offline test — currently failing on setup). None of the 20 can be asserted end-to-end here.

---

## L. Final architecture after changes

Unchanged from Section A — **no changes were made.** The target architecture (spec Part VI) requires, minimally, in dependency order:

1. **`sources` table** + Source identity/reliability (G2 / B12).
2. **`ingestion_contexts` table** + threading `ingestion_id` onto every derived-object writer (G1 / B3).
3. **Artifact block addressing** — normalized `artifact_blocks` with source offsets (G2 / B16).
4. **Ingestion-time screening record** — `screening_decisions` (`ALLOW/QUARANTINE/REJECT` + detector@version + reason) (G3 / B14).
5. **Source classifier** — `PROCEDURE/REFERENCE/CLAIM/…` gate before procedure eligibility (G4 / B13).
6. **Document → Observation → Claim → Evidence(document)** path — replace the direct `capture_procedure()` call in `skill_ingestion.py` with the canonical chain; keep the parser, injection screen, capability abstraction, novelty logic (G4–G6, G11 / B1).
7. **Relax `capture_claim` anchoring** — accept Source/Artifact/Observation/Evidence provenance; keep task/episode refs as optional context; still reject truly unanchored opaque claims (G5 / B7).
8. **Claim belief mechanism** — populate `belief_score`/`belief_method` from `evidence` aggregation with independence de-dup and cite-the-evidence updates; keep it separate from `observation` confidence; converge the split status representation (G5 / B8, B9).
9. **`independence_group` on `record_claim_evidence`** (G6 / B10).
10. **`procedure_claim_refs` table** — `(procedure_id, procedure_version, claim_id, claim_version, role ∈ {PRECONDITION,APPLICABILITY,ASSUMPTION,RATIONALE,DECISION,EXPECTED_EFFECT,FAILURE_MODE,VERIFICATION})` + backfill from `preconditions[*].claim_id` + compatibility reads (G8 / B4).
11. **Role-aware invalidation** — `claim_impact` keyed on `procedure_claim_refs.role`; strong roles force stale/revalidate, explanatory roles do not (G8 / B5).
12. **Generalize `procedure_implementations`** — add `role/applicability/evidence_refs/supported_capabilities/impl_version_constraint/interface_binding/status`; converge with `implementation_tasks`; make `solution_implementations.py` read it (G10 / B6). Board note for the `schema.md` 1:1↔M:N discrepancy (B17).
13. **Stop `_write_task_nodes` at ingestion** — remove task-node materialization from the document path; task_nodes stay execution-time only (G11 / B2).
14. **Publication full dependency traversal** — extend `_traverse_dependencies` to claims→observations→sources→artifacts→evidence once (6) exists; add an independent-global-verification gate (G24 / B11).
15. **Remove placeholder goal / unconditional success** from `ingestion_jobs.py` once the episode evidence carries a real goal/outcome (B15).

Each step is one gated change: migration (additive + idempotent) + writer rewiring + compatibility reads + fresh-DB / representative-row / idempotency tests + the golden ingestion assertions for that stage. The offline proving tests (~210 across both passes) are green; the DB-backed proving tests (T2/T3) are the remaining gate — a local Postgres is now wired via `dbtarget` (see below).

---

**Steps 1–13 landed across Pass 1 + Pass 2** (migrations 50–55 applied to hosted + local; 9 new services; wired into `compile_skill_artifact` incl. `artifact_blocks` + `screening` + document-Claim; `claim_belief.py`; `publication_deps.py`). Steps **15 (placeholder goal in `ingestion_jobs.py`)** untouched.

## Handoff — what remains

1. **DB proving tests (T2/T3).** A local Postgres is wired (`dbtarget local`). Owed: `test_schema_drift.py` + `test_migration_upgrade_e2e.py` green against it; per-migration T2 tests for 50–55 (fresh-DB apply / representative-row / idempotent re-run / rollback-or-documented-irreversibility); a golden ingestion E2E (T3) — real SKILL.md → assert `sources`/`ingestion_contexts`/`artifact_blocks`/`observations`/`evidence(document)`/`procedure_claim_refs`/`procedure` all present with correct provenance and **no** `task_nodes`; a non-procedural doc → Source + blocks + Observation, **no** Procedure.
2. **Run `procedure_claim_refs.backfill_refs_from_preconditions`** against the real corpus (A33); record before/after counts. (Local DB is schema-only — run against a corpus-loaded DB.)
3. **B15** — `ingestion_jobs.py` hardcoded placeholder goal + unconditional `outcome="success"` in the trace→procedure sweep.
4. **B9 residual** — `procedure_extraction/failure_handlers.py` still writes `claim_status` directly; route it through `claim_belief.status_from_belief` for full convergence.
5. **B1 residual** — block-level secret redaction (`artifact_blocks.text` is from raw content); cite the document Observation to a specific block span; a screen `REJECT` currently only warns, does not abort capture (policy call).
6. **G9 / G12 / G13 / G14** — procedure-composition cycle rejection; local/private sync (A11–A13); `.stealth/` projection contract; retrieval index-lag tracking. None audited in depth or touched.
7. **Board note** — `schema.md` models `Implementation → Procedure` 1:1; Pass 1 + spec B23 use M:N via `procedure_implementations`. `schema.md` is frozen → board note, not an edit.
8. **Not this lane** — Plan B (MCP execution lifecycle, `find_best_way` routing, recursive child runs, `RouteDecision`, `StealthExecutionContext`, ExecutionRecorder) — G15–G22, G25–G26, B1–B37.
9. **External** — `backend/app/config.py` has an uncommitted `embedding_provider_chain: "gemini,voyage" → "gemini"` change from another lane; left untouched here.
7. **Step 8 (claim belief):** populate `belief_score`/`belief_method` from `evidence` aggregation with independence de-dup; keep separate from observation confidence; converge the `claim_status` vs `truth_state` split (B9).
8. **Step 14:** extend `publication._traverse_dependencies` to walk procedure→`procedure_claim_refs`→claims→observations→sources→artifacts→evidence; add an independent-global-verification gate (B11).
9. **Board note:** B17 — `schema.md` models `Implementation.procedure_id → Procedure` (1:1); Pass 1 + spec B23 use M:N via `procedure_implementations`. `schema.md` is frozen → this is a board note, not an edit.
- **Not this lane:** Plan B (MCP execution lifecycle, `find_best_way` routing, recursive child runs, `RouteDecision`, `StealthExecutionContext`, ExecutionRecorder) — G15–G22, G25–G26, B1–B37.
