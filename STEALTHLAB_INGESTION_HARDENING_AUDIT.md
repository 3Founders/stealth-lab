# StealthLab — Ingestion + Knowledge Hardening Audit (Plan A / Gates G0–G14, G23–G24)

**Lane:** INGESTION + KNOWLEDGE architecture hardening.
**Spec audited against:** `STEALTHLAB_EXTREME_FINAL_HARDENING_V4.md` (Part II Plan A, Part II-A §1–§38, Part IV G0–G14/G23/G24, Part VI, Testing T1–T15).
**Repo revision at audit:** `main` @ `1663c94` for the original audit; Implementation Pass 1 rebased onto `main` @ `301b9bf` (incorporates upstream `976b647` "Global Internet Ingestion admission gate", which took `db/49`, so Pass 1's migrations are `db/50`–`db/55`).
**Environment / DB:** two Postgres targets, switched via `backend/scripts/dbtarget.{py,ps1,sh}` (`DATABASE_URL_LOCAL` ⟷ hosted `DATABASE_URL`; see `backend/DB_TARGETS.md`).
- **hosted** (Supabase, egress-limited): this lane's tables applied during Pass 1 under the **pre-renumber** names `50–55`; the `schema_migrations` ledger there still carries those 6 rows. Plan B's `50–63` + this lane's renamed `64–69` reconciliation on hosted is a **deliberate operator action** (see the re-audit section's ledger-reconciliation block) — not run from here, per the egress budget.
- **local** (native Postgres): schema built migration-by-migration; this lane's 6 tables are present and correct (applied under old names `50–55`; `procedure_implementations` carries all 6 new columns; `ingestion_context_id` back-link on all target tables). Ledger has 6 orphan rows `50_sources.sql … 55_artifact_blocks.sql`; Plan B's `50–63` are `pending`. Reconciliation SQL in the re-audit section. (Pre-existing MISMATCH warnings on migrations 11–34 are cosmetic: ledger checksum predates `migrate.py`'s CRLF→LF normalization — schema is correct.) **T2** (`test_migrations_64_69_t2_e2e.py`, 8 passed) and **T3** (`test_ingestion_canonical_chain_e2e.py`, 2 passed) verified against local; `test_schema_drift.py` → 2 passed (no drift).

Offline-suite progression (`DATABASE_URL` unset): baseline `2556 / 360 / 7` → Pass 1 `2736 / 360 / 7` → Pass 2 `2778 / 360 / 7` → Pass 3 (`035adf6`) + G1 trace `2784 / 361 / 6` (the extra skip is `test_migration_upgrade_e2e` correctly skipping with DATABASE_URL cleanly unset; +228 passing across all passes, **zero regressions**).

**Verdict:** `EXTREME FINAL HARDENING INCOMPLETE` — but **Implementation Pass 1 has landed** (see the next section). The program is 29 gates (G0–G28) + 34 A-phases + 15 test categories; Pass 1 closes or advances 8 of the ingestion+knowledge gaps identified below, as additive migrations (now applied) + writer rewiring + offline proving tests. **No gate is fully CLOSED** because "CLOSED" per the spec requires the *whole* DB/E2E surface (T2–T14) green — T2 and T3 now pass against local; T4/T6/T8–T14 and a corpus-loaded backfill remain. The per-item state below says exactly what remains.

---

## IMPLEMENTATION PASS 1 — what landed (2026-09-10)

**Merged-tree offline suite: `2688 passed / 360 skipped / 7 failed`** (`python -m pytest tests -q`, `DATABASE_URL` unset, 305 s). The 7 failures are byte-identical to the documented baseline-7 (embedder `_embed_via_chain` rename ×3, `test_mcp_six_tool_surface` `fake_find()` signature ×2, `test_injection_adversarial` test-setup bug ×1, `test_migration_upgrade_e2e` needs a DB ×1). **Zero regressions. +132 passing tests** from ~170 new offline tests.

### New migrations (additive + idempotent, `IF NOT EXISTS` throughout, NO in-migration backfill — APPLIED to the hosted DB; T2 tests still owed)

| File | Adds | Gate |
|---|---|---|
| `db/64_sources.sql` | `sources` origin registry (`source_kind` enum, identity `UNIQUE (source_type, locator, publisher)`, `reliability_score`/`_method` kept separate from claim belief); `ingested_artifacts.source_ref` | G2 / B12 |
| `db/65_ingestion_contexts.sql` | `ingestion_contexts` (the §A1 field list — actor/workspace/scope/classification/extractor identity); `ingestion_context_id` back-link column on `ingested_artifacts`, `observations`, `procedures`, `evidence`, `knowledge_nodes` | G1 / B3 |
| `db/66_procedure_claim_refs.sql` | `procedure_claim_refs` typed relation (8-role vocab, `UNIQUE (procedure_id, procedure_version, claim_id, role)`, partial strong-role index) | G8 / B4+B5 |
| `db/67_procedure_implementation_relation.sql` | generalizes `procedure_implementations` — `role`/`implementation_version[_constraint]`/`supported_steps`/`supported_capabilities`/`applicability`/`interface_binding`/`evidence_refs`/`status`/bitemporal; drops the old 2-col UNIQUE for a partial `(procedure_id, implementation_id, role)` identity index | G10 / B6 |
| `db/68_screening_decisions.sql` | `screening_decisions` (`ALLOW`/`QUARANTINE`/`REJECT` + `check_type` + `detector`@`detector_version` + `signals` + `reason`, auditable, not-deleted) | G3 / B14 |
| `db/69_artifact_blocks.sql` | `artifact_blocks` — immutable normalized blocks with char `source_start`/`source_end` offsets into `artifact_content_hash`, heading nesting, stable `h{n}-{slug}` anchors | G2 / B16 |

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

- **`services/skill_ingestion.py`** — `compile_skill_artifact` now runs the canonical chain on the `captured`/`new_version` outcomes: `register_source(type=document)` → `open_ingestion_context` → `capture_procedure` → stamp `procedures.ingestion_context_id` → emit one `document_procedure` **Observation** → emit one `evidence(type='document', strength 0.3, independence_group='skill_md:'+hash)` row → `complete_ingestion_context`. `ingested_artifacts` now carries `source_ref` + `ingestion_context_id`. **B2:** both `_write_task_nodes` call sites removed (grep-confirmed no other callers) — ingestion no longer manufactures task_nodes. `_persist_package_relations` `ON CONFLICT` retargeted to migration 67's `(procedure_id, implementation_id, role) WHERE t_invalid IS NULL`. **Merged with upstream `976b647`'s admission gate** (`classify_admission` — `reject`/`review`/`admit`): the admission decision runs first; the canonical chain runs for `admit` and `review` (a `review` outcome writes the procedure `availability='quarantined'` but still fully provenance-tracked), a `reject` returns `status="rejected"` with an `ingested_artifacts` audit row and no Source/context. `IngestOutcome` carries both feature-sets' fields. Tests: `test_skill_ingestion_offline.py` (chain + admission both asserted); `test_ingestion_admission_offline.py` (upstream's, still green).
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

- ~~**T2**~~ **done** — `test_migrations_64_69_t2_e2e.py` (8 passed against local): additive-only check on all 6 files, idempotent re-run of each `db/5N_*.sql`, representative-row insert + named-CHECK rejection for every new table. `test_schema_drift.py` → 2 passed (no drift). Rollback is intentionally not automated (fresh-start rule 1), documented.
- ~~**T3**~~ **done** — `test_ingestion_canonical_chain_e2e.py` (2 passed against local): a real procedural SKILL.md → all 12 chain rows verified (Source → IngestionContext → Artifact → artifact_blocks → Observation → Evidence(document) → Claim → procedure_claim_ref(RATIONALE) → screening_decision → Procedure(candidate)), every derived row `ingestion_context_id`-stamped, **no task_nodes**; a non-procedural doc → no Procedure.
- ~~**A33 corpus backfill**~~ **mechanism proven** — `test_backfill_refs_from_preconditions_e2e.py` (1 passed, local): seeds representative rows, asserts `preconditions[*].claim_id` → `PRECONDITION`/`backfilled` typed refs, claim-less preconditions ignored, full idempotency. The once-per-environment run against the production corpus is still a deploy step (pass a `limit` ≥ the live-procedure count — the scan is `ORDER BY t_created ASC LIMIT n`, documented).
- **DB-backed regression sweep** — the full `dbtarget local -- pytest tests` run (offline + ~360 `*_e2e.py`) has ~35 pre-existing e2e failures from missing env (LLM keys, OIDC auth) / fixture drift / Plan B — none from this lane's work (triaged; only `test_skill_ingestion_e2e`'s B2 assertion was ours, fixed).
- ~~**G1 residual**~~ **done** — `ingestion_jobs.resolve_trace_ingestion_context` opens one `IngestionContext` per trace **session** (`source_type='trace'`, `source_uri='session:<id>'`, resolved from the DB so it is idempotent across workers). `handle_normalize_trace_event` stamps each Observation and threads the id onto the promote job; `handle_promote_observation_to_claim` stamps the derived Claim; `handle_extract_procedure_from_episode` stamps the Procedure version row and its procedure-targeted Evidence. Both ingestion paths now carry `ingestion_context_id`.
- ~~**B9 partial**~~ **done** — `failure_handlers.handle_requires_review` no longer hand-stamps `claim_status='uncertain'`: it writes a non-status `properties.review` marker and calls `recompute_claim_belief` to re-derive the status. `handle_dependency_queue` still sets `'stale'` — but that is a *lifecycle* transition (environment drift → revalidate), outside the belief projection, now documented as the ownership boundary: belief owns `candidate|supported|disputed|uncertain`, lifecycle owns `stale|superseded|invalid|retracted`.
- **G12 / G13 / G14** — audited (below), all OPEN or weak-PARTIAL; each is a real subsystem needing its own design + implementation pass, not a one-migration change:
  - **G12 — local schema-aligned learning + private sync (A11–A13).** `app/local_agent/` is a substantial local-agent layer (`local_store.py` / `local_claims.py` SQLite stores, `local_learning*.py`, `unified_retrieval.py`, history/git/repo bootstrap importers). The **explicit** local→global publication path exists (`publish.py::publish_local_procedure`, gated). **Missing:** the middle tier — an idempotent, content-hash-based, version-aware, resumable, scope-preserving local→`USER_PRIVATE`-cloud **sync protocol** with durable cursors (A13). No `sync` module; local knowledge either stays local or is explicitly published. **State: PARTIAL** (stores + publication exist; automatic private sync does not).
  - **G13 — `.stealth/` working-set projection (§36 / §B35).** There is a `.stealthlab/*.db` local **SQLite cache** (a different thing — the spec explicitly allows it as "a local registry/cache"). **Missing entirely:** the agent-facing `.stealth/context.md` (bounded index-first router, `head`/`grep`-navigable, `[ROUTER]`/`[CLAIMS:*]`/`[PROCEDURES:SELECTED]`/… sections) + `run.json` + `meta.json`; atomic `write-temp → fsync → rename`; staleness detection via `meta.json`; `.stealth/index/` partitioning over budget. No writer for any of it. **State: OPEN** (greenfield).
  - **G14 — global hierarchical retrieval + index freshness (§B37).** RRF fusion + the non-compensatory applicability cascade exist (`retrieval.py`, `applicability.py`); embedding backfill scripts exist. **Missing:** `canonical_revision` / `indexed_revision` / `index_lag` as tracked, queryable values; the "a stale index may return candidates but authoritative version/applicability checks run against canonical rows before selection" guarantee as a first-class enforced mechanism (grep for those terms → nothing). **State: OPEN** for the freshness-tracking contract; the retrieval stages themselves are PARTIAL.
- **Board note owed** — `schema.md` `Implementation → Procedure` 1:1 vs Pass 1's M:N (`schema.md` frozen).
- **External:** `backend/app/config.py` has an uncommitted `embedding_provider_chain` change from another lane (not part of this work; left untouched).

---

## RE-AUDIT vs merged Plan B — `7a6e18f` "MCP + procedure-conditioned execution hardening (B1–B38)" (2026-09-10)

Plan B (the MCP execution lifecycle lane, explicitly **not this lane**) landed on
`main` while this lane was mid-flight. It took migration numbers **50–63**, which
collided with this lane's Pass-1 migrations (also 50–55). **Resolution
(commit `9f9c876`, pushed to `main`):** this lane's six migrations were
`git mv`'d to **64–69** — `64_sources` `65_ingestion_contexts`
`66_procedure_claim_refs` `67_procedure_implementation_relation`
`68_screening_decisions` `69_artifact_blocks` — with every `-- Migration NN`
header, in-SQL sibling cross-ref, and `migration 5X` comment in the 12 touched
services + 6 offline test suites + `publication_deps.py` + this doc bumped.
`test_migrations_50_55_t2_e2e.py` → `test_migrations_64_69_t2_e2e.py`. The SQL
bodies are unchanged; 89 offline tests across the six renumbered-table suites
pass, imports clean.

### Migration ordering — verified clean

Fresh-DB apply order is now `… 39 … 58 → 59 … 64 … 67 …`. The interaction that
mattered: Plan B's **`58_procedure_implementations_and_dependencies.sql`** does a
faithful `CREATE TABLE IF NOT EXISTS procedure_implementations` capture that adds
**exactly the same rich columns** this lane's `67` adds (`role`,
`implementation_version[_constraint]`, `supported_steps`,
`supported_capabilities`, `applicability`, `interface_binding`, `evidence_refs`,
`status`, `ingestion_context_id`, `t_valid`, `t_invalid`), the same
`idx_procedure_implementations_identity` partial-unique index, and the same
`role`/`status` CHECK vocab (as named constraints). After `58`, migration `67`
is **idempotent-inert on a fresh DB** — every `ADD COLUMN IF NOT EXISTS` and
`CREATE … IF NOT EXISTS` no-ops — but still does two non-redundant things `58`
does not: `ALTER COLUMN resource_path DROP NOT NULL` (migration `39` created it
`NOT NULL`) and `DROP CONSTRAINT procedure_implementations_procedure_id_implementation_id_key`
(the legacy 2-col unique from `39`). Both are `IF EXISTS`-guarded / inherently
idempotent, so `67` is safe in either apply order (it already ran first on
`local` under its old name `53_procedure_implementation_relation.sql`; Plan B's
`58` will no-op against it there). **No conflict. Migration `67` kept as-is**
(immutable-once-applied + still meaningful against the pre-`58` shared DB).

### What Plan B closes / advances in THIS lane's gate matrix

| Gate / Test | Was (this audit) | Now, given Plan B | Evidence in `7a6e18f` |
|---|---|---|---|
| **G13** `.stealth/` projection | **OPEN** (greenfield) | **CODE-COMPLETE (via Plan B)** — `app/execution/stealth_projection.py` (230 ln) writes `.stealth/{context.md,run.json,meta.json}` atomically, `[ROUTER]/[CLAIMS]/[PROCEDURES:SELECTED]/[IMPLEMENTATIONS]/[COORDINATION]` section model, `meta.json.projection_revision`; wired into `find_best_way` `plan_only` + `continue_run(repo_path=…)`. Named gaps (Plan B's own deferred doc, items 28–30): `[RELEVANT GLOBAL CLAIMS]` populated only as far as `relevant_claims.py` reaches; `projection_revision` is a gen-time unix ts not a monotonic counter. | `stealth_projection.py`, `test_find_best_way_stealth_projection_e2e.py`, `mcp_server/server.py` |
| **T11** `.stealth/` budget test | **OPEN** (dep on G13) | **PARTIAL** — `test_find_best_way_stealth_projection_e2e.py` exercises the projection end-to-end; a dedicated large-corpus byte-budget/router-navigation assertion (T11 proper) is still not present. | same |
| **T12 / B36** multi-agent file-intent coordination | **NONE** | **CLOSED (via Plan B)** — `app/execution/coordination.py` (239 ln) + `db/56_execution_run_node_file_intents.sql` + `declare_file_intent` MCP tool; read-glob / write-glob conflict + unmet-dependency advisory detection. Gaps (Plan B deferred items 31–34): glob-overlap is a deliberate shared-prefix over-approximation; `symbols_expected_to_modify` stored but not checked; advisory only (nothing forces a host to call it). | `coordination.py`, `test_coordination_e2e.py`, `test_coordination_offline.py`, `test_declare_file_intent_mcp_e2e.py` |
| **G23** `report_execution` → Observation/Evidence/Claim-candidate learning | **PARTIAL** (deferred to Plan B lane) | **CODE-COMPLETE (via Plan B)** — B18 host-executed learning loop, private-by-default extraction from a reported execution. | `test_report_execution_learning_loop_e2e.py`, `server.py` `report_execution` |
| **T8** procedure-conditioned execution E2E | **PARTIAL** | **ADVANCED (via Plan B)** — `test_procedure_run_e2e.py`, `test_mega_chain_e2e.py`, `test_find_best_way_plan_only_e2e.py`, `test_continue_run_implementation_binding_e2e.py`. Still no run through a real sandboxed LLM agent loop (key-gated). | those tests |
| **T9** recursive-execution recovery / cycle+budget guards | **PARTIAL** | **ADVANCED (via Plan B)** — `app/execution/recursion_guard.py` (188 ln) + `db/52_execution_run_recursion.sql` + `durable_resume.py` (222 ln); `test_recursion_guard_e2e.py`, `test_find_best_way_recursion_e2e.py`, expanded `test_durable_run_e2e.py`. Complements this lane's G9 canonical-composition cycle gate (they guard different layers: G9 = authoring graph, Plan B = runtime child-run recursion). | those |
| **T10** verification ladder | **PARTIAL** | **still PARTIAL** — Plan B added `app/services/verification.py` (272 ln) + `db/55_verification_results.sql` + `execution/behavior_verification.py` + `verifiers/`, but Plan B's **own** deferred doc (item 10) still calls the *ranked 6-class evidence ladder* (`SELF_REPORT`…`REAL_WORLD_OUTCOME`) a GAP — types exist, nothing ranks/requires them. Unchanged verdict. | `verification.py`, `test_*verification*` |
| **T1** TEST/STAGING/PROD fail-closed gate | **CODE-COMPLETE (DB-UNVERIFIED)** (this lane, `7495878`) | **unchanged — this lane is ahead of Plan B here.** Plan B's deferred doc item 11 still lists this as a GAP ("safety is incidental, follows which API key is present"); this lane already shipped `config.py` `environment` tri-state + `runtime_guard.assert_production_safe` at `main.py` startup + `test_runtime_guard_offline.py` (the T1 proving test). No merge conflict — different code regions. | `runtime_guard.py` |
| **G8** applicability integration | **CODE-COMPLETE (DB-VERIFIED)** | **unchanged**, note: Plan B added `app/services/relevant_claims.py` (96 ln, `test_relevant_claims_e2e.py`) — a *retrieval* surface for claims relevant to a goal/procedure, distinct from precondition-checking. It reads `procedure_claim_refs` (this lane's `db/66` table). Complementary, no conflict; a future G14 pass should fuse it. | `relevant_claims.py` |
| **G10** Procedure↔Implementation M:N | **CODE-COMPLETE (DB-VERIFIED)** | **CODE-COMPLETE, with a convergence debt.** Two services now write **one table** (`procedure_implementations`): this lane's `services/procedure_implementations.py` (`bind_implementation` / `close_binding` / `add_evidence_ref`) and Plan B's `services/procedure_implementation_bindings.py` (207 ln — `resolve_binding_for_step`, `submit_implementation` MCP tool, wired into `continue_run`). They agree on the schema (same columns, same identity index) and neither is a parallel registry, but the split write API should be reconciled into one module. **New board/handoff item (B17-adjacent).** Plan B's mig `53/54` built a separate `procedure_implementation_bindings` table first, then `58/59` pivoted onto `procedure_implementations` and dropped it — so the final shape matches this lane's `67`. | mig `58/59`, `procedure_implementation_bindings.py`, `test_procedure_implementation_bindings_e2e.py` |
| **G7 / A33** backfill scan window | mechanism proven; corpus run owed | Plan B's **`db/57_procedures_engineering_fixture_default_and_backfill.sql`** fixed an `is_engineering_fixture` visibility drift that had been hiding **~1496 real procedures** from the corpus. The eventual `backfill_refs_from_preconditions` production run now sees the full live set — pass `limit ≥` the corrected count. | mig `57` |

### Still OPEN after the re-audit (unchanged by Plan B)

- **G12** — local schema-aligned learning + private sync (A11–A13). Plan B did
  **not** touch `app/local_agent/`; the middle sync tier is still absent.
- **G14** — retrieval index-freshness contract (`canonical_revision` /
  `indexed_revision` / `index_lag`). Plan B's retrieval-adjacent work
  (`relevant_claims.py`, `applicability.py` +95 ln) does not add lag tracking.
- **T13** — symlink-escape + SSRF-on-locator security E2E.
- **T14** — latency p50/p95/p99 rig.
- **T5–T7** — verification-ladder / adapter-matrix E2E (T10 partial as above).

### Migration-ledger reconciliation (operational — NOT executed here)

`migrate.py --status` against **local** now shows `50`–`69` all `pending`
(Plan B `50`–`63` never applied; this lane's `64`–`69` recorded under the old
names). The `schema_migrations` ledger carries **6 orphan rows**
`50_sources.sql … 55_artifact_blocks.sql` (this lane's pre-renumber names — the
schema objects they created are all present and correct on `local`). Because the
renamed `64`–`69` files are fully idempotent, the safe reconciliation on
**local** is:

```sql
DELETE FROM schema_migrations
 WHERE filename IN ('50_sources.sql','51_ingestion_contexts.sql',
   '52_procedure_claim_refs.sql','53_procedure_implementation_relation.sql',
   '54_screening_decisions.sql','55_artifact_blocks.sql');
```

then `dbtarget.py local -- python scripts/migrate.py` (applies Plan B `50`–`63`
+ re-applies this lane's `64`–`69` as no-ops, recording them under the correct
names). **Hosted** (Supabase, egress-limited): this lane's `50`–`55` were
applied to hosted during Pass 1; the same 6-row `DELETE` + a hosted `migrate.py`
run is owed there, but is a **deliberate operator action** given the egress
budget — left for the user to schedule, not run from here.

> **Local reconciliation DONE (2026-09-10):** the 6 orphan rows were deleted
> and `migrate.py` applied `50`–`69` on `local` in one clean pass — **confirms
> the `58`→`67` ordering is conflict-free against a real DB.** Ledger = 69 rows,
> 0 pending, 0 mismatch (beyond the pre-existing cosmetic 11–34 CRLF set).
> Hosted still owed.

---

## LOCAL WORKING-SET ARCHITECTURE — ratified spec deviation + P1 (2026-09-10)

**Decision (user-ratified).** The local side of StealthLab is redefined as a
**filesystem-native working set + a durable runtime journal**, with **no local
SQLite / local Postgres**. Global Postgres + pgvector stays authoritative for
semantic retrieval, the Claim graph, ranking, permissions, publication. "Cloud
decides what knowledge is relevant; the local filesystem makes that knowledge
cheap for (small) agents to navigate and execute." Navigation is a *knowledge
page fault*: `grep index/*.idx → object id + exact line range → sed the range →
continue`; a local miss calls the Stealth MCP, which projects N objects locally
and regenerates the index.

**Frozen-spec deviation (board note owed — P6).** Spec v4 A11/A12/B35 model
`.stealth/` as a *disposable projection* preferring a single `context.md`, and
call local SQLite "a local registry/cache" to be reused. This decision (a)
removes the SQLite local store entirely and (b) promotes `.stealth/` to the
**authoritative local representation** an agent acts on (global remains
long-term truth; `.stealth/` is page cache — trusted like a cache, staleness
detected via `meta.json`, refilled on fault). Recorded here as deliberate; the
`.scratch/` board note is P6.

### Layout produced by `app.stealth.generator.generate_projection`

```
.stealth/
  context.md            compact B35 router (kept — compat + B35 readers)
  run.json              machine-readable current run (kept)
  meta.json             projection_revision + per-type revisions + change_cursor (staleness)
  claims.md  procedures.md  implementations.md  run.md     addressable object pages
  index/
    root.idx            name|target|hint            (router, ≤ 4096 B, generator raises if over)
    claims.idx  procedures.idx  implementations.idx  id|version|scope|status|tags|file|start|end|summary
    run.idx            node|status|owner|deps|globs|file|start|end|summary
```

Index line ranges are 1-based inclusive and **disposable** — regenerated every
run, never edited in place. `|` / newlines in any field are sanitised so the
parser stays one `str.split` and a value can't forge a second row.

### P1 — landed (green-lit; D2 + D3 approved)

| Piece | Where |
|---|---|
| `app/stealth/` package — `format.py` (idx/md codecs + budgets), `atomic.py` (`atomic_write` + `atomic_write_batch`, `meta.json` written LAST), `legacy_context.py` (the B35 `_render_*` verbatim), `generator.py` (`generate_projection`), `errors.py` | new |
| `app/execution/stealth_projection.py` | now a **thin shim** re-exporting `generate_projection` / `_render_*` / `_atomic_write` / `STEALTH_DIRNAME` / `CONTEXT_MD_MAX_BYTES` / `StealthProjectionError` — every existing import + both `mcp_server/server.py` call sites unchanged |
| `test_stealth_format_offline.py` (13) — codec round-trips, separator-injection safety, md line-range correctness, run-scoped builders, T11 bounded-router with a 400-object synthetic working set | new |
| `test_stealth_projection_e2e.py` (+1, now 5) — every `.idx` row slices out exactly its block, root router ≤ budget, regeneration byte-identical, `meta.json` staleness signal | extended |
| `test_stealth_projection_offline.py` (9), `test_find_best_way_stealth_projection_e2e.py` (1) — updated `listdir` assertions from exact-set to superset (new pages + `index/`); otherwise green | touched |

**Verified:** `test_stealth_format_offline` + `test_stealth_projection_offline` → 21 passed (offline); `test_stealth_projection_e2e` + `test_find_best_way_stealth_projection_e2e` → 6 passed against `local`.

### Still to do (P2–P6)

- **P2** — MCP server as the single writer; `events.jsonl` append-only journal with `seq`; advisory file locking; `stealth` read-CLI.
- **P3** — the knowledge page-fault: `project_knowledge(ids|query)` MCP tool → global retrieval → append `.md` + regenerate `.idx`; `[RELEVANT GLOBAL CLAIMS]` stops being honestly-empty.
- **P4** — `exploration.md` + multi-agent owners / file-intents in `run.*` (reuses merged `coordination.py`).
- **P5** — remove the SQLite local store: delete `local_store.py`, `local_claims.py`, `local_learning_sweep.py`, the 4 bootstrap importers, `publish_local_procedure`, `unified_retrieval.py`, `local_applicability.py`, `local_ingestion.py`, `local_episode_evidence.py`; **keep `runner.py`** (execution provider, Plan B); rewire `ingestion_scheduler` / `ingestion_admission`. Trace-derived private learning goes to the global DB private-scoped (the `ingestion_jobs.py` path).
- **P6** — `.scratch/` board note for the deviation + gate-matrix close-out.

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

**Added:** `db/64_sources.sql`, `db/65_ingestion_contexts.sql`, `db/66_procedure_claim_refs.sql`, `db/67_procedure_implementation_relation.sql`, `db/68_screening_decisions.sql`, `db/69_artifact_blocks.sql`. Migration series is now 56 files (upstream `49_ingestion_admission_audit.sql` + mine 64–69).
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

Release-critical unless noted. `CLOSED` = spec requirement met **and** DB/E2E-verified. `CODE-COMPLETE (DB-VERIFIED)` = migration applied + writer wired + offline proving tests + a T2/T3 DB assertion green, but coverage of adjacent concerns (e.g. more detector classes, trace-path parity) still owed. `PARTIAL` = substantial mechanism exists with a named gap. `OPEN` = not built / not started. Migrations 64–69 are **applied** (hosted + local, 0 pending, 0 mismatch); **T2** (`test_migrations_64_69_t2_e2e.py`) and **T3** (`test_ingestion_canonical_chain_e2e.py`) pass against local.

### Plan A gates (state after Implementation Pass 2)

| Gate | Item | State | Note |
|---|---|---|---|
| **G0** | Baseline SHA + schema/API contract + config inventory | **CLOSED** | This document. `main@1663c94`; offline baseline 2556/360/7; post-Pass-2 see §J. |
| **G1** | `IngestionContext` / provenance manifest | **CODE-COMPLETE (DB-VERIFIED)** | mig 65 `ingestion_contexts` + back-links on 6 tables. **Both paths wired:** `compile_skill_artifact` (document, T3-verified end-to-end — every derived row stamped) and `ingestion_jobs.resolve_trace_ingestion_context` (trace, offline-tested — one context per session, stamps observation→claim→procedure→procedure-evidence). Remaining: trace-path DB E2E; the trace context is left `open` (spans many async jobs — documented). |
| **G2** | Source + Artifact normalization + immutable block addressing | **CODE-COMPLETE (DB-VERIFIED)** | mig 64 `sources` + mig 69 `artifact_blocks`; `normalize_markdown` invoked by `compile_skill_artifact`. **T2** (offset + identity CHECKs) + **T3** (blocks in the live chain, `content[start:end]` round-trips) pass. Block-span citation + block-text secret redaction landed (`035adf6`). Remaining: a `Source [V]` for the trace path. |
| **G3** | Security / policy screening (`ALLOW/QUARANTINE/REJECT` + detector provenance) | **CODE-COMPLETE (DB-VERIFIED)** for the record; **PARTIAL** for coverage | mig 68 `screening_decisions` (applied) + `screening.py`; **now invoked** by `compile_skill_artifact` (one row per finding, alongside the existing downgrade). Remaining: a screen `REJECT` does not yet abort capture (policy deferred); PII/license/malware detectors; SSRF check on locators; block-level redaction; T13. |
| **G4** | Observation extraction / validation (+ structural source classification §6) | **PARTIAL** (advanced) | Document path emits one `document_procedure` Observation with `ingestion_context_id`. Still missing: Observations per `artifact_block` / block-span citation; the `PROCEDURE/REFERENCE/CLAIM/…` source classifier (B13). |
| **G5** | Independent Claim normalization, belief, dedup, conflict/family | **PARTIAL** (advanced) | **B7/B10 done**. **B8 done** — `claim_belief.py`: evidence-only belief with independence de-dup + document ceiling 0.5, written via a ChangeSet citing evidence; hooked on `record_claim_evidence` + `relate_claims`. **B9 done** for evidence/relation paths (`status_from_belief`) — but `failure_handlers.py` still writes `claim_status` directly. **Document path now derives one Claim** (`bb2deba`). Still missing: structured `subject/predicate/object` columns unpopulated by `capture_claim`; T4. |
| **G6** | Evidence normalization + independence accounting | **PARTIAL** (advanced) | **B10 done**. Document path writes a typed `evidence(type='document')` row (`independence_group='skill_md:'+hash`) for the procedure; belief engine consumes claim-targeted evidence with independence de-dup. Still: no per-Claim document Evidence row yet. |
| **G7** | Procedure extraction / validation / versioning | **CLOSED** *(trace path, offline)* / **PARTIAL** *(overall)* | **B15 done** (`035adf6`): source-supplied `goal_text` required, no fabricated goal/outcome. Document path still has no groundedness validator. |
| **G8** | Procedure↔Claim typed refs + applicability integration | **CODE-COMPLETE (DB-VERIFIED)** | mig 66 `procedure_claim_refs` + service + **B5 role-aware invalidation** (`claim_impact.py`); legacy scan kept as compat. Document path authors a `RATIONALE` ref (T3-verified in the live chain); T2 verifies the role vocab + identity UNIQUE. Remaining: run `backfill_refs_from_preconditions` (A33); wire `add_procedure_claim_ref` into the *trace* extractors. |
| **G9** | Procedure composition validation (cycle rejection, version-pinned child refs, runtime≠canonical) | **CODE-COMPLETE (DB-VERIFIED)** | `procedure_graph.validate_procedure_composition_{definition,in_storage}` (`035adf6`) — write-time gate rejecting cycles / unresolved pinned refs / max-depth; wired into `capture_procedure` + `supersede_procedure`. `test_procedure_composition_e2e.py` (DB) + `test_procedure_graph_offline.py`. |
| **G10** | Implementation Registry validation + Procedure↔Implementation M:N relation metadata | **CODE-COMPLETE (DB-VERIFIED)** | mig 67 generalizes `procedure_implementations` (T2: the new columns are usable + role vocab enforced against the live DB); `procedure_implementations.py`; `solution_implementations.py` reads it unioned with the legacy path. Remaining: converge `implementation_tasks`; board note for the `schema.md` 1:1↔M:N discrepancy (B17); a real 3-adapter T6. |
| **G11** | Static/global ingestion refactor + corpus migration/backfill | **PARTIAL** (advanced) | **B1 done**: document path runs Source→IngestionContext→Observation→Evidence(document)→Claim→Procedure, plus `artifact_blocks` + `screening_decisions`; admission gate unioned. **B2 done**: no task_nodes at ingestion. Still: **corpus backfill un-run**; trace-path Source table; T3 golden E2E. |
| **G12** | Local schema-aligned learning + scope / private sync | **OPEN** | Not touched by this lane or Plan B. Middle sync tier still absent. |
| **G13** | `.stealth/` projection service | **CODE-COMPLETE + P1 of the ratified local-architecture rebuild** | Plan B shipped the B35 trio; **P1** (this lane) adds the filesystem-native working set — `app/stealth/` package, addressable `claims/procedures/implementations/run.md` + `index/*.idx` grep routers, byte-budgeted root router, atomic batch write. `stealth_projection.py` is now a shim. P2–P6 (journal, page-fault, coordination, SQLite removal, board note) remain — see the "LOCAL WORKING-SET ARCHITECTURE" section. |
| **G14** | Global hierarchical retrieval + index freshness | **OPEN** | Retrieval stages exist; the `index_lag` freshness contract is still unbuilt. Plan B's `relevant_claims.py` adds a claims-retrieval surface but no lag tracking. |
| **G23** | `report_execution` → Observation / Evidence / Claim-candidate learning | **CODE-COMPLETE (via Plan B `7a6e18f`)** | B18 host-executed learning loop, private-by-default extraction; `test_report_execution_learning_loop_e2e.py`. |
| **G24** | Publication / privacy / license / IP dependency traversal | **CODE-COMPLETE (offline-only)** | **B11 done** (`acbd2f5`): `publication_deps.traverse_publication_dependencies` walks procedure→claims→observations→sources→artifacts→evidence, fail-closed (private/org, `PRIVATE_CLASSES`, unresolved/low-reliability source, or traversal-bound hit → blocking); "private evidence ≠ global verification" enforced (`verification_inherited` always false; `global_verification_required` unless ≥2 independent public verification groups). Wired into `publish_procedure`; `publication_records` carries the full traversal + verification determination. Remaining: independent *global* re-verification is recorded-as-required but not executed; license/IP checks are visibility/classification-based only; DB E2E. |

### Testing categories (state after Implementation Pass 1)

| T | State |
|---|---|
| T1 (TEST/STAGING/PROD separation; staging/prod fail-startup on a fake provider; proving test that prod cannot activate a test adapter) | **CODE-COMPLETE (DB-UNVERIFIED)** — `config.py` `environment` tri-state (fail-closed) + `runtime_guard.assert_production_safe` called at `main.py` startup + `test_runtime_guard_offline.py` (19, incl. the proving test). Was OPEN. Remaining: exercise against a real STAGING/PRODUCTION boot. |
| T2 (per-migration fresh + representative-row + idempotency + rollback) | **DONE** — `test_migrations_64_69_t2_e2e.py` (8 passed, local): additive-only, idempotent re-run of each file, representative row + named-CHECK rejection per table. `test_schema_drift.py` → 2 passed. Rollback intentionally not automated (fresh-start rule 1). |
| T3 (golden ingestion E2E) | **DONE** — `test_ingestion_canonical_chain_e2e.py` (2 passed, local): full 12-row document chain + no-Procedure for a non-procedural doc. |
| T4 (claim-graph invariants) | **DONE** — `test_claim_graph_t4_e2e.py` (2 passed, local): B7 zero-anchor claim valid; one Claim → many typed refs in distinct roles; contradictory claims coexist + CONTRADICTS edge; belief change cites its evidence (result + ChangeSet). |
| T5–T7 | **OPEN** — need adapter matrix + verification-ladder E2E. |
| T8 (procedure-conditioned execution E2E) | **ADVANCED (via Plan B)** — `test_procedure_run_e2e.py`, `test_mega_chain_e2e.py`, `test_continue_run_implementation_binding_e2e.py`, `test_find_best_way_plan_only_e2e.py`. Gap: no real sandboxed LLM agent loop (key-gated). |
| T9 (recursive-execution recovery, cycle+budget guards) | **ADVANCED (via Plan B)** — `recursion_guard.py` + `db/52` + `durable_resume.py`; `test_recursion_guard_e2e.py`, `test_find_best_way_recursion_e2e.py`. Complements this lane's G9 authoring-graph gate. |
| T10 (verification ladder) | **PARTIAL** — Plan B added `verification.py` + `db/55_verification_results.sql` + `behavior_verification.py`, but the *ranked 6-class* ladder is still a GAP (Plan B deferred item 10). |
| T12 (multi-agent file-intent coordination) | **CLOSED (via Plan B)** — `coordination.py` + `db/56_execution_run_node_file_intents.sql` + `declare_file_intent` MCP tool; `test_coordination_e2e.py`, `test_declare_file_intent_mcp_e2e.py`. Gaps: glob-overlap over-approximation, `symbols_expected_to_modify` unchecked, advisory-only. |
| T13–T14 | **OPEN** — symlink/SSRF security E2E (T13); latency p50/p95/p99 rig (T14). |
| T11 (`.stealth/` budget) | **PARTIAL → advancing** — `test_stealth_format_offline.py` asserts the bounded root router + per-object line-range navigation over a 400-object synthetic working set; `test_stealth_projection_e2e.py` asserts every `.idx` row resolves to exactly its block + staleness via `meta.json` against a real run. Remaining for full T11: a thousands-of-object corpus via the P3 page-fault path. |
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

**Steps 1–13 landed across Pass 1 + Pass 2** (this lane's 6 migrations — renamed `64–69` after the Plan B collision, `9f9c876` on `main`; schema objects applied to hosted + local under the old `50–55` names, ledger reconciliation owed; 9 new services; wired into `compile_skill_artifact` incl. `artifact_blocks` + `screening` + document-Claim; `claim_belief.py`; `publication_deps.py`). Step **15 (placeholder goal in `ingestion_jobs.py`)** — done in Pass 3 (`035adf6`, B15).

## Handoff — what remains

1. **DB proving tests (T2/T3).** A local Postgres is wired (`dbtarget local`). Owed: `test_schema_drift.py` + `test_migration_upgrade_e2e.py` green against it; per-migration T2 tests for 64–69 (fresh-DB apply / representative-row / idempotent re-run / rollback-or-documented-irreversibility); a golden ingestion E2E (T3) — real SKILL.md → assert `sources`/`ingestion_contexts`/`artifact_blocks`/`observations`/`evidence(document)`/`procedure_claim_refs`/`procedure` all present with correct provenance and **no** `task_nodes`; a non-procedural doc → Source + blocks + Observation, **no** Procedure.
2. **Run `procedure_claim_refs.backfill_refs_from_preconditions`** against the real corpus (A33); record before/after counts. (Local DB is schema-only — run against a corpus-loaded DB.)
3. **B15** — `ingestion_jobs.py` hardcoded placeholder goal + unconditional `outcome="success"` in the trace→procedure sweep.
4. ~~**B9 residual**~~ — done (`4f39e9f`).
5. **B1 residual** — block-level secret redaction (`artifact_blocks.text` is from raw content); cite the document Observation to a specific block span; a screen `REJECT` currently only warns, does not abort capture (policy call).
6. **G9** — done (Pass 3). **G12 / G14** — still OPEN (local/private sync tier; retrieval index-lag contract). **G13** — now CODE-COMPLETE via merged Plan B (`stealth_projection.py`); T11 large-corpus budget assertion still owed.
7. **Board note** — `schema.md` models `Implementation → Procedure` 1:1; Pass 1 + spec B23 use M:N via `procedure_implementations`. `schema.md` is frozen → board note, not an edit.
8. **Migration renumber** — done (`9f9c876`, on `main`): this lane's `50–55` → `64–69` after the Plan B collision. **Ledger reconciliation** on local + hosted is owed (6-row `DELETE` + `migrate.py` re-run — SQL in the re-audit section); the renamed files are idempotent so the re-apply is a no-op.
9. **G10 convergence debt (new)** — `services/procedure_implementations.py` (this lane) and `services/procedure_implementation_bindings.py` (Plan B) both write `procedure_implementations`. Same schema, no parallel registry, but the split write API should be merged into one module. Migration `67` is now idempotent-inert on a fresh DB after Plan B's `58` (verified clean); kept as-is (immutable + still meaningful pre-`58`).
10. **Plan B is MERGED** (`7a6e18f`, on `main`) — no longer "pending / not this lane". Re-audit section above maps what it closes here (G13, G23, T12 CLOSED; T8/T9 advanced; T10 still partial). Plan B's remaining in-lane gaps: ranked verification ladder (T10), G15–G22/G25–G26 execution-lifecycle items per `MCP_HARDENING_DEFERRED_ITEMS.md`.
11. **External** — `backend/app/config.py` has an uncommitted `embedding_provider_chain: "gemini,voyage" → "gemini"` change from another lane; left untouched here.
