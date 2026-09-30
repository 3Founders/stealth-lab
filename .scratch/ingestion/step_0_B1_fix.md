# Step 0, blocker B1 — the paid extraction path now produces knowledge

Date: 2026-09-29
Fixes: **B1** (`step_0_SUMMARY.md` §5) and the client-shape trap it names as B2.
Shard: **local only** — container `sl_step4_pg`, port **55441**, fresh database
`sl_step0b` (created for this run, migrations applied through
`126_ingestion_context_license.sql`). `run_step0_pilot.py` forces
`DATABASE_URL` / `DATABASE_URL_DIRECT` / `CONTROL_DATABASE_URL` /
`SEARCH_DATABASE_URL` at loopback before importing the app, and the CLI
independently refuses any non-loopback DSN. No `kel_*` database and no port
55432 was contacted. `backend/.env` still points those four at Neon; nothing
wrote there.

---

## 1. Root cause — three independent defects, not one

The summary attributed the zero yield to the prompt/schema drift alone. Reading
the code, the paid path had **three** separate ways to produce nothing, and only
one of them had been fixed before this change.

| # | defect | what it did | state before |
|---|---|---|---|
| a | markdown-fenced JSON rejected by the strict parser | 8/8 calls parsed to nothing | fixed in the previous change (`_strip_code_fence`) |
| b | **`client.chat.completions.create(...)` called inline inside an `async def`** | with a sync `OpenAI` it blocks the event loop for the whole call; with an `AsyncOpenAI` it returns an un-awaited coroutine and dies on `.choices` — `'coroutine' object has no attribute 'choices'` — **after the request was already billed** | unfixed |
| c | **prompt named fields/enums the schema rejects** | a model that obeys the prompt produces a response the strict parser *must* reject | unfixed (§5 called this "step-1 work") |

(b) is the trap the task brief describes. (c) is why fixing (b) alone would still
have yielded zero. Both had to go, and neither is optional: the brief's success
criterion is non-zero yield.

Two further defects surfaced only while measuring the fix, and both are
"silently wrong write" shapes in the same module — see §6.

## 2. The exact fix

### 2.1 The async strategy — `run_blocking`, not a bare `await`, not a sync client

**Which of the two options I used: `app.utils.aio.run_blocking`.** Not
"revert to a sync client and call it inline", and not a bare `await` either.

`backend/app/services/trajectory_semantics.py:549`

```python
await ingest_budget.guard(SEMANTICS_OP)
response = await run_blocking(
    client.chat.completions.create,
    model=model,
    messages=[...],
    temperature=0.1,
    max_tokens=4000,
)
```

**Why `run_blocking` and not a bare `await`:** the injected `client` is a
sync `OpenAI` in every current caller — `ingestion_jobs._general_compute_client()`,
`_extraction_client()`, and `_RotatingOpenAIClient`, which only ever exposes a
sync `.create`. A bare `await` on `client.chat.completions.create(...)` would
`TypeError: object ChatCompletion can't be used in 'await' expression` on all
five call sites. The live probe below confirms the real client is a
`_RotatingOpenAIClient`.

**Why `run_blocking` satisfies the hard rule** ("never let a blocking call run
inside an `async def`"): `run_blocking` puts the sync call in the default thread
pool (`asyncio.to_thread`), so the loop stays free. That is the existing repo
convention — `applicability.py:258` already calls a chat completion exactly this
way, and `app/utils/aio.py`'s own docstring names the second behaviour:
"if the callable turns out to be async … the coroutine it returns is awaited
here, so callers work with either kind of client." So the same line is correct
for an `AsyncOpenAI` too, which is what makes the previous shape
(`await`-less, sync-only) a trap in both directions.

This is strictly a superset of the fix the summary proposed (a sync client) and
strictly a superset of the trap the pilot hit (an async client).

Two tests pin it, because the two failure modes are invisible to each other:
`test_sync_client_call_runs_off_the_event_loop_thread` (the sync client must not
run on the loop thread) and `test_event_loop_stays_responsive_during_the_extraction_call`
(other coroutines keep running across a 0.2 s call).
`test_async_openai_client_is_awaited_not_discarded` pins the reverse shape.

### 2.2 The prompt — derived from the schema, so it cannot drift again

`backend/app/services/trajectory_semantics.py:147` (`_schema_contract`) renders
`TrajectorySemanticExtraction.model_json_schema()` into the system prompt, with
`title` keys stripped. `PROMPT_VERSION` is bumped `v1` → `v2`: a prompt is a
versioned artifact, so the fix gets its own version rather than silently
reusing `v1`. The prose rules were corrected to the schema's own spellings
(`primary_goal`, step `description` + `subgoal_text`, lowercase
`observed`/`inferred`/`generalized`) and one rule added: emit no schema keywords
in the answer (`extra="forbid"` would reject them).

A drift pin can no longer exist, because there is nothing hand-written to drift.
Two tests replace the deleted one:
`test_prompt_is_derived_from_the_schema_so_it_cannot_drift_again` and
`test_every_field_name_the_prompt_shows_survives_the_strict_parser` — the
second pulls every backticked identifier out of the prompt and asserts the
strict schema accepts it. It fires on any future field rename.

**Note on cost:** the schema block adds ~1,000 input tokens (avg in 3,599 →
5,064) but per-call cost went **down**, $0.022233 → $0.016244, because output
went from 762 to 1,441 tokens of previously-rejected text to ~1,500 tokens of
schema-conformant text. The rejected output was cheap because the parse threw
the whole response away.

### 2.3 The budget guard

Half-gate rule: the trigger and the writer landed in the same change. The
summary's audit finding was that the extraction paths call
`ingest_budget.record_completion` and never `ingest_budget.guard`, so the daily
cap was post-hoc. `trajectory_semantics` was worse than the finding stated — it
recorded nothing at all.

| file | guard | writer |
|---|---|---|
| `services/trajectory_semantics.py:549` (new) | `guard(SEMANTICS_OP)` | `record_completion(model, SEMANTICS_OP, usage)` at `:560` |
| `services/skill_extraction/grounded.py:334` (new) | `guard("extraction")` | `record_completion` at `:356` |
| `services/skill_extraction/ungrounded.py:263` (new) | `guard("extraction")` | `record_completion` at `:280` |
| `services/claim_extraction.py:531` (new) | `guard("claim_extraction")` | `record_completion` at `:538` |
| `services/repo_ingestion.py:394` | already had both | — |

`SEMANTICS_OP = "trajectory_semantics"` is one constant used by the guard, the
ledger row and the pilot's report, so a pre-spend stop and its ledger row are
attributable to the same thing.

Two placement decisions worth stating:

- **The guard sits outside the `try` in both skill_extraction paths.** Those
  functions convert *everything* into `SkillExtractionTransientFailure`, which
  callers treat as a retryable rejection. A budget stop is a cost decision;
  laundering it as a transient failure turns a cap into a retry storm.
- **`BudgetExceeded` propagates as itself** from `extract_trajectory_semantics`
  (`:564`), for the same reason: a caller that retries transient failures must
  not burn attempts against a budget that is already spent. Because a new
  exception type now crosses the module boundary, all four calling surfaces
  handle it: `api/trajectories.py:236` and `:369` → 503, `mcp_server/server.py`
  `run_extraction`/`reextract_trajectory` → `REFUSED:`, and
  `scripts/run_trajectory_ingestion.py:150` breaks the loop and reports
  `budget_stopped` rather than crashing on an unhandled raise.

`guard` is a no-op unless a worker installed a budget, so the API and MCP paths
(which never do) are unchanged — pinned by
`test_no_budget_installed_means_no_gate_and_no_ledger_row` and
`test_no_budget_installed_leaves_every_path_unchanged`.

The pilot also gained `--budget-cap-usd` (default 0 = use the deployment's
`DAILY_LLM_BUDGET_USD`). It exists because `backend/.env` has
`DAILY_LLM_BUDGET_USD=100000`, which is a placeholder, not a ceiling.

## 3. A failure is no longer a silent success

Three distinct outcomes, never collapsed:

- **yield** — the pass produced ≥1 Goal/Claim/Procedure
- **abstention** — the pass completed and produced none (a real answer)
- **failure** — the pass raised, counted by exception type

`backend/app/ingestion/traj_pilot_cli.py`

- `_one_semantic_pass` (`:281`) is the whole verdict in one place; the counters
  are `semantics_attempted / succeeded / yielded / abstained / failed` plus
  `semantics_failure_reasons` and `semantics_knowledge_items`.
- `_semantics_ok` (`:270`) is the exit code: attempted > 0 and succeeded == 0 is
  `status: "degraded"` and **exit 1**, with a `DEGRADED:` line on stderr. The
  first pilot's exact shape.
- A missing client is now `semantics_failure_reasons["no_llm_client"]`, not a
  silent skip — `--semantics N` was requested and could not be honoured.
- A budget stop sets `args.semantics = 0` so the rest of the sub-sample is not
  spent on refusals.
- `trajectory_extractions` now reports `pending` as well, so
  `completed + failed` can be *checked* against the counters instead of assumed
  equal. It is equal: 34 + 16 = 50.

Also removed: the pilot's `_SpendRecordingClient`. It recorded spend at the
pilot's own boundary, which would now **double-count** every call alongside the
module's own `record_completion`. `llm_calls` is read from the `llm_spend` row
count over the run's own interval, and `spend.spend_is_accounted_for` is False
when completed calls outnumber ledger rows — so a $0.00 reading can no longer be
mistaken for "the run was free" when it actually means "the money was never
recorded".

## 4. Re-run numbers

Two runs, both on the local shard, both with `--budget-cap-usd 10`.

### 4.1 Confirmation run — 200 trajectories, 10 extractions

`--limit 200 --semantics 10 --budget-cap-usd 10` → 625 s, $0.1639, exit 0.
166 accepted · **74 Goals / 17 Claims / 8 Procedures** · 10 attempted, 8
succeeded, 2 failed (`GoalQualityRejected`). 31.7 MB.

### 4.2 The 1,000-trajectory pilot

`--limit 1000 --semantics 50 --budget-cap-usd 10`, fresh `sl_step0b`:

| metric | value |
|---|---|
| rows streamed / resolved | 1,000 / 1,000 |
| **accepted** | **729** (1.00 episode each; 44,795 events) |
| rejected | 271 — `duplicate_instance_in_run` 135, `held_out_repo` 64, `license_unmappable` 35, `license_quarantine` 27, `held_out_instance` 10 |
| license decisions | ALLOW 729 · QUARANTINE 27 · unmappable 35 |
| scaffold turn-capped | 30 |
| malformed rows / events | 0 / 0 |
| **extraction calls attempted / succeeded** | **50 / 34** |
| abstained | 0 |
| failed | 16 — all `GoalQualityRejected` (§5) |
| **knowledge items produced** | **330 Goals · 61 Claims · 34 Procedures = 425 new rows** |
| epistemic mix of the 447 citation rows | inferred 276 · observed 144 · generalized 27 |
| **$ spent in run** | **$0.818129** (`trajectory_semantics` $0.8121, `embedding` $0.0060, judge chain $0.0000) |
| **$ per accepted item** | **$0.001122** (per accepted trajectory, in-run total) |
| **$ per accepted knowledge item** | **$0.001943** (report figure, in-run total ÷ 421 objects the extraction counted); **$0.001911** extraction-only ÷ 425 new rows |
| **wall time** | **3,722.7 s = 62.0 min** (705 items/h) |
| **DB bytes** | **+136,208,384 (136.2 MB)** delta; 153,386,343 total |
| `trajectory_extractions` | total 50 · completed 34 · failed 16 · **pending 0** |
| process exit code | 0, `status: ok` |

Reconciliation: 34 completed + 16 failed = 50 attempted. `semantics_knowledge_items`
(421) counts objects the extraction *claims* to have created including dedup hits;
`knowledge_delta` (425) counts new `goals`/`knowledge_nodes`/`procedures` rows.
They are different quantities and both are reported rather than reconciled by
fudge.

**Comparison with the broken run** (same corpus, same shard size, local only):

| | before (1000-traj) | after (1000-traj) |
|---|---|---|
| knowledge items | **0** | **425** |
| extraction calls succeeded | 0 of 13 | **34 of 50** |
| spend | $0.00 raw path / $0.20 probe | $0.818 |
| reported status | "done", exit 0 | `ok`, exit 0, counters reconcile |

### 4.3 Why `--semantics 50` and not 1,000

**The $10/day cap did not bind.** Measured per-call cost is **$0.016244**, not
the $0.022233 in the summary, so $10 buys ~615 calls. The binding constraint was
wall time: the pass is output-bound at ~1,440 output tokens on
`gemma-4-31B-it`, and each extraction also drives the compaction judge chain
(avg 13.5 judge calls). Measured ~62 s per extraction, so 50 calls ≈ 52 min and
1,000 calls ≈ 17 h. 50 calls is a 5× larger yield sample than the confirmation
run at an eighth of the day's cap. **Projected full corpus at the measured rate:
32,161 extractions ≈ $522** (not the $715 the summary projected), ≈ 23 days of
wall clock at 50 calls/hour of extraction. Both numbers are the report's own
`projection_full_corpus`.

## 5. The remaining 16/50 failure — one cause, and it is the gate working

All 16 failures are the same rejection from `find_or_create_goal`
(`services/goals.py:443`, `GoalQualityRejected`), e.g.:

```
canonical_name 'Locate existing `fillna` implementations in `geopandas/array.py`
and `geopandas/geoseries.py`.' rejected: names a hyper-specific literal file
path, not a generalizable outcome
```

**100% of the failures are the goal-quality gate correctly refusing to write a
file-path-shaped Goal.** That is the gate doing its job, not a bug in it. The
loss is architectural and is **not** fixed here: one rejected `primary_goal` or
step `subgoal_text` aborts the whole pass, discarding the Claims and Procedures
that parsed fine. That asymmetry is worth two follow-ups, both out of B1's
scope and both needing their own measured run:

1. **Prompt** — state that a Goal is a generalizable outcome and must not name
   a literal file path. Cheapest fix, same lever as §2.2.
2. **Structure** — a rejected Goal should be dropped into `uncertainties` and
   the pass should continue, exactly as `parse_extraction_response`
   (`trajectory_semantics.py:270`) already does for elements whose
   `event_indices` do not resolve. That would recover most of the 32% at no
   extra spend, but it changes extraction semantics and needs its own tests.

## 6. Two more silent-write defects found while verifying the fix

Both were found by querying the rows the run produced, not by reading code.

**6.1 A post-parse failure left the row at `pending` forever.** The module's
docstring promises the row "flips to 'completed'/'failed' at the end", but only
the LLM call and the parse had handlers. The 200-run produced
`completed 8, failed 0` beside a counter saying 2 failed — two rows stuck at
`pending`, which reads as "never ran" to
`run_trajectory_ingestion.py`'s own re-extraction query (`WHERE NOT EXISTS
(SELECT 1 FROM trajectory_extractions …)`), silently orphaning the work. The
persistence phase is now a nested `_persist()` with one handler covering every
write; the close-out UPDATE is `AND status='pending'` so a late failure cannot
rewrite a completed row. The 1,000-run reports `pending 0`.

**6.2 `confidence_summary` was stored as a JSON *string*, not an object.** The
write passed `json.dumps(summary)` to a `::jsonb` parameter — but
`app/db/session.py:23` registers a `jsonb` codec with `encoder=json.dumps`, so
the value was encoded twice. Measured: `jsonb_typeof(confidence_summary)` =
`"string"` for all 34 completed extractions, so
`confidence_summary->>'goals'` was `NULL` and this module's per-extraction yield
counters were unreadable by anything, including
`GET /v1/trajectories/extractions/{id}`. Now the dict is bound directly
(`trajectory_semantics.py:751`); the same trap is already documented at
`route_decision.py:450` and `trace_worker.py:1280`. **Live-verified** on the
local shard — one real extraction, `jsonb_typeof = object`,
`->>'goals' = 9`. The 34 pre-fix rows stay double-encoded; no backfill (hard
rule 1), and the 17 correctly-shaped rows are distinguishable in one query.

## 7. Tests

Hard rule 7 — proving tests ship in the same change as the code.

**New: 3 files, 44 collected tests** (12 test functions in the first — some
parametrized over `grounded`/`ungrounded` — 13, and 19 across the sweep's
parametrization)

- `tests/test_trajectory_semantics_async_and_budget_offline.py` (14) — both
  client shapes, loop-not-blocked (two independent tests), guard-before-spend
  asserted as an *order* plus the ledger write, over-cap refusal spends nothing
  and makes zero provider calls, `BudgetExceeded` is not an
  `ExtractionTransientFailure`, no budget installed ⇒ unchanged, the two
  prompt/schema pins, a post-parse rejection closes the row, the `pending`
  guard, and the `confidence_summary` object-vs-string pin.
- `tests/test_extraction_budget_guard_offline.py` (17) — an `ast` **sweep**
  over all five paid extraction paths asserting `ingest_budget.guard` is called
  before the provider call by *line number*, plus a caller census that fails if
  a sixth path appears. A sweep rather than five per-site tests, because the
  failure mode being closed is "someone adds a path and forgets the gate", and
  per-site tests are exactly what that survives.
- `tests/test_traj_pilot_semantics_accounting_offline.py` (13) — the three
  outcomes, a transport bug counted by exception type, a budget stop that stops
  asking, no client ≠ silent skip, and the run-level verdict including
  attempted-and-all-failed ⇒ not-ok.

**Deleted deliberately:** `test_extraction_prompt_and_schema_disagree_on_names_and_enum_case`
in `tests/test_chat_message_trajectory_offline.py`. It asserted the drift still
existed and said in its own text "if the prompt is corrected, delete this test
deliberately". A header note points at its two replacements.

**Counts, before → after** (no pytest config; `DATABASE_URL` unset)

| set | before | after |
|---|---|---|
| the 4 step-0 / trajectory-semantics / budget files | 64 passed, 1 failed (65) | 63 passed, 1 failed (64) — the deleted drift pin, now covered by the new files |
| 3 new files | — | **44 passed** |
| the full affected set (18 files: the above 4 + parse/compaction/api/claim×2/skill×2/segmentation×2/repo×3) | — | **321 passed, 1 failed** |

`app.main`, `app.mcp_server.server`, `app.api.trajectories` and
`app.ingestion.traj_pilot_cli` all import clean.

**The 1 failure is pre-existing and unrelated.** It is in the baseline, it is
not mine, and I did not touch it:
`test_corpus_level_cc_by_is_not_used_as_the_per_item_gate` asserts
`classify_spdx("CC-BY-4.0").decision != "ALLOW"`, but HEAD commit `573d2c3`
("CC-BY-4.0 accepted with attribution") put CC-BY-4.0 on the permissive
allowlist. The test encodes a licence-policy assumption the repo has since
deliberately reversed, and un-skipping it is a policy call, not a test fix.

The full offline suite was **not** run, per the brief.

## 8. Files changed

```
backend/app/services/trajectory_semantics.py        async call, budget gate, schema-derived prompt,
                                                    _persist() failure handling, confidence_summary bind
backend/app/ingestion/traj_pilot_cli.py             3-outcome accounting, degraded exit 1, --budget-cap-usd,
                                                    ledger row count, pending column
backend/app/services/claim_extraction.py            pre-spend guard
backend/app/services/skill_extraction/grounded.py   pre-spend guard (outside the try)
backend/app/services/skill_extraction/ungrounded.py pre-spend guard (outside the try)
backend/app/api/trajectories.py                    BudgetExceeded -> 503
backend/app/mcp_server/server.py                   BudgetExceeded -> REFUSED
backend/scripts/run_trajectory_ingestion.py        budget stop breaks the loop
backend/tests/test_chat_message_trajectory_offline.py   drift pin deleted deliberately
backend/tests/test_trajectory_semantics_async_and_budget_offline.py   new
backend/tests/test_extraction_budget_guard_offline.py                new
backend/tests/test_traj_pilot_semantics_accounting_offline.py        new
```

Not touched, deliberately: `procedures.py`, `ingestion_jobs.py`,
`trace_worker.py`, `workflow_knowledge.py`, `skillmd_*` — other lanes are
writing to this checkout in parallel (see the working-tree warning in
`step_0_SUMMARY.md` §6), and a step-0 re-run still wants its own worktree for a
clean measurement. Nothing was committed.

## 9. Artifacts

- `.scratch/ingestion/step_0_B1_fix_200.json` — the 200-trajectory confirmation run
- `.scratch/ingestion/step_0_B1_fix_1000.json` — the 1,000-trajectory pilot
- Shards: `sl_step0` (200-run, pre-fix `confidence_summary`) and `sl_step0b`
  (1,000-run + live probe, post-fix). Both on `localhost:55441`, disposable.

## 10. What is still open

1. **The 32% `GoalQualityRejected` loss** (§5). Cheapest fix is a prompt rule;
   the structural fix recovers it at no extra spend. Both need their own run.
2. **One episode per trajectory** (`step_0_SUMMARY.md` F6) is unchanged: the
   extractor still sees a whole 64-step trajectory as one unit, at
   ~1,440 output tokens per call. Output is the cost driver, so segmentation is
   the main lever on the $522 full-corpus projection.
3. **Step 0 should still be re-run in its own worktree** before its numbers are
   treated as a clean measurement — this run inherited another lane's
   uncommitted edits to shared files, same caveat as the original.
4. **The pre-existing CC-BY test failure** (§7) needs a policy decision.
