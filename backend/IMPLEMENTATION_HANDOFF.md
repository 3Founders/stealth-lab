# Implementation handoff: memory-substrate build, steps 1-2 done

This is the CODE-level handoff. For the PLANNING-level one (why every
decision below was made), see `handoff.md` and `.scratch/memory-substrate/`
on the `research/claude-code-hooks` branch — that's where the map, all 18
resolved tickets, and the inventory live. This doc doesn't repeat that
reasoning; it tells you what's actually built, tested, and true right now
on `main`, so you can start step 3 without re-deriving anything.

**Read this first, then go straight to "Where things stand" below.**

## The real implementation order (from `handoff.md`)

1. ~~Migration ledger + CI schema/code drift check (ticket 17)~~ — **done**
2. ~~`trace_events` + trace-header + collector/job pipeline (tickets 06, 16, 18)~~ — **done**
3. **Episode assembly (ticket 11)** — next, and it's a **prototype step**:
   build a throwaway segmenter over real Claude Code session transcripts
   and react to the output before committing to boundaries. Blocked on
   real data — see "The one real blocker" below.
4. Observations (ticket 04), then claims wiring (tickets 03, 10)
5. `procedures` + HTN relocation (tickets 05, 15)
6. Applicability + lifecycle (tickets 12, 13), then retrieval integration (ticket 14)

## Where things stand

Everything below is verified against a real, live Postgres — not just
written and assumed to work. Full regression: **631 tests passed, 2
skipped (pre-existing, unrelated), 0 failures.**

### Step 1 — migration ledger (ticket 17)

- **`backend/scripts/migrate.py`** — the single, real entry point.
  Replaces the old `for f in db/0*.sql` loop, which silently skipped
  `10_code_sourced_agents.sql` (that glob only matches filenames starting
  with the digit `0`). Real ledger table (`schema_migrations`: filename,
  checksum, applied_at, kind), real tamper detection (checksum mismatch on
  an already-applied file → refuses to silently re-run, exit 1).
  ```
  DATABASE_URL=... python3 scripts/migrate.py           # apply pending
  DATABASE_URL=... python3 scripts/migrate.py --status   # see ledger state
  DATABASE_URL=... python3 scripts/migrate.py --dry-run  # preview only
  ```
  Verified: fresh-DB bootstrap (correct order including file 10), true
  idempotent no-op on re-run, tamper detection catches a real edited file,
  safe re-run against an already-provisioned DB.

- **Two real, live schema/code drifts closed:**
  - `app/services/retrieval.py`: `VALID_EMBEDDING_COLUMNS` is now a real,
    named, exported constant (was an inline tuple literal, duplicated
    nowhere — the CI test imports this exact constant, not a re-hardcoded
    copy). `embedding_joint` itself was a real ghost column — accepted by
    code, created by no DDL file. Closed by
    `backend/db/11_fix_embedding_joint_drift.sql` (adds the column +
    matching HNSW index to `task_nodes`).
  - `app/models/ontology.py`: `ProvenanceSource` now includes
    `"public_generated"` (was missing; the DB enum already had it and
    `knowledge_update.py`'s `apply_generated()` writes it on every call —
    hydrating those rows via `from_row()` was raising before this fix).

- **`backend/tests/test_schema_drift.py`** — the real CI check, two tests,
  both comparing code constants against the **live database** (not the
  DDL files — a hand-added column would pass a file-only check).
  Skips (not fails) with no `DATABASE_URL` set. Both currently pass.

- Seed data moved: `backend/db/09_seed_internal_agents.sql` →
- Seed data stays in the normal numbered sequence: `backend/db/09_seed_internal_agents.sql`.
  Ticket 17's own answer suggested splitting seed data into a separate `db/seeds/` folder
  (seed INSERTs mixed into a DDL sequence is a real smell) — tried, then reverted on request:
  the distinction is organizational, not load-bearing, and the file works identically tracked
  like every other migration. `kind='schema'`/`'seed'` stays in the ledger table in case this
  is revisited later.

- Three real, live docs fixed (all had the exact same broken glob):
  `backend/README.md`, root `README.md`, `experiments/swebench_pro/RUNBOOK.md`.

### Step 2 — trace ingestion pipeline (tickets 06, 16, 18)

- **`backend/db/12_trace_ingestion_pipeline.sql`** — new tables:
  - `agent_traces` — the trace-header table. **Name is provisional**,
    ticket 06 deliberately left it unnamed; nothing downstream depends on
    this exact string. One row per causally-connected execution (one
    turn / one subagent run).
  - `trace_events` — atomic events. `event_type` is free `TEXT`, not a
    CHECK-constrained enum on purpose — a hard 3-value CHECK is exactly
    why the *existing* `traces` table couldn't hold Claude Code's ~31
    real event types (ticket 06's own reasoning). `dedup_key` is
    `UNIQUE`, real idempotency mechanism.
  - `ingestion_jobs` — the durability/compilation queue,
    `SELECT ... FOR UPDATE SKIP LOCKED`-ready for a future multi-worker
    deployment even though milestone 1 runs one in-process worker.
  - Two `episodes` fixes: added `t_invalid` (bi-temporal column, didn't
    exist before), and `episode_links`' FK changed from `ON DELETE
    CASCADE` to no action — **verified directly**: a hard `DELETE` on a
    linked episode now genuinely fails with a real constraint violation;
    tombstoning (`UPDATE episodes SET t_invalid = now()`) works fine and
    is unaffected.
  - The existing `traces` table is untouched, per ticket 06's own answer
    — it keeps serving `triggers.py` and `eval/layer2.py` exactly as
    before.

- **`backend/app/services/trace_redaction.py`** — pure functions,
  operates on parsed JSON structure, never raw text (a raw-text regex
  substitution risks producing invalid JSON; walking parsed leaves
  structurally cannot). Three layers: known-token patterns (AWS/GitHub/
  Slack/Stripe/OpenAI/Anthropic key shapes, generic bearer tokens,
  PEM private key blocks), path-based exclusion (`.env`, `*.pem`,
  `id_rsa`, `.ssh/`, etc. — wholesale-excludes both `tool_input` and
  `tool_output` when the input path is sensitive, not just the path
  string itself), entropy heuristics deliberately **not** implemented yet
  (ticket 18 flagged these as higher-false-positive; narrow-structural-
  version-first, same discipline as ticket 17's own scope cut).
  `NEVER_SEND_EXTERNALLY_BY_DEFAULT = True` is a real constant, not a
  comment — any future external-LLM integration must check it explicitly;
  this module cannot and does not enforce that on its own.

  **Two real bugs found and fixed while testing** (both in
  `tests/test_trace_redaction.py`'s history, worth reading if extending
  this): the first version checked each string leaf independently for
  "does this look like a path," which correctly redacted `file_path` but
  left the actual file *contents* in `tool_output` completely untouched.
  Fixed to check `tool_input` first, then exclude `tool_output` wholesale
  — a cross-field decision a per-leaf check structurally cannot make.

- **`backend/app/services/trace_collector.py`** — dedup key
  (`session_id` + event type + sequence + payload hash, since hooks carry
  no native event ID per ticket 07's research), bounded local append file
  (default 50k lines, drops oldest 20% in a batch when exceeded, not one
  line at a time), a real, visible drop counter stored as the file's own
  first line (not a log message that can be missed).

- **`backend/app/services/trace_worker.py`** — reads the collector file,
  ensures a trace header exists (creates one on first sight of a new
  `trace_id`), inserts events via `INSERT ... ON CONFLICT (dedup_key) DO
  NOTHING RETURNING id`, creates one `ingestion_jobs` row per real
  (non-duplicate) insert. Idempotent by re-processing the whole file every
  time, not by tracking a cursor — simpler, and a hand-maintained offset
  file is one more thing that can drift or corrupt.

  **Real end-to-end test** (`tests/test_trace_ingestion_e2e.py`) runs the
  full collector → worker → live-database path: confirms the actual
  stored `tool_output` row never contains a raw secret, confirms
  idempotent re-processing produces `inserted=0, skipped_duplicate=1` on
  the second pass with real counts, confirms multiple events in one
  session share exactly one trace header. **One real test-hygiene bug
  found**: my own cleanup helper only cleared 2 of 3 tables, so
  `ingestion_jobs` rows accumulated across repeated runs (confirmed: 20
  real leftover rows found in the test DB). Fixed, then proved fixed by
  running the same test twice consecutively.

## Honest, deliberate gaps — not oversights

- **No real Claude Code hook wiring exists.** The collector's logic
  (redact → key → append) is real and tested; actually invoking it from
  a `~/.claude/settings.json` hook is untested, because I have no real
  Claude Code process available to test against. Whoever does this next
  should treat that wiring as new, unverified work, not an extension of
  something already proven.
- **Entropy-based secret detection is not implemented.** Ticket 18's own
  reasoning: higher false-positive rate, ship the narrow version first.
- **The trace-header table's name (`agent_traces`) is provisional.**
  Rename freely — nothing downstream depends on the specific string yet.
- **`event_type` and `job_type` are free TEXT, not enums**, deliberately
  — ticket 06's reasoning about why the old `traces.action_type` CHECK
  couldn't hold Claude Code's real event vocabulary applies here too.

## Running any of this yourself

```bash
cd backend
export DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/DBNAME

# apply everything, in order, including the two new migrations
python3 scripts/migrate.py

# full suite, including the live-DB tests (they skip cleanly without DATABASE_URL)
python3 -m pytest tests/ -q

# just the new stuff
python3 -m pytest tests/test_schema_drift.py tests/test_trace_redaction.py \
    tests/test_trace_collector.py tests/test_trace_ingestion_e2e.py -v
```

## Real gotcha, worth knowing before you start step 3

Ticket 11 (episode assembly) is explicitly a **prototype** ticket — it
needs real Claude Code session transcripts to react to, not more
architectural reasoning. `~/.claude/projects/<project>/<session-id>.jsonl`
is where they live (per ticket 07's research), and their per-line schema
is confirmed **undocumented and unstable across releases** — don't build
a segmenter against assumed field names without actually looking at a
real file first. A schema-sniffing script for exactly this exists
already if it wasn't already used — ask whoever has it before rebuilding
one.
