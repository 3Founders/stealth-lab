# Board note — local architecture: `.stealth/` is the local truth; SQLite local store removed

**Lane:** CORE-A (ingestion + knowledge hardening, Plan A / G12–G13).
**Status:** ratified by the founder in-session (2026-09-10). Recorded here because
it is a **deliberate deviation from the frozen spec** (v4 §A11 / §A12 / §B35 and
`schema.md`), and CLAUDE.md hard-rule 3 says discrepancies become board notes.

## The decision

The local side of StealthLab is a **filesystem-native working set plus a durable
runtime journal**. There is **no local SQLite and no local Postgres**. Global
Postgres + pgvector remains authoritative for semantic retrieval, the Claim
graph, ranking, permissions, and publication.

> Cloud decides *what* knowledge is relevant; the local filesystem makes that
> knowledge cheap for (small) agents to navigate and execute.

Mental model:

```
global Postgres        ≈ disk / long-term memory
.stealth/ working set  ≈ page cache
model context window   ≈ CPU cache
```

Navigation is a **knowledge page fault**: `grep index/*.idx` → object id + exact
line range → `sed -n 'start,end p' <file>.md` → continue. On a local miss the
agent calls the Stealth MCP, which projects the needed objects from global
Postgres into `.stealth/`, regenerates the affected index, and the agent greps
again. A small model never reasons over 50k tokens of project memory — it greps,
picks among a handful of index rows, reads ~25 lines, follows a Procedure,
verifies output.

### `.stealth/` layout

```
.stealth/
  context.md              compact B35 router (kept — B35 readers + back-compat)
  run.json                machine-readable current run (kept)
  meta.json               projection_revision (journal seq) + per-type revisions + change_cursor
  claims.md  procedures.md  implementations.md  run.md  exploration.md
  events.jsonl             append-only local journal, monotonic seq (the WAL / single-writer log)
  index/
    root.idx              name|target|hint            (router, byte-budgeted)
    claims.idx  procedures.idx  implementations.idx    id|version|scope|status|tags|file|start|end|summary
    run.idx              node|status|owner|deps|globs|file|start|end|summary
    exploration.idx
```

Index line ranges are disposable optimisation metadata — regenerated every run,
never hand-edited. Stable ids/anchors are authoritative. The local daemon (the
MCP server, already `--workers 1` on `127.0.0.1`) is the **only writer**; agents
read `.stealth/` directly (`grep`/`sed`) and mutate only through the daemon,
which appends the journal, updates state, regenerates `*.idx` + `*.md`, and
atomic-renames.

## What deviates from the frozen spec

| Frozen spec (v4 §A11/§A12/§B35, `schema.md`) | This decision |
|---|---|
| `.stealth/` is a **disposable projection**, "not another knowledge store"; prefers a single `context.md`; explicitly says *don't* maintain `claims.md`/`procedures.md`/… mirrors | `.stealth/` is the **authoritative local representation** an agent acts on (global stays long-term truth; `.stealth/` is a trusted page cache with `meta.json` staleness detection + page-fault refill). Per-type addressable `.md` pages + `.idx` routers are the primary shape; `context.md` is kept as the compact router. |
| "reuse existing local SQLite/structured store if present"; local SQLite is "a local registry/cache" | The SQLite local store (`app/local_agent/local_store.py`, `local_claims.py`, `.stealthlab/*.db`) is **removed entirely**, along with `local_learning_sweep.py`, the bootstrap importers, `unified_retrieval.py`, `publish_local_procedure`, and the reduced local schema. |
| A13 private sync tier (local ↔ cloud USER_PRIVATE) | **Deferred (G12)** — no storage for it yet. Without the sync tier the local SQLite store was a dead-end island (learn locally → stays on one disk forever, or manually publish to *public*), which is the main reason it is removed rather than kept. |

## Rationale

- One mechanism, not two. Today: `.stealth/*.md` projection **+** `.stealthlab/*.db`
  SQLite **+** `unified_retrieval.py` fusing local-SQLite with global-cloud. That is
  real drift surface (the local schema is a reduced copy that must keep chasing the
  global column vocabulary).
- Transparency: `git diff` shows what an agent learned; PRs review it; a human can
  curate it. A SQLite blob needs tooling to inspect.
- Trace-derived private learning does **not** disappear — `app/services/ingestion_jobs.py`
  already writes trace-derived candidates to the **global** DB as `visibility='private'`
  + `owner_id` rows (`resolve_trace_ingestion_context` → observation → claim →
  procedure). That becomes the single trace-learning path.
- What is genuinely lost: offline operation (no substrate when disconnected) and
  the "store file = privacy boundary, exactly one viewer by construction" property
  (privacy is now enforced by `scope_predicates()` + RLS on global private rows,
  not a separate disk). Accepted.

## Cost / one-way-door notes for the board

- `app/local_agent/runner.py` (the **local execution provider**, Plan B, must stay)
  imported `local_learning` / `local_store` / `unified_retrieval` — removal required
  a careful decouple of `runner.py` (no-op the local-candidate persist → global
  private path; global-only retrieval). This is a cross-lane edit into Plan B's
  execution provider; `runner.py`'s public API is kept signature-stable.
- `app/services/local_retrieval.py` is a **different module** (code-structure
  retrieval used by `server.py` / `slot_binders.py`) and is **not** removed.
- `schema.md` is frozen; it still shows the local reduced schema. This note is the
  record; no `schema.md` edit.

## Delivery

- **P1** (`a7c53f1`, on `main`) — `app/stealth/` package: `.idx`/`.md` format +
  codecs, `atomic_write_batch`, run-scoped generator producing the per-type pages
  + `index/*.idx`, byte-budgeted root router. `app/execution/stealth_projection.py`
  is now a shim. 21 offline + 6 local-DB tests.
- **P2–P4** (`61f278d`, on `main`) — `app/stealth/journal.py` (`events.jsonl`,
  monotonic `seq`, `SingleWriterLock`) + `scripts/stealth.py` read-CLI;
  `app/stealth/faults.py::project_knowledge` + MCP tool (global-only resolve,
  additive merge, `not_found` never fabricated); `app/stealth/exploration.py` +
  node owners/file-intents in `run.*` from `execution_run_nodes` (mig 56).
  40 stealth tests pass.
- **P5** (`4ac0d0b`, on `main`) — 12 `app/local_agent/` modules + 2 CLIs + 19
  test files deleted; `runner.py` decoupled (global-only retrieval via
  `search_procedures`, kept signature-stable); `publish_local_procedure` gone,
  `publication.py::publish_procedure` is the sole Local→Global path;
  `local_learning_sweep` tick dropped. Combined offline suite 2679 pass / 5
  fail (⊆ baseline; the 2 dropped were the local-runner embedder-seam tests).
- **P6** — this note.

### Follow-up owed

- A global-only "second user reuses a published procedure" e2e, replacing the
  deleted local-lifecycle `test_second_user_global_reuse_e2e` /
  `test_ideal_v1_lifecycle_e2e`.

See `STEALTHLAB_INGESTION_HARDENING_AUDIT.md` § "LOCAL WORKING-SET ARCHITECTURE"
for the running detail.
