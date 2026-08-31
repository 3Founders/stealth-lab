# Phase 2 — Global Procedural Library ingestion compiler: implementation plan

**Status:** proposed, awaiting approval. Source brief: `prompts.md`.
**Architectural constraint:** the current codebase + `.scratch/research/global-procedural-memory-architecture-audit-2026-08-30.md`
(§E and §P are literally "the ingestion compiler"). The two `.scratch/phase1_*.md`
files the brief names do not exist; per founder ruling we treat the live substrate
as the contract.

---

## 1. What already exists (reuse, do not rebuild)

| Brief asks for | Already in repo | Gap |
|---|---|---|
| SKILL.md parsing | `app/services/skill_ingestion.py::parse_skill_md` (frontmatter, numbered/bulleted steps, `applies_when` kept as prose) | none — reuse verbatim |
| parse→dedup→write | `skill_ingestion.py::ingest_skill_md` (novelty ≥0.90 via `find_applicable_procedures`, embeds `input_type="document"`, `capture_procedure(provenance="prior_library")`) | no task nodes, no source provenance, no staleness, no manifest |
| `ExtractedProcedure` contract | `app/services/procedure_extraction/schema.py` (pydantic: name/goal/capability_statement/steps/slots/preconditions/scope/exclusions/failure_conditions/invariants) | SKILL.md path bypasses it — maps straight to `capture_procedure` kwargs |
| `ExtractionStrategy` ABC | `procedure_extraction/strategies.py` (`DeterministicExtractor`, `GroundedHybridExtractor`) | **trace-shaped by design** — its `extract()` takes `ProcedureEvidence` (tool sequences/observations). A SKILL.md is a document; `skill_ingestion.py`'s docstring already established the separate document path. Keep separate. |
| capability abstraction | `procedure_extraction/capability.py`, `GroundedHybridExtractor` capability pass | not wired to the SKILL.md path |
| procedures schema | `db/18_procedures.sql` + `19` (embedding) + `20` (`capability_statement`, `approval_status`, `extracted_by`, `procedure_extractors`) | — |
| `task_nodes` | `db/01_ontology.sql` (name, description, io_schema, embedding, provenance, bitemporal) | nothing links a procedure's steps to task_nodes |
| polymorphic edges incl. `procedures` | `db/18` widened `edges` CHECK to `('knowledge_nodes','task_nodes','procedures')` | no producer writes procedure→task_nodes edges yet |
| `find_applicable_procedures` | `app/services/applicability.py` (cold-start gate → non-compensatory cascade → similarity rank; `invariant_bindings` supported) | — |
| dedup / merge | `procedures.py::merge_duplicate_procedures` (survivor rule, tombstone losers, `SUPERSEDES/DUPLICATE_OF` edge, ChangeSet) + `dedup.find_duplicate_clusters` | pre-insert novelty exists; post-insert cluster merge exists; **no version-supersede writer** |
| ingestion jobs / counts | `app/services/ingestion_jobs.py` (SKIP LOCKED queue, per-handler counts, `process_pending_jobs`) | it's a *job queue*, not a *run manifest*; job types are trace-only |
| CLI | `scripts/run_ingestion.py` (argparse `--once/--interval`), `scripts/seed_canonical_coding_procedures.py` | no `skill-dir` / `skill-repo` subcommands |
| lifecycle / staleness enum | `procedures.staleness ∈ {fresh,stale,revalidating}`, `procedures.py::mark_procedure_stale` | nothing derives staleness from a *source content hash* |

**Net:** ~60% of the pipeline is present. This phase is wiring + four small new
surfaces, not a greenfield build.

---

## 2. New surfaces (the actual work)

### 2a. Source-adapter abstraction — `app/services/ingestion_sources/`
Smallest thing that generalises (brief §2, §13). **Not** GitHub-specific internally.

- `base.py`
  - `@dataclass SourceArtifact`: `source_type: str`, `uri: str`, `repository: str|None`,
    `path: str|None`, `commit: str|None`, `content: str`, `content_hash: str` (sha256),
    `discovered_at: datetime`.
  - `class SourceAdapter(Protocol)`: `discover() -> AsyncIterator[SourceRef]`,
    `fetch(ref) -> SourceArtifact`, `fingerprint(artifact) -> str` (defaults to `content_hash`).
- `skill_md.py`
  - `LocalDirSkillSource(root: Path)` — globs `**/SKILL.md` / `**/*.skill.md`.
  - `GitHubSkillSource(repo_url, ref="HEAD")` — resolves raw file URLs; commit SHA
    captured into `SourceArtifact.commit`. Network only in `fetch()`; `discover()` uses
    the GitHub contents API. HTTP via existing `httpx` usage in the repo.
- Dispatch: `SOURCE_ADAPTERS = {"skill_md_dir": ..., "skill_md_repo": ...}`. Only
  SKILL.md now; table shape lets `github_workflow`, `documentation`, ... slot in later.

### 2b. The compiler — extend `app/services/skill_ingestion.py`
New `async def compile_skill_artifact(pool, artifact: SourceArtifact, *, embedder, client=None, domain=None) -> IngestOutcome`:

1. `parse_skill_md(artifact.content)` (reuse).
2. **Capability statement:** if `client` configured, one `GroundedHybridExtractor`-style
   abstraction call over `description + steps` → abstract `capability_statement`;
   else `capability_statement = None` and record `abstained_capability=True` (brief §6,
   §3 "prefer ABSTAIN over hallucinated structure"). Never fabricate.
3. **Staleness / version detection** (brief §10, §11) against new `ingested_artifacts`:
   - hash unseen → new procedure.
   - hash matches an existing artifact row → **no-op**, bump `last_seen`.
   - same `(source_type, uri)` but new hash → `supersede_procedure(...)` → new version
     row, `SUPERSEDES` edge, old row `t_invalid`. New `ingested_artifacts` row.
   - `applies_when`/steps mention a deprecated API + a version token → set
     `staleness='stale'` on the superseded row via `mark_procedure_stale`.
4. **Novelty / dedup** (brief §9): `check_novelty` (≥0.90). On duplicate → attach an
   `ingested_artifacts` row pointing at the *existing* `procedure_id` + append a
   provenance entry to its `evidence_refs`; do **not** insert, do **not** merge on
   name alone. Lineage preserved.
5. **Write** (novel case): `capture_procedure(... provenance="prior_library",
   domain_payload={"source": {...full artifact provenance...}, "applies_when": prose},
   embedding=<document embedding of capability_statement or description>,
   invariants=<only if explicitly supplied>)`.
6. **Task nodes** (brief §4): for each parsed step, insert one `task_nodes` row
   (`name`=slugified step goal, `description`=step text, `provenance='prior_library'`,
   `created_by='skill_md_ingestion'`) and one `edges` row
   (`edge_type='DECOMPOSES_TO'`, `source_table='procedures'`, `target_table='task_nodes'`,
   `properties={"order": i}`). Procedure `steps` JSON stays planner-neutral and unchanged.
7. Return `IngestOutcome(status ∈ {captured, new_version, duplicate, stale_noop, rejected},
   procedure_id, version_row_id, task_node_ids, artifact_id, reason)`.

### 2c. Provenance + manifest schema — `backend/db/32_ingestion_provenance.sql`
Additive, idempotent. Header states "next free number: 32" (31 is highest).
**NOT** `global_procedures`, **NOT** `ingested_tasks` — these are provenance/telemetry
side tables, exactly the `content_hash / first_seen / last_seen` the brief §11 asks to retain.

- `ingested_artifacts` — `id`, `source_type`, `uri`, `repository`, `path`, `commit`,
  `content_hash`, `extractor_version`, `procedure_id` (uuid, the logical handle it fed),
  `procedure_row_id` (uuid, the exact version), `first_seen`, `last_seen`, `run_id`.
  `UNIQUE (source_type, uri, content_hash)`. Standard bitemporal + `owner_id`/`visibility`.
- `ingestion_runs` — `run_id` (uuid7), `started_at`, `finished_at`,
  `metrics JSONB` (`sources_seen/artifacts_seen/candidates/accepted/duplicates/
  rejected/stale/errors` — brief §14 shape verbatim), `source_spec JSONB`, `created_by`.

### 2d. Version-supersede writer — `app/services/procedures.py::supersede_procedure`
Referenced by existing docstrings, never implemented. Minimal:
`supersede_procedure(pool, *, prior_row_id, **changed_fields) -> {id, procedure_id, version}`
— re-inserts a `procedures` row reusing `procedure_id`, `version = prior.version + 1`,
carries forward unchanged fields, sets prior `t_invalid = now()`, writes a `SUPERSEDES`
edge (mirrors `merge_duplicate_procedures`' edge idiom), records a ChangeSet.
Half-gate: lands in the same change as its caller (2b step 3).

### 2e. CLI — `backend/scripts/ingest_skills.py`
argparse style matching `run_ingestion.py`. Subcommands:
- `skill-dir <path> [--domain D] [--dry-run]`
- `skill-repo <github-url> [--ref R] [--domain D] [--dry-run]`
- `search "<query>" [--k N]` → thin wrapper over `find_applicable_procedures`
  (`require_verified=False`), prints name/version/similarity/verification_state.
Writes one `ingestion_runs` row per run, prints the metrics JSON to stdout (brief §14).

---

## 3. Tests — written first (brief §1)

**Offline** (no `DATABASE_URL`, `FakePool`/`FakeConn` per existing convention):
- `tests/test_ingestion_sources_offline.py` — `LocalDirSkillSource.discover/fetch/fingerprint`;
  `content_hash` stable & sensitive; `GitHubSkillSource` URL resolution (monkeypatched HTTP).
- extend `tests/test_skill_ingestion_offline.py` — task-node rows emitted per step;
  procedure→task_nodes edges with `order`; source provenance lands in `domain_payload.source`;
  hash-unchanged → `stale_noop` no write; hash-changed → `supersede_procedure` path;
  capability abstained (no client) → `capability_statement` stays null, no fabrication;
  manifest counts add up (`candidates == accepted + duplicates + rejected + stale`).
- `tests/test_supersede_procedure_offline.py` — version increments, prior `t_invalid` set,
  `SUPERSEDES` edge emitted, unchanged fields carried.

**Live** (`skipif` no `DATABASE_URL`, self-cleaning by name prefix — matches `*_e2e.py`):
- `tests/test_skill_ingestion_e2e.py` — brief §16:
  1. real public SKILL.md (vendored fixture committed under `tests/fixtures/`,
     **plus** an opt-in truly-networked variant gated by `STEALTHLAB_LIVE_FETCH=1`),
  2. run the real compiler,
  3. assert `procedures` row (independent `SELECT`), `capability_statement`/`provenance`,
  4. assert `task_nodes` rows + `edges` rows exist,
  5. assert `embedding IS NOT NULL`,
  6. assert `ingested_artifacts` + `ingestion_runs` rows,
  7. `find_applicable_procedures` returns it,
  8. re-run same content → `stale_noop`, no new rows; mutate content → new version.

**Proving-test rows** (ROADMAP Appendix C discipline): ship in the same commits as the code.

---

## 4. Migration / lane note

`backend/db/**` is **Lane CORE-A**'s owned path. Migration 32 either goes through a
board request or a founder OK. Everything else (`app/services/ingestion_sources/`,
`skill_ingestion.py`, `scripts/`, `tests/`) is currently unowned. Flagging now, not after.

`DATABASE_URL`: none in this checkout. Live tests are `skipif`-guarded and run once a
local Postgres 15 + pgvector is pointed at it (then Supabase cutover as a separate step).

---

## 5. Subagent split

Dependency order: **A + B parallel → C → D**.

| Agent | Scope | Files | Depends on |
|---|---|---|---|
| **A — schema + supersede** | migration 32 (`ingested_artifacts`, `ingestion_runs`); `supersede_procedure()` + offline test | `backend/db/32_*.sql`, `backend/app/services/procedures.py`, `backend/tests/test_supersede_procedure_offline.py` | — |
| **B — source adapters** | `ingestion_sources/{base,skill_md}.py` + offline tests | `backend/app/services/ingestion_sources/**`, `backend/tests/test_ingestion_sources_offline.py` | — |
| **C — the compiler** | `compile_skill_artifact()` wiring parse→capability→dedup/staleness→capture→task_nodes→edges→artifact/run rows; extend offline tests | `backend/app/services/skill_ingestion.py`, `backend/tests/test_skill_ingestion_offline.py` | A (artifact/run tables, `supersede_procedure`), B (`SourceArtifact`) |
| **D — CLI + live proof + report** | `scripts/ingest_skills.py`; `tests/test_skill_ingestion_e2e.py` + vendored fixture; `.scratch/phase2_ingestion_report.md` | `backend/scripts/ingest_skills.py`, `backend/tests/**`, `backend/tests/fixtures/**`, `.scratch/**` | C |

Each agent: write failing tests first, run `python -m pytest tests -q` (offline) before
handing back, paste counts. Integrator (me) merges A+B, then briefs C, then D, then runs
the full offline suite + the live suite once a DB is available, and writes the final
`.scratch/phase2_ingestion_report.md` (brief §17).

---

## 6. Explicit non-goals (brief §18)

No SLM/WASM execution. No UGC. No research claim graph. No retrieval redesign. No
`global_procedures` / `ingested_tasks` / parallel DB. No automated repair system.
No GitHub-workflow / docs / paper adapters (dispatch shape only).
