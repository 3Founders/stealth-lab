# Handoff: Memory Substrate map — planning complete, implementation open

Branch `research/claude-code-hooks`. Map at `.scratch/memory-substrate/`.

**Status: 18 of 18 tickets resolved. The map is complete.** Its Destination — "representation,
schema, migration strategy and implementation order settled well enough that implementation can
begin with no major unknowns" — is reached. Wayfinder's job is done; what remains is building.

## Start here

1. **`.scratch/memory-substrate/map.md`** — Destination, Notes, and 18 one-line decision summaries
   with links. Read this first; it is the index.
2. **`.scratch/memory-substrate/inventory.md`** — what the codebase actually contains today, with
   `file:line` citations. Every ticket cites it.
3. **The ticket files** — `.scratch/memory-substrate/issues/NN-*.md`. Each has the question, the
   `## Answer`, and (for the last six) a `## Research findings` section.
4. **`.scratch/memory-substrate/research/`** — four literature briefs (`answers1-4.md`) plus the
   per-decision triage in `research-prompts-open-questions.md`.

## What the map decided, in one page

**New tables**: `procedures` (05), `observations` + `observation_events` (04), `trace_events` +
a trace-header table (06). **Reused as-is**: `knowledge_nodes` for claims (03), `episodes` (06),
`traces` untouched (06), the whole retrieval stack (14), Agent Store lifecycle tables (01).

**Load-bearing cross-ticket decisions** — breaking any one of these breaks others:

- **State is not stored.** `state_before`/`state_after` are read-time projections over the claim
  graph; `state_delta` is a set difference (10). Ticket 12's `S_current` is the same query.
- **Hard constraints filter; soft signals fuse.** Applicability uses a non-compensatory cascade
  (12); retrieval unions and fuses (14). These look contradictory and are not — do not unify them.
- **Procedure ≠ execution binding.** A stored HTN plan is a *binding of* a procedure, never the
  procedure (05, 15).
- **Closed-world assumption.** Absence is determinate, so unknown preconditions fail closed for
  free (10, 12). No NULL sentinels anywhere.
- **Cold start disables reuse rather than weakening gates.** The planner is the *default* early
  and becomes the fallback once procedures verify (12, 15).

## Before you write code

Three things the map flags that are easy to get wrong:

- **Every new table carries both `owner_id` and `visibility`** — not one (09). `owner_id` alone
  produces broken SQL in `access.py::visibility_predicate()`.
- **Property-based tests for RRF invariants land before `retrieval.py` is extended** (14) — it is
  load-bearing with zero coverage today.
- **Borrowed constants go in config, not in source** — the 8k token budget, the ≥10-success
  verification bar, circuit-breaker thresholds, and the HTN budget constants are all transferred
  from other domains and unvalidated here.

## Known defects to fix along the way (from ticket 01, confirmed)

- `embedding_joint` is read/written by code but created by no DDL file.
- `ProvenanceSource` in `models/ontology.py` omits `public_generated`, which the DB has and
  `knowledge_update.py` writes — hydrating those rows raises.
- `main.py:27` creates a pool via `create_pool()`; `close_pool()` closes the module global, so the
  live pool is never closed.
- Documented setup applies migrations `01`–`05` only; `06`–`10` are undocumented.

## Recommended implementation order (from the map's own dependencies)

1. Migration ledger + the CI schema/code drift check (17) — it catches the defects above.
2. `trace_events` + trace-header + the collector/job pipeline (06, 16, 18).
3. Episode assembly (11) — this one is a **prototype**: build a throwaway segmenter over real
   transcripts and react to the output before committing to boundaries.
4. Observations (04), then claims wiring (03, 10) — `capture_claim()` needs its `embedding` fix.
5. `procedures` + the HTN relocation (05, 15).
6. Applicability + lifecycle (12, 13), then retrieval integration (14).

## If something is genuinely unspecified

Check the map's **Not yet specified** section — 23 items are deliberately deferred there with the
reason. If it is in that list, it was a decision to defer, not an oversight. If it is not in that
list and not in a ticket, that is a real gap: raise it rather than guessing.
