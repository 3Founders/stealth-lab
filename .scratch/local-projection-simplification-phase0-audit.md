# Phase 0 Audit — Simplifying the `.stealth/` local projection

Scope: map the real current pipeline (source → ... → `.stealth/`) before touching any
code, per the "boring grep-able local projection" redesign request. Produced by reading
`app/stealth/`, `app/execution/{durable_run,durable_resume,episode,recorder,stealth_projection}.py`,
`app/services/{claim_extraction,skill_ingestion,relevant_claims,procedure_claim_refs,verification}.py`,
`app/services/ingestion_sources/`, `app/services/procedure_extraction/`,
`app/services/{trace_collector,trace_worker}.py`, `backend/db/*.sql`, and
`app/mcp_server/server.py`. No code was changed to produce this document.

## CURRENT pipeline, as it actually exists today

```
source (SKILL.md / AGENTS.md·CLAUDE.md / CI workflow / runbook)
  → SourceAdapter.discover()/fetch()/fingerprint()   (app/services/ingestion_sources/*)
  → ingested_artifacts (content_hash-addressed, migration 32)
  → artifact_blocks (normalized, anchored sub-document structure, migration 69)
  → skill_ingestion.py: compile_skill_artifact()/ingest_skill_md()
       ├─ capture_procedure()                         → procedures (migration 18)
       └─ _emit_document_screening_and_claims()
            → claim_extraction.extract_claim_candidates_cached()
            → capture_claim()                          → knowledge_nodes node_type='claim' (01+21)
somewhere in parallel, live execution produces the other leg:
execution_plans/task_graphs → execution_runs/execution_run_nodes (durable_run.py, migration 36)
  → ExecutionRecorder → execution_run_events (append-only, seq-ordered)
  → episode (1:1 w/ execution_run, open at start / close at terminal status, same txn)
  → close triggers ingestion_jobs: consolidate_local_episode
  → observations/observation_events (migration 14, immutable, no confidence field)
  → (candidate claims/procedures from observations — same claim_extraction/procedure_extraction
     machinery as the document leg, not a separate system)
verification:
  procedures.postconditions → derive_criteria() (in-memory, not persisted as its own row)
  → verification_results (migration 55, per (execution_run_id, criterion_id), UPDATE-in-place
     re-checks, not append-only history)
  execution_run_nodes.verification_state — ONE scalar column per node (unverified/verified/failed),
     wholly separate from verification_results, which is run-scoped not node-scoped
evidence:
  evidence (migration 24) — append-only (DB trigger blocks DELETE, only t_invalid tombstone),
     target_type ∈ {claim, procedure, implementation}, direction ∈ {supports, contradicts}
canonical DB → .stealth/ projection:
  app/stealth/generator.py::generate_projection(pool, workspace_root, procedure_run_id)
     reads execution_runs + get_run_context() + fetch_procedure_version() +
     evaluate_run_completion() + _fetch_file_intents()
     writes, atomically (SingleWriterLock + atomic_write_batch, meta.json last):
       context.md, run.json, meta.json  (B35 compact trio)
       claims.md + index/claims.idx
       procedures.md + index/procedures.idx
       implementations.md + index/implementations.idx
       run.md + index/run.idx
       exploration.md + index/exploration.idx (conditional)
       index/root.idx
       events.jsonl (via journal.py — NOT a mirror of execution_run_events; a
                     separate local-only journal of projection-regeneration bookkeeping)
```

## Key findings that determine the redesign

1. **`app/execution/stealth_projection.py` is a pure back-compat re-export shim.**
   All real logic is in `app/stealth/`. Treat `app/stealth/` as the only implementation
   to modify; the shim needs no parallel changes beyond what it already re-exports.

2. **Node verification is currently split across two disjoint mechanisms**, exactly as
   the task's §10 complains:
   - `execution_run_nodes.verification_state` — one scalar per node (unverified/verified/failed).
   - `verification_results` — real per-criterion rows, but keyed by `execution_run_id` only,
     **not linked to a node**. Criteria are derived live from `procedures.postconditions`,
     never persisted as their own row/table (no `verification_criteria`/`TestSpec` entity exists).
   To get `VERIFY|N-003|V-001|...` semantics, `verification_results` needs a **node-scoping
   column** (a nullable FK/column to `execution_run_nodes.id` or `node_order`), not a new
   verification system. This is an additive migration, not a replacement.

3. **The required SUPPORT/REFINE/SUPERSEDE/CONTRADICT/INVALIDATE claim-update classification
   does not exist anywhere in the codebase.** What exists: `claim_equivalence.py`'s narrower
   4-value classifier (`equivalent/contradicts/related/unrelated`), detection-only, writing to
   `claim_relation_candidates` for human review — never auto-applied. Separately,
   `claims.py::relate_claims`/`link_claims` can write `SUPERSEDES`/`CONTRADICTS` edges, but only
   as explicit maintenance calls, not derived automatically from new evidence. **This is a real
   gap to build, not a system to "reuse."** It should be built as a thin classification layer
   that (a) calls the existing equivalence classifier as one input, (b) maps its 4 outputs plus
   new REFINE/SUPPORT/INVALIDATE/NEW_SCOPE cases onto the *existing* `relate_claims`/evidence
   machinery, rather than inventing new storage.

4. **`.stealth/events.jsonl` and the canonical `execution_run_events` table are unrelated
   today.** The projection generator never reads `execution_run_events`; `events.jsonl` only
   carries `projection_regenerated`/exploration bookkeeping. Per the task's §12 instruction
   to inspect before adding a second event system: the correct fix is a **bridge**, projecting
   real `execution_run_events` rows (already append-only, already atomic with state transitions)
   into `events.jsonl` at generation time — not a new event system, and not repurposing the
   existing journal's own bookkeeping semantics.

   **Addendum (2026-09-14):** the `ingestion` lane applied 4 previously-pending migrations
   (73, 74, 78, 79) directly against the real database while doing unrelated pipeline-hardening
   work. Migration **79 (`79_local_episode_learning.sql`) adds `episodes.execution_run_id`**,
   which was missing before and is exactly the join key this bridge needs — an `episodes` row
   can now be traced back to the `execution_runs`/`execution_run_events` it came from without a
   separate lookup table. This closes part of the gap above; the bridge function itself (project
   a bounded tail of `execution_run_events` for the active run into `events.jsonl`) is still
   unbuilt, but it no longer needs its own schema change to find the right run.

5. **`supersede_procedure()` (`app/services/procedures.py`) is a real, working production path**
   (bi-temporal new-version-row + carry-forward + `SUPERSEDES` edge + ChangeSet, one transaction),
   already called from `skill_ingestion.py`. No new supersession mechanism is needed for
   Procedures — only for Claims (finding 3).

6. **`procedure_claim_refs` (migration 66) already gives Claim→Procedure impact propagation**
   almost for free: `STRONG_ROLES` (PRECONDITION/APPLICABILITY/ASSUMPTION) are explicitly
   documented to "force stale/revalidation on claim change." The task's §5 "identify dependent
   Procedures → mark stale" requirement is largely **already-built plumbing** — what's missing
   is the trigger that actually calls it when a Claim changes state, and propagation from there
   into any *active* run depending on that Procedure (a new, small piece).

7. **MCP mutation surface is broader than CLAUDE.md's docstring claims.** `KnowledgeUpdater` truly
   is used only by `submit_approval`/`decide_decomposition` for the `knowledge_nodes`/`edges`
   graph specifically — that part of CLAUDE.md is accurate. But `report_execution`, `find_best_way`,
   `reproduce_procedure`, `decide_procedure`, and `submit_implementation` all mutate other
   canonical tables through separate, legitimate write paths (`record_execution_outcome`,
   `approve_procedure`/`reject_procedure`, implementation bindings). `generate_projection` is
   invoked from inside `find_best_way`/`verify_completion`, and separately exposed as its own
   tool via `project_knowledge`. None of this needs to change structurally — it confirms MCP
   already owns "semantic/canonical actions," per the task's §15 requirement — but the redesign
   should route local-knowledge mutation (Claim propose/update, Procedure propose) through
   these same existing service functions, not new ones.

8. **`.idx` files already give near-exactly the requested `index.md` shape** — pipe-separated,
   one row per object, line-range pointers into the matching `.md`. `run.idx` is literally
   `node|status|owner|deps|globs|file|start|end|summary`, already designed for
   `grep -E 'RUNNING|BLOCKED'`. The gap is that today there are *four* separate `.idx` files
   plus a `root.idx` router, and the primary markdown pages (`claims.md`/`procedures.md`) nest
   facts under `## HEADING` blocks with indented `key: value` sub-lines rather than one grep-able
   record per line — exactly the complaint in the task's §2/§3/§6.

9. **`implementations.md` is a real existing top-level file.** Per the task's §23 instruction to
   only keep it if justified: current design already treats Implementations as a distinct object
   type with their own resolution/binding services (`procedure_implementation_bindings`,
   `procedure_dependencies`). Folding the *reference* into `NODE|...|implementation=I-82` (as the
   task suggests) while keeping full Implementation detail behind MCP (`inspect_implementation`,
   already read-only) satisfies the smallest-interface goal without losing anything — no service
   change required, just dropping `implementations.md` from the primary projection output.

## Preserve / Simplify / Migrate / Delete

**PRESERVE (no structural change — reuse as-is):**
- Canonical storage: `knowledge_nodes` (claims), `procedures`, `procedure_extractors`,
  `procedure_claim_refs`, `evidence`, `verification_results`, `execution_runs`,
  `execution_run_nodes`, `execution_run_events`, `episodes`, `observations`,
  `ingested_artifacts`, `artifact_blocks`.
- Services: `claim_extraction.py`, `skill_ingestion.py` + all 5 `ingestion_sources/` adapters,
  `procedure_extraction/` package (registry, strategies, schema), `supersede_procedure()`,
  `relevant_claims.py`, `verification.py`'s method ladder, `trace_collector.py`/`trace_worker.py`
  append-only mechanics, `access.py` tenant/visibility scoping, `SingleWriterLock`/
  `atomic_write_batch` atomicity primitives.
- MCP tool boundary and division of responsibility (semantic actions in MCP, no filesystem-read
  tools) — already matches the target design in §15.

**SIMPLIFY (same underlying data, new projection shape — this is most of the actual work):**
- `app/stealth/generator.py`: rewrite `claims.md`/`procedures.md`/`run.md` renderers to emit
  one-line-per-record formats (`C-xxx | STATUS | tags | statement | source=...`,
  `P-xxx | STATUS | tags | goal | step -> step -> step`,
  `NODE|N-xxx|...` / `VERIFY|N-xxx|V-xxx|...`) instead of the current `## HEADING` + indented
  `key: value` block format. Same source queries, different `format.py` template functions.
- Collapse `index/root.idx` + 4 per-type `.idx` files into one small `index.md` router
  (topic/tag → stable-ID lists + current-run summary), replacing line-range pointers into
  markdown pages with the fact that the pages are now themselves one-line-per-record (so a
  separate `.idx` line-range layer is no longer pulling its weight for claims/procedures/run —
  worth measuring in Phase 26 before fully deleting `.idx`, per the task's "don't build ahead of
  measurement" instruction).
- Drop `implementations.md` from the primary output; fold `implementation=I-xx` into `NODE|...`
  lines; keep Implementation detail reachable via existing `inspect_implementation` MCP tool.

**MIGRATE (schema additions, additive per the repo's migration discipline):**
- New migration: add a nullable node-scoping column to `verification_results`
  (`execution_run_node_id UUID REFERENCES execution_run_nodes(id)`, nullable so run-scoped
  criteria with no single owning node remain valid) so `VERIFY|N-xxx|V-xxx|...` can be a real
  per-node projection of real rows, not a re-derivation.
  **Decision (2026-09-14): yes, build this column.** Populate it from a source that already
  exists but isn't used for this today — `ProcedureStep.verification`
  (`procedure_extraction/schema.py`) is captured per step at extraction time but currently only
  `procedures.postconditions` feeds `derive_criteria()`. Derive additional criteria from each
  step's `verification` at plan-compile/node-creation time, tied to the `execution_run_nodes`
  row whose `node_order` matches that step's order. Procedure-wide `postconditions` stay
  `execution_run_node_id = NULL` and render as run-level `VERIFY|R-xx|...` lines rather than
  being forced onto one node. `execution_run_nodes.verification_state` (the existing scalar)
  is unchanged — it stays a fast rollup, computed the same way `evaluate_run_completion`
  already rolls up run-level criteria, just now also over the per-node ones.
- New migration: a `claim_relation_state` classification enum
  (SUPPORT/REFINE/SUPERSEDE/CONTRADICT/INVALIDATE/NEW_SCOPE/UNRELATED) surfaced either as a new
  narrow table or as an extension of `claim_relation_candidates`, wired to actually call
  `relate_claims`/mark dependent procedures stale via `procedure_claim_refs` STRONG_ROLES —
  this is new code, but it is an extension of existing tables, not a parallel system.
- New bridge function: project a bounded tail of `execution_run_events` (for the active run)
  into `.stealth/events.jsonl` at generation time — additive to `journal.py`, not a new event
  store.

**DELETE / DEPRECATE (only after callers migrate, per §22):**
- `context.md`/`run.json`/`meta.json` stay as an internal/legacy compatibility layer for now
  (tests and possibly other callers depend on them — needs a grep-for-callers pass in Phase 1
  before any removal), explicitly demoted from "the interface" to internal plumbing per §22/§29.
  Do not delete in this change; mark deprecated in the generator's docstring.
- `implementations.md` as a *top-level* file: remove from `generate_projection`'s default output
  once Phase 1's compatibility-caller grep confirms nothing outside tests reads it directly.
- Nothing at the DB/service layer is deleted — every finding above is additive.

## Why Phase 1 is safe to begin

Every "simplify" item above is a rendering change in `app/stealth/format.py`/`generator.py`
against data that is already correctly modeled and queried. Every "migrate" item is a nullable,
additive column or a new narrow table, consistent with the repo's frozen-migration/immutable-once-
applied discipline — nothing here requires touching `schema.md` or spec v4, and nothing here
deletes or renumbers an existing table or migration. The two real gaps (node-scoped verification
criteria, claim-update classification) are scoped precisely enough to write failing tests against
before implementation, per the task's Phase 1 instruction.
