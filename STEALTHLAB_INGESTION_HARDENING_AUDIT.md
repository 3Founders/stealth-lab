# StealthLab — Ingestion + Knowledge Hardening Audit (Plan A / Gates G0–G14, G23–G24)

**Lane:** INGESTION + KNOWLEDGE architecture hardening.
**Spec audited against:** `STEALTHLAB_EXTREME_FINAL_HARDENING_V4.md` (Part II Plan A, Part II-A §1–§38, Part IV G0–G14/G23/G24, Part VI, Testing T1–T15).
**Repo revision at audit:** `main` @ `1663c94` for the original audit; Implementation Pass 1 rebased onto `main` @ `301b9bf` (incorporates upstream `976b647` "Global Internet Ingestion admission gate", which took `db/49`, so Pass 1's migrations are `db/50`–`db/55`).
**Environment constraint:** the audit was written with `DATABASE_URL` unset (offline suite only). A **hosted Supabase Postgres is now reachable** via `backend/.env` (`DATABASE_URL`), but its egress budget is nearly exhausted, so DB use is deliberately minimal: `migrate.py --status` was run (migrations 01–49 applied, **50–55 pending**, all checksums match); nothing else. Migrations 50–55 were **NOT applied** (blocked pending explicit approval — see Handoff). The full DB/E2E/backfill test surface (T2–T14) remains un-run. Baseline offline suite: **2556 passed / 360 skipped / 7 failed**.

**Verdict:** `EXTREME FINAL HARDENING INCOMPLETE` — but **Implementation Pass 1 has landed** (see the next section). The program is 29 gates (G0–G28) + 34 A-phases + 15 test categories; Pass 1 closes or advances 8 of the ingestion+knowledge gaps identified below, all as additive migrations + writer rewiring + offline proving tests. **No gate is fully CLOSED** because "CLOSED" per the spec requires the migration *applied* and DB/E2E tests green — migrations 50–55 are written and offline-verified but not yet applied. The per-item state below says exactly what remains.

---

## IMPLEMENTATION PASS 1 — what landed (2026-09-10)

**Merged-tree offline suite: `2688 passed / 360 skipped / 7 failed`** (`python -m pytest tests -q`, `DATABASE_URL` unset, 305 s). The 7 failures are byte-identical to the documented baseline-7 (embedder `_embed_via_chain` rename ×3, `test_mcp_six_tool_surface` `fake_find()` signature ×2, `test_injection_adversarial` test-setup bug ×1, `test_migration_upgrade_e2e` needs a DB ×1). **Zero regressions. +132 passing tests** from ~170 new offline tests.

### New migrations (additive + idempotent, `IF NOT EXISTS` throughout, NO in-migration backfill — un-run: no Postgres)

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

### What Pass 1 does NOT do (still OPEN / next)

- **Migrations 50–55 are written and offline-verified but NOT APPLIED.** `migrate.py --status` against the hosted DB confirms 01–49 applied and **50–55 pending, checksums clean** — but the apply step is gated (needs explicit approval; egress budget is tight). No fresh-DB / representative-row / rollback tests (T2), no schema-drift check. This is the single largest remaining verification gap.
- **The historical corpus backfill (A33) has not run.** `backfill_refs_from_preconditions` is written and offline-tested but is an explicit callable, not wired anywhere.
- The document path emits an Observation + Evidence(document) for the **procedure**, but does **not** yet derive **Claims** from the document (B1 is partially closed — the chain exists, Claim derivation from `artifact_blocks` is the next step).
- `artifact_blocks` normalization is **not yet invoked** by `compile_skill_artifact` (the table + normalizer exist; wiring them in, and citing the emitted Observation back to a block span, is the next step).
- `screening.py` is **not yet invoked** by `compile_skill_artifact` (the existing inline injection screen still runs; routing it through `record_screening_run` for the audit trail is the next step).
- Claim **belief** aggregation (B8) — not started. `belief_score`/`belief_method` still dead columns.
- Publication dependency traversal (B11) still procedure→procedure only.
- No end-to-end golden ingestion test (T3) — impossible without a DB.

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

**Backend offline suite — baseline (pre-work):** `2556 passed, 360 skipped, 7 failed` (315 s).
**Backend offline suite — after Implementation Pass 1 (merged tree):** `2688 passed, 360 skipped, 7 failed` (305 s). **+132 passed, 0 new failures, skipped unchanged.**

The 7 failures are byte-identical before and after — pre-existing on `main`, none in this lane's target files:

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

Release-critical unless noted. `CLOSED` = spec requirement met **and** DB/E2E-verified. `CODE-COMPLETE (DB-UNVERIFIED)` = the migration + writer + offline proving tests have landed in Implementation Pass 1, but the migration is unapplied and no DB/E2E test has run (this environment has no Postgres). `PARTIAL` = substantial mechanism exists with a named gap. `OPEN` = not built / not started.

### Plan A gates (state after Implementation Pass 1)

| Gate | Item | State | Note |
|---|---|---|---|
| **G0** | Baseline SHA + schema/API contract + config inventory | **CLOSED** | This document. `main@1663c94`; offline baseline 2556/360/7; post-Pass-1 2688/360/7. |
| **G1** | `IngestionContext` / provenance manifest | **CODE-COMPLETE (DB-UNVERIFIED)** | mig 51 `ingestion_contexts` + `ingestion_context.py` + back-link columns on 5 tables; `compile_skill_artifact` opens/completes one and stamps it on the procedure/observation/evidence/artifact. Was OPEN. Remaining: apply mig 51; thread it through the trace path too; T2. |
| **G2** | Source + Artifact normalization + immutable block addressing | **CODE-COMPLETE (DB-UNVERIFIED)** | mig 50 `sources` + `sources.py` (identity dedup); mig 55 `artifact_blocks` + offset-preserving `normalize_markdown`. Was PARTIAL. Remaining: apply migs; **invoke `normalize_markdown` from `compile_skill_artifact`** and cite the Observation back to a block span; a real `Source [V]` table for the trace path; T2/T3. |
| **G3** | Security / policy screening (`ALLOW/QUARANTINE/REJECT` + detector provenance) | **CODE-COMPLETE (DB-UNVERIFIED)** for the record; **PARTIAL** for coverage | mig 54 `screening_decisions` + `screening.py` (reuses existing injection + secret detectors; `decide()`; auditable, never-deleted rows). Was PARTIAL. Remaining: **route `compile_skill_artifact`'s inline screen through `record_screening_run`**; add PII/license/malware detectors; SSRF check on locators; T13. |
| **G4** | Observation extraction / validation (+ structural source classification §6) | **PARTIAL** (advanced) | Document path **now emits one `document_procedure` Observation** with `ingestion_context_id` (was: none). Still missing: Observations per `artifact_block`; the `PROCEDURE/REFERENCE/CLAIM/…` source classifier (B13). |
| **G5** | Independent Claim normalization, belief, dedup, conflict/family | **PARTIAL** (advanced) | **B7 done**: `capture_claim` accepts document/observation provenance without a task/episode anchor. **B10 done**: `record_claim_evidence` takes `independence_group`. Still missing: **belief aggregation** (B8 — `belief_score`/`belief_method` still dead), split status convergence (B9), structured `subject/predicate/object` columns still unpopulated, and the document path does not yet derive Claims from `artifact_blocks`. |
| **G6** | Evidence normalization + independence accounting | **PARTIAL** (advanced) | **B10 done** (`independence_group` reachable). Document path **now writes a typed `evidence(type='document')` row** for the procedure with `independence_group='skill_md:'+hash` (was: JSONB `evidence_refs` blob only). Still: no per-Claim document Evidence yet. |
| **G7** | Procedure extraction / validation / versioning | **CLOSED** *(trace path, offline)* / **PARTIAL** *(overall)* | Unchanged this pass. Document path still has no groundedness validator; `ingestion_jobs.py` placeholder goal (B15) untouched. |
| **G8** | Procedure↔Claim typed refs + applicability integration | **CODE-COMPLETE (DB-UNVERIFIED)** | mig 52 `procedure_claim_refs` (8-role vocab) + `procedure_claim_refs.py` + **B5 role-aware invalidation** in `claim_impact.py` (strong roles → stale; explanatory → untouched); legacy `preconditions @> …` scan kept as compat, UNIONed. `backfill_refs_from_preconditions` written + offline-tested. Was OPEN. Remaining: apply mig 52; **run the backfill** (A33); wire `add_procedure_claim_ref` into the extractors so new procedures author typed refs; T4. |
| **G9** | Procedure composition validation | **PARTIAL** | Unchanged this pass. |
| **G10** | Implementation Registry validation + Procedure↔Implementation M:N relation metadata | **CODE-COMPLETE (DB-UNVERIFIED)** | mig 53 generalizes `procedure_implementations` (role/applicability/evidence_refs/version-constraint/interface_binding/status); `procedure_implementations.py` service; `solution_implementations.py` reads it, unioned with the legacy path. Was PARTIAL. Remaining: apply mig 53; converge `implementation_tasks`; board note for the `schema.md` 1:1↔M:N discrepancy (B17); T6. |
| **G11** | Static/global ingestion refactor + corpus migration/backfill | **PARTIAL** (advanced) | **B1 partially closed**: document path converged onto Source→IngestionContext→Observation→Evidence(document)→Procedure. **B2 done**: `_write_task_nodes` no longer called at ingestion. Still: Claims not derived from the document; `artifact_blocks`/`screening` not yet invoked; **corpus backfill un-run** (no DB). |
| **G12** | Local schema-aligned learning + scope / private sync | **OPEN** | Not touched this pass. |
| **G13** | `.stealth/` projection service | **OPEN** | Not touched this pass. |
| **G14** | Global hierarchical retrieval + index freshness | **OPEN** | Not touched this pass. |
| **G23** | `report_execution` → Observation / Evidence / Claim-candidate learning | **PARTIAL** | Unchanged (Plan B lane). |
| **G24** | Publication / privacy / license / IP dependency traversal | **PARTIAL** | Unchanged this pass. B11 (traverse to claims/observations/sources/artifacts/evidence) still open — but the objects it needs to traverse now exist for documents (migs 50–55). |

### Testing categories (state after Implementation Pass 1)

| T | State |
|---|---|
| T1 (TEST/STAGING/PROD separation; staging/prod fail-startup on a fake provider; proving test that prod cannot activate a test adapter) | **CODE-COMPLETE (DB-UNVERIFIED)** — `config.py` `environment` tri-state (fail-closed) + `runtime_guard.assert_production_safe` called at `main.py` startup + `test_runtime_guard_offline.py` (19, incl. the proving test). Was OPEN. Remaining: exercise against a real STAGING/PRODUCTION boot. |
| T2 (per-migration fresh + representative-row + idempotency + rollback) | **OPEN** — no DB. Migrations 50–55 unapplied; `test_migration_upgrade_e2e.py` still failing here for lack of a DB. |
| T3–T10, T12–T14 | **OPEN** — require DB / server / load rig. |
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

Each step is one gated change: migration (additive + idempotent) + writer rewiring + compatibility reads + fresh-DB / representative-row / idempotency tests + the golden ingestion assertions for that stage. None may land without its proving tests green against a real Postgres — which this environment does not provide.

---

**Steps 1–7, 9–13 landed in Implementation Pass 1** (as migrations 50–55 + 7 new services + 6 rewired writers + ~170 offline tests). Steps **8 (claim belief), 14 (publication traversal), 15 (placeholder goal)** are untouched.

## Handoff — what the next session must do

1. **Get a Postgres 15 + pgvector instance** (`pgvector/pgvector:pg15`). Nothing below is real until this exists.
2. **Apply migrations 50–55** (`python scripts/migrate.py`), then `--status` to confirm 56/56, 0 checksum mismatches. Run `test_schema_drift.py` and `test_migration_upgrade_e2e.py` against the fresh DB.
3. **Write T2 tests** for each of 50–55: fresh-DB apply, representative-row apply, idempotent re-run, (rollback or documented irreversibility).
4. **Run `procedure_claim_refs.backfill_refs_from_preconditions`** against the real corpus (A33); record before/after counts.
5. **Wire the three not-yet-invoked pieces into `compile_skill_artifact`:** `artifact_blocks.normalize_markdown` + `persist_artifact_blocks` (and cite the Observation to a block span); `screening.record_screening_run` (route the existing inline injection screen through it); Claim derivation from the blocks (`capture_claim(source_ref=…, ingestion_context_id=…, observation_id=…)`).
6. **Write the golden ingestion E2E (T3)** now that the object chain exists: real SKILL.md → assert `sources`/`ingestion_contexts`/`artifact_blocks`/`observations`/`evidence(document)`/`procedure` rows all present with correct provenance, and NO `task_nodes`; real non-procedural doc → assert Source + blocks + Observation, NO Procedure.
7. **Step 8 (claim belief):** populate `belief_score`/`belief_method` from `evidence` aggregation with independence de-dup; keep separate from observation confidence; converge the `claim_status` vs `truth_state` split (B9).
8. **Step 14:** extend `publication._traverse_dependencies` to walk procedure→`procedure_claim_refs`→claims→observations→sources→artifacts→evidence; add an independent-global-verification gate (B11).
9. **Board note:** B17 — `schema.md` models `Implementation.procedure_id → Procedure` (1:1); Pass 1 + spec B23 use M:N via `procedure_implementations`. `schema.md` is frozen → this is a board note, not an edit.
- **Not this lane:** Plan B (MCP execution lifecycle, `find_best_way` routing, recursive child runs, `RouteDecision`, `StealthExecutionContext`, ExecutionRecorder) — G15–G22, G25–G26, B1–B37.
