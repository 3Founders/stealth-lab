# Phase 2 — Global Procedural Library ingestion compiler: closure report

**Status: closed.** Source brief `prompts.md`; implementation plan
`.scratch/phase2_ingestion_plan.md`. That plan's own subagent split was
A (schema + `supersede_procedure`) → B (source adapters) → C (the
compiler) → D (CLI + live proof + this report). Agents A–C's code landed
uncommitted, fully implemented, before the session that wrote it hung.
This report closes agent D's deliverable, once A–C were independently
verified against a real database rather than trusted on inspection alone.

## What was verified, and how

1. **Offline suite** (no `DATABASE_URL`): `test_supersede_procedure_offline.py`,
   `test_ingestion_sources_offline.py`, `test_skill_ingestion_offline.py` (extended)
   — **44 passed**, run together, zero failures.
2. **Migration 32** (`ingested_artifacts` / `ingestion_runs`) applied to the real
   local Postgres (`postgresql://chait:***@localhost:5432/postgres`) via
   `scripts/migrate.py`; additive, idempotent, applied clean.
3. **CLI built and run for real** (agent D's own deliverable, which never
   landed): `backend/scripts/ingest_skills.py` (`skill-dir` / `skill-repo` /
   `search` subcommands, thin wrapper over the already-tested compiler —
   no new business logic). Run live against a real vendored fixture:

   ```
   python scripts/ingest_skills.py skill-dir tests/fixtures/skills --domain coding
   {
     "run_id": "b36969c5-5c31-4cfb-b8e8-e4c8639fdbda",
     "metrics": {
       "sources_seen": 1, "artifacts_seen": 1, "candidates": 1,
       "accepted": 1, "duplicates": 0, "unchanged": 0, "stale": 0,
       "rejected": 0, "errors": 0
     }
   }
   ```

4. **Independent row verification** (brief's own §16 requirement — "do not
   call the ingestion successful merely because your own code printed
   PASS"), via a direct `SELECT` outside the ingestion code path:
   - `procedures` row: `provenance='prior_library'`, `embedding IS NOT NULL`,
     `verification_state='candidate'` (nothing born verified, ticket-13
     discipline honored), `domain_payload.source` carries the real
     `content_hash`/`source_type`/`uri`/`path` provenance the brief's §7
     asks for.
   - 5 real `edges` (`OWNS`, `procedures`→`task_nodes`) — one per the
     fixture's 5 numbered steps, confirming the procedure/task mapping
     (brief §4) actually fired, not just the procedure row.
   - a real `ingested_artifacts` row, `run_id` matching the CLI's own
     reported run.
5. **Live e2e test written and passing**: `backend/tests/test_skill_ingestion_e2e.py`
   (brief §16's full checklist — real fixture, real compiler, real
   `procedures`/`task_nodes`/`edges`/`ingested_artifacts` rows, real
   embedding, real retrieval via `find_applicable_procedures`, and a
   second run against byte-identical content proving re-ingestion is a
   real no-op, not a duplicate) — **1 passed**.
6. **Full backend offline suite**, run twice independently after every
   change above: **1528 passed, 133 skipped, 0 failed** (the 133 includes
   every `*_e2e.py` file's live-only tests, correctly skipped with no
   `DATABASE_URL`).

## Sources used

SKILL.md only (brief's own stated V1 scope — GitHub-repository source
adapter (`GitHubSkillSource`) exists and is offline-tested via mocked
HTTP, but was not exercised against a real network GitHub repo this
pass; `search`/`skill-repo` CLI subcommands exist and are ready).

## Procedures ingested / duplicates / rejected / stale / extraction failures

One real procedure ingested this pass (the vendored fixture used for
both the manual CLI run and the automated e2e test — the same content,
so the e2e test's own manifest counts are the authoritative numbers):
`candidates=1, accepted=1, duplicates=0, rejected=0, stale=0, errors=0`,
plus a confirmed `unchanged=1` on re-ingestion of byte-identical content.
No rejected or stale candidates were produced this pass because only one,
clean, well-formed fixture was ingested — the rejection/staleness/dedup
PATHS themselves are real and covered by the 44 offline tests (a
malformed SKILL.md, a changed-content-hash supersession, a near-duplicate
candidate), just not exercised live this pass with a second real corpus
document.

## Exact schema surfaces used

`procedures` (migration 18/19/20 — name/goal/steps/provenance/domain/
domain_payload/embedding/embedding_model_id/embedding_dim), `task_nodes`
(migration 01), `edges` (migration 01/18, `OWNS` procedures→task_nodes),
`ingested_artifacts`/`ingestion_runs` (migration 32, new this phase).
No `global_procedures`, no `ingested_tasks`, no parallel store — exactly
as the brief's §18 stop condition requires.

## Known limitations (honest, not glossed over)

- `GitHubSkillSource` untested against a real network repository this
  pass — offline-tested only (mocked HTTP for URL resolution).
- No real second-source ingestion run exercising the dedup/supersede/
  staleness paths against genuinely different real-world content — those
  paths are unit-proven (offline tests) but not yet demonstrated on a
  real corpus with real near-duplicates.
- `capability_statement` stayed `NULL` on the ingested procedure (visible
  in the verification query above) — the compiler's own documented
  behavior when no LLM `client` is configured for this run (honest
  ABSTAIN per brief §3/§6, not a bug); a run with a real client would
  populate it via the `GroundedHybridExtractor`-style abstraction pass.
- CLI's `search` subcommand is implemented and reuses
  `find_applicable_procedures` directly but was not separately live-run
  this pass beyond what the e2e test already exercises via the library
  call (not the CLI subprocess itself).
