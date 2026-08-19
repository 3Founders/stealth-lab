# Implementation handoff: memory-substrate build, steps 1-3 done

> **Update (Aug 19, commit `14d8649`).** Step 3 is now done — the episode-assembly
> prototype ran over 36 real sessions and **falsified two of ticket 11's inherited
> assumptions**; see "Step 3" below. A code review of your steps 1-2 also found
> defects that need fixing **before hook wiring** — see "Review of steps 1-2".
> Nothing in your work is being reverted; the sequencing changed because no hooks
> are configured anywhere, so the collector isn't receiving data yet.

This is the CODE-level handoff. For the PLANNING-level one (why every
decision below was made), see `handoff.md` and `.scratch/memory-substrate/`
on the `research/claude-code-hooks` branch — that's where the map, all 18
resolved tickets, and the inventory live. This doc doesn't repeat that
reasoning; it tells you what's actually built, tested, and true right now,
so you can pick up without re-deriving anything.

**Read the update box above, then "The real implementation order". If you are
starting fresh: "Where things stand" is the state of steps 1-2; "Step 3" and
"Review of steps 1-2" are what changed on Aug 19.**

## The real implementation order (revised Aug 19)

1. ~~Migration ledger + CI schema/code drift check (ticket 17)~~ — **done**
2. ~~`trace_events` + trace-header + collector/job pipeline (tickets 06, 16, 18)~~ — **done**
   (but see "Review of steps 1-2" — fixes needed before hook wiring)
3. ~~Episode assembly prototype (ticket 11)~~ — **done**, `experiments/episode_assembly/`
4. **Migration 13** — `project_id` + episode columns. Next, and small. See below.
5. **Collector/worker fixes (A1-A7 below)** — required *before* any hook wiring.
6. Observations (ticket 04), then claims wiring (tickets 03, 10)
7. `procedures` + HTN relocation (tickets 05, 15)
8. Applicability + lifecycle (tickets 12, 13), then retrieval integration (ticket 14)

**Why the collector fixes moved after step 3 rather than before it:** neither
`~/.claude/settings.json` nor the project's `.claude/settings.local.json` has a
`hooks` key — **no hooks are configured anywhere on this machine.** The collector
is receiving nothing, so its data-loss window isn't losing data today. The fixes
are prerequisites for hook wiring, not live incidents. (The 9
`hook_additional_context` attachments visible in transcripts come from
plugin/skill hooks, not from our settings.)

**"The one real blocker" at the bottom of this doc is resolved** — there was no
blocker. 36 real StealthLab transcripts (107 MB, 28,969 lines) plus 69 subagent
transcripts were sitting in `~/.claude/projects/` the whole time.

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


## Step 3 — episode assembly prototype (ticket 11), done

`experiments/episode_assembly/` — two scripts + `FINDINGS.md`. Generated reports
are gitignored (reproducible from the scripts, and subagent filenames can encode
prompt-derived labels). Corpus is **this project's sessions only**, per the repo
owner's explicit decision — other project dirs hold unrelated private work.

```bash
cd experiments/episode_assembly
python3 sniff_schema.py --json schema_report.json     # what's actually in the files
python3 segment.py --json segmentation_report.json    # the three rule sets
```

**Read `FINDINGS.md` before building anything on episodes.** Two of ticket 11's
inherited assumptions were falsified by real data:

1. **The idle-gap bimodality does not exist.** We implemented exactly what the
   ticket prescribed — 2-component GMM on log-scaled inter-event times, threshold
   at the valley, expecting ~1h. Raw gaps fit 0.6s/3.7s (6.4× separation);
   human prompt-to-prompt gaps fit 136s/579s (4.2×). Neither is bimodal; both
   fitted valleys land ~3 orders of magnitude below expectation. The
   web-analytics source measures *human-paced* clicks; agent transcripts are
   *machine-paced* within a task (p50 gap 2.3s) and human idleness is a **long
   tail** (p99 903s, max 147h), not a second mode. **Recommendation: drop idle
   as a boundary signal rather than tune it.** If a temporal rule is ever wanted,
   use a fixed percentile and label it arbitrary — don't dress it up as fitted.
2. **Commit/test boundaries are ~97% disjoint from prompt boundaries**
   (Jaccard 0.028) and give a median of **1 episode per session** — unusable as a
   top-level cut. This validates the ticket's `prompt > subagent > commit/test >
   idle` precedence from the opposite direction than expected: commit/test is
   *metadata or a sub-boundary*, never a boundary rule.

Measured: rule A (prompt) 823 episodes, B (prompt+subagent) 888, C (commit/test)
332. Most actionable pathology: **147 trivial episodes of ≤2 events — 18% of all
rule-A episodes.** Prompt-only over-segments and needs a merge rule; the mirror
case (22 prompts spawning >200 events) says it also under-segments. Those two
merge/subdivide rules are the real remaining design work, and neither is a
boundary-signal question.

### Schema facts that would otherwise have been silent bugs

Your doc's warning to look at a real file first was right, and stronger than you
knew. A single-file sample shows 8 line types; **the full corpus has 16**:

- **9 of the 16 carry no `timestamp`** — `last-prompt`, `mode`, `ai-title`,
  `file-history-snapshot`, `permission-mode`, `agent-name`, `agent-setting`,
  `relocated`, `worktree-state`, `agent-color`. Any temporal logic must impute
  or skip.
- **Schema drift is intra-file**: 11 CLI versions across the corpus, several
  within single transcripts. Ticket 07 said "unstable across releases"; it's
  unstable *within one file*. Parse defensively per line.
- **A session is a forest, not a chain** — 46 `parentUuid: null` roots across 36
  files. Compaction and resume create new roots; a single-chain walk truncates
  silently.
- **Subagent work is in sibling files**, `<session>/subagents/agent-<hex>.jsonl`,
  joined by `sourceToolAssistantUUID` → the parent assistant line's `uuid`.
  `isSidechain` is always false in main files. Ticket 11 treats subagent
  start/end as an in-session signal; it needs a cross-file join.
- **`tool_use` ≠ `tool_result`** — 6,077 vs 6,026. Interrupted/denied calls leave
  unmatched pairs.
- `cwd` and `gitBranch` are on **every** conversational line — directly usable
  for `project_id` (see migration 13 below).

### Prior art — don't rebuild the prompt predicate

`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/session-report/skills/session-report/analyze-sessions.mjs`
(875 lines) already solves "is this line a genuine human prompt," and it's
subtler than it looks. Beyond `isMeta`/`isCompactSummary`/`isSidechain`/
`tool_result`, it also drops lines starting `<task-notification`,
`<scheduled-wakeup`, `<background-task`, `[Request interrupted`.

**Those four prefixes matter enormously.** Background-agent notifications arrive
as `type:"user"` lines — without that filter, every one opens a spurious episode,
and our sessions are full of them. `segment.py` adopts the predicate verbatim.

## Migration 13 — next, and small

Two things land together (do **not** edit migration 12 — `scripts/migrate.py`
checksums it, so editing an applied file is now a hard error by your own design):

1. **`project_id`** — ticket 09 (`09-isolation-and-auth.md:84-90`) explicitly
   deferred this column "until ticket 06's actual trace/episode schema is being
   built." That schema is migration 12, and `project_id` didn't land. Repo owner's
   call: **add it now** — episode assembly is exactly the consumer that groups by
   repo/workspace, and retrofitting after trace data accumulates is far worse.
   `cwd` + `gitBranch` on every transcript line make the value concrete.
2. **`episodes` columns** — it currently has no `parent_episode_id` (ticket 11
   requires it for hierarchical nesting), no `session_id`, no start/end
   timestamps, and **no `owner_id`/`visibility`** (`03_access.sql` never covered
   it; ticket 09 flags this explicitly).

## Review of steps 1-2 — fix before hook wiring

To be clear about what's good, because most of it is: the dedup-key design
(computed once at collect time, persisted, never recomputed) is right;
`ON CONFLICT … RETURNING id` gives a real rather than assumed duplicate signal;
event+job sharing one transaction is correct; redaction walking parsed JSON
instead of raw-text regex is the right call, and the cross-field
`tool_input`→`tool_output` exclusion is a genuinely subtle fix that a per-leaf
check structurally cannot make. Migration 11 correctly closes the
`embedding_joint` ghost column.

The following are real and confirmed by reading source, ordered by severity.

**A1 — `append_event` is not an append.** `trace_collector.py:96` is
`file_path.write_text(...)`, preceded by `read_text()` of the whole file at `:81`.
Three consequences, on the one operation `trace_worker.py:4-7` calls "the durable
step… the one irreversible step":
- `write_text` opens `"w"` — it **truncates first**. A process killed mid-write
  loses *the entire file*, every prior event plus the drop counter.
- **O(file) per event**: at the 50k-line default with ~1KB records that's ~50MB
  read + 50MB written per event, on a hook hot path the docstring calls "fast,
  non-blocking." The comment at `:26-29` justifies batched trimming as avoiding
  per-append O(n) — but O(n) is paid every append regardless, so that rationale
  doesn't describe the code.
- **No locking.** Claude Code fires hooks concurrently for parallel tool calls;
  two concurrent calls interleave last-writer-wins.

*Fix:* real append (`open(path,"a")`, one `write()`, `flush()`+`os.fsync()`) under
an advisory lock (`msvcrt.locking` on Windows — that's the path that must work
here — / `fcntl.flock` on POSIX). Move `drop_count` to a sidecar
`<name>.meta.json`; a counter in line 1 can't coexist with O(1) appends. Trimming
becomes periodic compaction under the same lock.

**A2 — trimming discards events the worker never saw.** `:88-93` drops the oldest
20% with no knowledge of worker progress. If the worker is down during one 50k
burst, those events are gone. `drop_count` increments and **nothing ever reads
it** (`read_drop_count` has zero non-test callers), so the loss is invisible in
the DB. *Fix:* worker writes a high-water mark to the sidecar; compaction won't
trim past it; surface `drop_count` into the database.

**A3 — worker is O(file) per run with no rotation.** `trace_worker.py:91` does
`pool.acquire()` **inside** the per-record loop, and `_ensure_trace_header` is
called once per event (`:100`) though there's one header per session. A 50k file
= 50k acquisitions, 100k-150k sequential round trips — ~50-75s local, 8-12 min
remote, *every run*, since nothing truncates. *Fix:* one connection per run,
hoist the header call per distinct `trace_id`, `executemany` in chunks.

**A4 — one malformed line permanently stalls the pipeline.** `_read_records`
(`:35`) has no `try/except` around `json.loads`; `datetime.fromisoformat` at
`:52`/`:96` is likewise unguarded (and pre-3.11 rejects trailing `Z`, which
JS-origin payloads emit). One truncated line — exactly what A1's torn write
produces — aborts the run, processes zero records, and is a permanent poison
pill. *Fix:* per-line try/except, route bad lines to `<name>.quarantine`, continue.

**A5 — redaction doesn't work on Windows paths.** `_is_sensitive_path`
(`trace_redaction.py:79`) uses `PurePosixPath` and the comment at `:77-78` claims
it "normalizes separators." It does not — `PurePosixPath` treats `\` as an
ordinary character. `C:\Users\chait\.ssh\id_rsa` matches **no** rule (rule 6 needs
`.ssh/`, rule 3 needs `id_rsa` after `/` or start). **On this machine's own
platform, SSH keys are not excluded.** No test uses a backslash path. Also:
`Bash`-shaped `tool_input` bypasses path rules inconsistently (`cat .env` misses
rule 1 — `.env` preceded by a space; `cat ~/.aws/credentials` hits end-anchored
rule 7), and 5 of 8 token patterns have zero tests.

**A6 — the `tool_response` question, highest-consequence unknown.** `redact_event`
keys off `"tool_input"`/`"tool_output"` (`:143,150-158`). Claude Code's actual
`PostToolUse` field is **`tool_response`**. If a wrapper forwards the raw payload,
tool output is written **entirely unredacted**. Can't be settled from our code —
`trace_collector.py:14` points at `scripts/example_hook_wrapper.py`, which
**doesn't exist**, nor do the `scripts/collector.py` / `scripts/trace_worker.py`
referenced in `12_trace_ingestion_pipeline.sql:2-3`. *Fix:* confirm against
`.scratch/memory-substrate/research/claude-code-hook-schema.md`, then key off a
declared field list covering both names, with a redact-by-default fallback for
unknown large-string keys.

**A7 — `visibility`/`owner_id` are decorative.** Both columns are correctly
present on `agent_traces` and `trace_events` (`12_…sql:69-70,113-114`) ✓ — but
`trace_worker.py` never writes either (grep: zero matches), so every row is
`public`/`NULL` and nothing filters on them. `03_access.sql:6-14` names exactly
this as the cautionary case: `tenant_id` existed everywhere and *no query filtered
by it*. *Fix:* worker populates both; add a test asserting a non-default
`owner_id` survives ingestion. (`ingestion_jobs` having neither is fine — it's
infrastructure, not content.)

**Smaller, worth knowing:**
- **`trace_id` is always `session_id`.** `:64`/`:92` do
  `record.get("trace_id") or record["session_id"]`, and the collector record
  (`trace_collector.py:68-74`) has no `trace_id` key at all — so the fallback
  fires 100% of the time and `agent_traces` is one row per *session*, not per
  causally-connected execution. 11 of its 19 columns have no writer.
- **dedup_key isn't stable across redaction-rule changes** — it hashes the
  *redacted* payload including the injected `_redaction` key (`:67`). Adding one
  pattern changes every future key for the same logical event.
- **`sequence` collisions are unguarded** — a wrapper restarting numbering at 0
  per process silently collides and counts as `skipped_duplicate`.
- **Migration 12 couples unrelated changes** — ticket-18 `episodes`/`episode_links`
  work *and* the three trace tables in one transaction. An orphan-row failure on
  the `episode_links` constraint rolls back the file and **no trace tables get
  created**.
- **`_redaction` metadata and `drop_count` both dead-end** — computed, stored in
  the file, never read by the worker, no column to land in.
- **Worker has zero DB-free tests.** All of `test_trace_ingestion_e2e.py` skips
  without `DATABASE_URL`, so in CI without a database the worker's coverage is
  exactly zero and the suite still reports green.

One meta-note, offered usefully: the prose in these modules is unusually
assertive about its own rigor ("real, not assumed", "REAL BUG FOUND AND FIXED").
Much of it checks out — the `01_ontology.sql:127` citation is accurate, the
cross-field redaction fix is real and tested. But several claims don't match the
code: the "append" is a rewrite, the O(n) justification describes the wrong cost,
`PurePosixPath` doesn't normalize separators, the pattern list isn't ordered by
specificity, and three referenced files don't exist. Worth treating the comments
as intent rather than verification.

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

## Resolved: the step-3 "blocker"

This section previously warned that ticket 11 was blocked on real Claude Code
transcripts. **It wasn't** — 36 StealthLab sessions (107 MB, 28,969 lines) plus 69
subagent transcripts were already at
`~/.claude/projects/c--Users-chait-Prog-3Found-Stealth-StealthLab/`.

The warning's substance was right, though, and worth keeping: the per-line schema
really is undocumented and unstable, so `sniff_schema.py` exists precisely to look
before assuming. It found 16 line types where a single-file sample shows 8 — see
"Step 3" above.

The schema-sniffing script you asked about didn't exist; it does now
(`experiments/episode_assembly/sniff_schema.py`). Three *other* transcript parsers
do exist in installed plugins — `session-report/analyze-sessions.mjs` is the useful
one, and its human-prompt predicate is reused rather than reinvented.
