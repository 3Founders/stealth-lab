# Ingestion granular audit — function-level, eight areas

Date: 2026-09-28. Read-only audit. No product code changed by this pass.
Areas: trace write · observations · ingestion context · `ingestion_jobs` handlers · queue/worker/
enqueue/scheduler · paid-provider surface · skill compiler · canonical write layer · trajectories/repo/
sources · DB index coverage.

**Verification convention.** Every finding quotes code read from the working tree and carries a
`file:line`. Index-utilisation claims are labelled **[INFERENCE]** — no `EXPLAIN` was run, because
no database was contacted. Severity is about *consequence*, not effort.

---

## 0. Two things that change how you read this

### 0.1 Part of pass 1 was committed by someone else, without its proving test

`git show HEAD:backend/app/services/embeddings.py` **contains** the `duplicate_pairs` cache-dedupe
change, and the file is clean in `git status`. The commits on top of it are
`0c956d9 core-b: Step 2 summary...`, `58026bd core-b: wire ingest_verified_solution job type...` —
none of them mine.

Meanwhile `trace_worker.py`, `ingestion_jobs.py` and `test_trace_payload_cap.py` are still
uncommitted, and **`backend/tests/test_embedder_cache_dedupe_offline.py` is untracked** — it is the
proving test for code that is now in `HEAD`.

That is a hard-rule 7 violation ("proving tests ship in the same change") created by a concurrent
commit sweeping up an uncommitted file. The fix is one `git add` of that test file, not a code
change. Until then the dedupe and the `(api_key, max_retries)` client cache are **shipped and
unproven in the gate**.

### 0.2 The tree is moving

Fourteen files were already modified when this audit started, and the line numbers below were read
at different moments during it. Every claim here is "true when read"; re-verify anything you intend
to act on.

---

## 1. P0 — correctness, security, and budget

### 1.1 A paid provider is booked as free

`ingest_budget.py:34-35` remaps provider names before pricing:

```python
_PROVIDER_PRICING_NAME = {"gemini": "google", "google": "google", "voyage": "voyage", "gemma": "local",
                          "local": "local", "jev": "local", "openai": "openai", "anthropic": "anthropic"}
```

`gemma` → `local` → `(0.0, 0.0)`. But `providers.py:534-546` makes `gemma` a **paid hosted
General Compute call** when `GENERAL_COMPUTE_API_KEY` is set:

```python
    if name == "gemma":
        # Production fallback: use the configured General Compute
        # OpenAI-compatible endpoint when available.
```

So `_PRICE_PER_MTOK["gemma"] = (0.16, 0.16)` (governance.py:150) is **unreachable from the judge
chain**, and every tier-3 fallback call records `$0.00`. `gemma` is the last entry in
`semantic_provider_fallbacks` (`config.py:238`) — i.e. exactly the provider you pay for when the two
free-ish tiers are exhausted, which is the case the budget exists to bound.

The same table's unknown branch prices at the table max `(3.0, 15.0)`. `vertex` is *not* in
`_PROVIDER_PRICING_NAME`, so it falls there: `record_embedding` then books **input only**
(`ingest_budget.py:129` passes one positional, `output_tokens=0`) at 20-25x a real embedding price.
The daily cap on an embedding-heavy run trips at roughly 4% of the real bill. Safe direction,
unusable guardrail.

### 1.2 Every paid path that spends without a `guard` — complete list

`guard` exists in exactly three places repo-wide: `embeddings.py:423`, `semantic/chain.py:166`,
`repo_ingestion.py:394`.

| Call site | Records? | Guarded? | Paid? |
|---|---|---|---|
| `embeddings.py:441` `record_embedding` | yes | **yes** (423) | yes |
| `chain.py:192` / `:202` `record_judge` | yes | **yes** (166) | yes |
| `repo_ingestion.py:403` | yes | **yes** (394) | yes |
| `deps.py:399` `CostGovernor.record` | yes | yes (via `enforce_limits`) | yes |
| `claim_extraction.py:511` | yes | **NO** | yes — one call **per chunk** |
| `skill_extraction/grounded.py:358` | yes | **NO** | yes — **`max_tokens=16000`** |
| `skill_extraction/ungrounded.py:282` | yes | **NO** | yes — **`max_tokens=16000`** |
| `trajectory_semantics.py:443` | **no** | **NO** | yes, 1/episode |
| everything under `procedure_extraction/` | **no** | **NO** | yes, 1/job + a synthesis call |

`procedure_extraction/` has **zero** `ingest_budget` references across all 12 files, while
`ingestion_jobs.py:1917-1919` calls the sweep's jobs "a real paid LLM call". The only pre-spend check
is coarse and at the wrong granularity — `worker.py:192-194`, once per *job lease*, so a job making
30 chat completions is admitted on one check.

A second, independent under-count: `ingest_budget.record_completion` returns without recording when
`usage is None` (`ingest_budget.py:136-137`), and all three unguarded sites pass
`getattr(response, "usage", None)`. An OpenAI-compatible provider that omits `usage` makes the spend
*absent*, not mispriced. Compare `make_cost_recorder`, which estimates from text length
(`deps.py:403-404`). Two different fallbacks for the same unknown.

All three also use `provider="google"` (the default), so extraction on Vertex or General Compute is
booked at Google rates.

### 1.3 GitHub bearer token survives a redirect to any public host

`github_corpus.py:54-80` builds `headers` **once, before the redirect loop**, and never rebuilds it:

```python
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    ...
    for _ in range(4):
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            current = str(httpx.URL(response.url).join(response.headers["location"]))
            assert_safe_locator(current)
            response = _http_get_with_retries(lambda u=current: httpx.get(u, ..., headers=headers))
```

`assert_safe_locator` will not stop it: it is a real DNS+IP-identity check
(`screening.py:490-515`, `screening.py:477-490`) that admits **any public host**. A 302 from
`api.github.com` to `evil.example.com` forwards the bearer token. Contrast `gh_client.py:87`, which
has `should_attach_credentials(host)` and does it correctly — `github_corpus.py` does not use it.

`_LOCATOR_HOST_ALLOWLIST` (`screening.py:467`, five GitHub hosts) **exists and gates nothing** — it
appears only inside the `reason` string at `screening.py:545`. "host on allowlist" is a description,
not a decision.

Also in that function: **no body-size cap.** `return response.status_code, response.content` buffers
the whole body; the `limit` check at `:312` runs after. `url_adapter.py:54-62` does it correctly with
`client.stream` and a 20 MB cap. And `assert_safe_locator`'s own docstring claims it should be called
"immediately before any server-side fetch (`httpx.get`, `git clone`)" — `_ensure_git_snapshot` never
calls it. The `git clone` safety rests entirely on `CorpusSourceSpec.__post_init__`.

There is also a DNS-rebinding TOCTOU: `assert_safe_locator` resolves the name, `httpx` resolves it
again independently, and the two results are never bound together.

### 1.4 Cross-tenant episode loss in transcript assembly

`trace_worker.py:1250-1254`, `write_session_episodes`:

```python
            for row in await conn.fetch(
                "SELECT id, metadata->>'assembly_fingerprint' AS fp FROM episodes "
                "WHERE session_id = $1",
```

No `scope_predicates()`, no visibility predicate, no `owner_id`, no tenant. And `episodes` is
deliberately RLS-free (`29_rls_backstop.sql:54-59`), so nothing downstream catches it.

The fingerprint (`trace_worker.py:879-893`) is `session_id` + episode *shape* — no content, no
tenant. So two tenants ingesting the same `session_id` compute the **same** fingerprint, tenant B's
episodes are counted `skipped_existing`, and are **never written**. Silent cross-tenant data loss,
no error, no log line. The write at `:1302-1303` *does* set owner/visibility, so the asymmetry is a
bug rather than a posture choice.

### 1.5 A tenant-bound transaction that ignores its binding

`ingestion_context.py:121-125`:

```python
        await conn.execute(
            "UPDATE ingestion_contexts SET status = $2, completed_at = now() WHERE id = $1::uuid",
            context_id, status,
        )
```

Runs inside `tenant_transaction(pool, scope)` (`:120`) and then filters on **nothing** but a
caller-supplied uuid. It will close any context id from any tenant. Currently saved only because
callers pass back an id they just minted themselves.

Related, `ingestion_context.py:83`:

```python
    bound_tenant = tenant_id if tenant_id is not None else scope.tenant_id
```

lets a caller stamp a row with an arbitrary organization inside a transaction bound to commons. The
column and the `app.tenant_id` setting then describe different tenants. Inert today; a booby-trap for
the multi-tenant cutover.

### 1.6 License screening: two opposite defaults for the same question

`screening.py:403-425` is a **denylist** of 21 ids. `spdx_license_signal` returns a finding only on
membership. So `NOASSERTION` — what GitHub returns when it *cannot classify* — is silently allowed,
and a repo relicensing GPL-3.0 → BUSL-1.1 goes REJECT → admit.

`repo_license_policy` is an **allowlist** and is wired into `repo_ingestion.py` only. Its four
importers are `ast_grep_rules.py` and `repo_ingestion.py` — never `skill_ingestion`, `screening`,
`artifact_blocks`, or `ingestion_admission`. So a corpus admitted by one path is QUARANTINEd by the
other. `repo_license_policy.py:4-13` states the trade-off explicitly ("a license nobody has ruled on
is not a pass, it is a QUARANTINE"), which makes the skill path the defect.

Also: `spdx_license_signal` returns `"severity": "flag"`, and `record_screening_run` maps flag →
QUARANTINE, not REJECT. Only the caller's choice to treat any non-`None` finding as fatal makes it a
block. The severity encoding misleads a future reader.

### 1.7 Stale-marking and replacement-capture are in no transaction

`skill_ingestion.py:3002-3011` loops `mark_procedure_stale`, which is **four independent autocommit
statements** per prior row (`procedures.py:1252` SELECT, `:1258` UPDATE,
`changeset_record.py:106` INSERT, `:111` executemany) with no `FOR UPDATE` and no
`WHERE staleness='fresh'`. The first `capture_procedure` is at `skill_ingestion.py:3113` — 109 lines
later.

A crash in between leaves: old procedures `staleness='stale'` (which
`applicability.check_hard_constraints()` excludes from retrieval), **no replacement rows**, no
`ingested_artifacts` row for the new content hash, and an `ingestion_contexts` row still `open` if
the process was killed. The bi-temporal contract — "nothing is deleted; an update closes the old
validity window and appends a new row" — is violated in the direction that loses retrieval
availability rather than data.

Retry converges (the staleness precheck requires `procedure_row_id IS NOT NULL`, which the crashed
attempt never wrote; the second stale-mark is a no-op by contract at `procedures.py:1255-1256`), so
the damage window is the retrieval outage, not the rows.

**`supersede_procedure` exists, is fully transactional and correct** — `procedures.py:617-677` does
`SELECT ... FOR UPDATE` then sets `t_invalid`/`t_expired` and appends a `SUPERSEDES` edge. It is
**deliberately not used here** (decision recorded at `skill_ingestion.py:2711-2720`), but the import
at `skill_ingestion.py:67` is dead and two docstrings (`:827-828`) still describe the old behaviour.

### 1.8 A bundle's advisory lock provides exclusion, not atomicity

`ingestion/handlers.py:87-88` opens `async with pool.acquire() as lock: async with
lock.transaction():` and takes `pg_advisory_xact_lock` at `:89`. But `:95`, `:113`, `:122`, `:127`
all use `pool` — **other connections, outside that transaction**. The module docstring (`:24-25`)
says "the whole bundle runs under one advisory lock". The exclusion half is true; the atomicity
half is not. A crash mid-bundle leaves a Goal with no Procedure.

The lock also holds a pooled connection for the entire bundle *including every LLM judge call*, which
is why `worker.py:336` sizes the pool `2*concurrency + 4`.

### 1.9 A second unpaid LLM call on the success path

`ingestion_jobs.py:2141` `_maybe_auto_synthesize` fires whenever the discovery query returns ≥
`MIN_CANDIDATE_EPISODES - 1` rows — i.e. on the *normal* success path, not a rare branch. Nothing
counts it against the `limit` the operator set, and `procedure_extraction/` has no budget references
at all.

### 1.10 Tenant discipline in the canonical write layer

`claims.py`: **1 of 15** statements derives its predicate from a builder (`list_current_claims`,
`:790-839`, which threads `param_index` correctly). The other 14 are hand-written and unscoped.
The sharpest is `relate_claims` at `:438-443`:

```python
            await conn.execute(
                "UPDATE knowledge_nodes SET properties = "
                " properties || '{\"truth_state\": \"OUT\"}'::jsonb "
                "WHERE id = $1::uuid", to_claim_id,
            )
```

A cross-tenant truth flip gated on nothing but a uuid. Mitigated only because the id comes from a
caller that already read the claim — there is no structural guard.

`procedures.py`: **32** statements, and `scope_predicates` / `tenant_predicate` /
`visibility_predicate` appear **zero** times in the file, despite importing `AccessScope` and
`tenant_transaction` at `:39`.

`run_procedure_dedup_sweep` (`:1497`) takes an `AccessScope` and its docstring justifies the missing
scope with: *"procedures carries no tenant_id column (see that function's own docstring)"*. **That
is stale** — `db/47_procedures_tenant_id.sql:16` added it. The sweep reads across all tenants under
`unrestricted()` on a table that can now carry a tenant.

**Important nuance:** these are *not* hand-written tenant *filters* — they are unfiltered
primary-key lookups that rely on the caller having resolved the id through a scoped read. The RLS
backstop (`db/29`) holds the line and is permissive-when-unset by design. The genuine gap is
`db/29`-covered tables written without `tenant_transaction`: `changeset_record.py:106-120` (the
universal ChangeSet audit sink) and `execution/plan_persistence.py:197-202`. Their writes succeed
and take the `NOT NULL DEFAULT` commons tenant, so the RLS arms are silently inert on the audit
tables.

### 1.11 The one-builder test is wrong; the other one is right

`test_phase1_security_boundaries_offline.py:281` fails with
`hand-written tenant filters in ['goal_abstraction.py']`. **The code is compliant.** Two independent
reasons:

1. The only `tenant_id = $` in that file is `goal_abstraction.py:803`, inside an **UPDATE SET list**
   — a column assignment whose value is a real `TenantScope` (`relation_tenant = tenant_scope.tenant_id`,
   `:693`), not a predicate.
2. The test's escape hatch is a typo. It exempts a file containing `"services/access.py"`;
   `goal_abstraction.py` imports it as a parenthesised block, so the literal never appears. The
   module that uses the builders *most heavily* in the services tree is the one flagged.

The correct test already exists and passes: `test_hardening_h1_identity_tenancy.py:390-412`, whose
regex requires a `WHERE`/`AND` on the same line as `tenant_id =` and whose docstring explains the
INSERT-column-list exemption. A repo-wide sweep with that regex returns hits only in `access.py`
itself. **Fix: delete the `phase1` test, or repoint it at the `h1` regex and fix the escape hatch.**

### 1.12 Quarantine writes unredacted, unbounded, to an unignored file

`trace_worker.py:121-128` appends the collector file's literal bytes to `<file>.quarantine`, with no
`redact_event`, in `"a"` mode, no rotation and no size cap. Content is post-redaction for the
collector path so it is not a *new* secret source, but a torn write can leave a fragment that never
completed a redact pass, and the file inherits none of the `.gitignore`/`.claude/traces` posture.

### 1.13 Blocking, unguarded, unrecorded — and blocking the loop

`trajectory_semantics.py:443-452`, inside `async def`:

```python
        response = client.chat.completions.create(
            model=model, messages=[...], temperature=0.1, max_tokens=4000,
        )
```

Sync client, no `asyncio.to_thread` (contrast `repo_ingestion.py:398`, same call, done right), no
`guard`, no `record`. Both API routes also lack the `Depends(enforce_limits)` +
`make_cost_recorder` pattern CLAUDE.md makes mandatory, and `api/trajectories.py:337` re-extraction
has no rate or cost control at all. The batch script's `--extract-limit` is a count cap, not a
budget.

### 1.14 `_EMBED_CACHE` memory bound is wrong by ~8x

`embeddings.py:175-176` claims "Bounded FIFO eviction keeps memory at ~80MB worst case." A 1024-dim
`list[float]` is ~32 KB; × `_EMBED_CACHE_MAX = 20000` is **~640 MB**, plus 20 000 ~90-char keys. The
cap is process-lifetime and monotonic up to the limit, so a long ingestion run will reach it. (The
*eviction policy* is also FIFO, not LRU: `_cache_put` only re-orders on insert, so a hot key is
evicted like a cold one.)

---

## 2. P1 — index coverage of the hot statements

**Migration numbering:** 128 files, `01`–`125`, highest `125_procedure_verifier_check.sql`.
**Next free number: 126.** (`72_`, `73_`, `74_`, `110_` each appear twice.)

| # | Statement | Served? | Evidence |
|---|---|---|---|
| 1 | `_LEASE_SQL` 3-way status OR + `ORDER BY id` | **PARTIAL** | `queue.py:171-191` |
| 1b | `claim_jobs` — `status='pending' ORDER BY id` | **YES** | `12:144-145` — the index the newer statement lost |
| 2 | `ingestion_contexts(source_uri, status='open')` | **NO** | no index on `source_uri` anywhere |
| 3 | `trace_events ON CONFLICT (dedup_key)` | **YES** | `12:108` `dedup_key TEXT NOT NULL UNIQUE` |
| 4 | `ingestion_jobs ON CONFLICT (job_type, idempotency_key)` | **YES** | `95:359-360`, partial `WHERE` matches the query textually |
| 5 | `embedding_rate_windows(scope)` ×4 | **YES** | `42:25` `scope TEXT PRIMARY KEY` |
| 6 | `enqueue_skill_package_jobs` exists-check | **NO** | two independent blockers |
| 7 | `observations.py` write + read-by-id | **YES** | PK; `observation_events` uniqueness re-established at `79:67-70` |
| 8 | `ingestion_context.py` INSERT / UPDATE / SELECT-by-id | **YES** | PK |
| 9a-c | `resume_failed_*`, `requeue_stuck_jobs` | **NO** | operator-triggered, low blast radius |
| 9d | `reap_exhausted` | **YES** | `95:361-362` — the statement the lease index was written for |

**Row 2 is the worst amplification in the pipeline.** `ingestion_jobs.py:115-120`:

```sql
"SELECT id FROM ingestion_contexts WHERE source_uri = $1 AND status = 'open' "
"ORDER BY started_at DESC LIMIT 1"
```

`source_uri` is in **no** index. `idx_ingestion_contexts_actor` is `(actor_id, started_at DESC)` —
`actor_id` is absent from the predicate, so the index cannot be driven, and its `DESC` second column
only orders *within one* `actor_id`, not globally. **Seq scan + sort, per trace event** — and
`trace_worker._BATCH_JOB_SQL` enqueues one `normalize_trace_event` per inserted `trace_events` row.
The table grows by one row per session while the query rate grows by one per event, so the scan cost
per unit time grows linearly in the size of a table that never shrinks.

Worse, the trace path passes `source_hash=None` and no `source_ref`/`run_ref`
(`ingestion_jobs.py:127`), so trace-path rows are excluded by the partial predicates of the other
three `ingestion_contexts` indexes and covered by none that can serve this query. Proposed shape:

```sql
-- shape only, not authored
CREATE INDEX CONCURRENTLY idx_ingestion_contexts_source_uri
    ON ingestion_contexts (source_uri, started_at DESC) WHERE status = 'open';
```

**Row 6 has two independent blockers.** `ingestion_jobs.py:1590-1600` repeats
`(CASE WHEN jsonb_typeof(payload)='string' THEN (payload #>> '{}')::jsonb ELSE payload END)` **three
times** in one predicate. [INFERENCE] Postgres matches an expression index by *syntactic* identity of
the indexed expression, so `42:81-89`'s bare `payload->>'source_id'` has nothing to match against a
`CASE`-wrapped expression. Independently, the index's partial predicate is
`status = 'pending'` while the query says `status IN ('pending','processing','done')` — the
implication fails on that alone. And the INSERT at `:1603-1608` has **no `ON CONFLICT`**, so this
scanned SELECT is the *only* dedup guard on the path; a duplicate that has reached `done` inserts a
second row freely.

**Row 1's asymmetry is the lesson.** `claim_jobs` (`ingestion_jobs.py:2591-2601`) and `reap_exhausted`
(`queue.py:265-268`) are both exactly served by the partial indexes that exist. `_LEASE_SQL` is the
same predicate with `ORDER BY id LIMIT n` bolted on and a third OR branch added, and that is what
breaks it — branches 2 and 3 have filter-only indexes leading on `run_after`/`lease_until`, not `id`.

**No retention anywhere for three of the four hot tables.** `retention.py:14-19` prunes only
`retrieval_decisions`, `projection_outbox`, and `ingestion_jobs` — and the `ingestion_jobs` prune is
`status IN ('done','cancelled')`, so `'failed'` and `'retryable_failed'` rows are **never** deleted.
`trace_events`, `observations` and `ingestion_contexts` have no delete path in application code at
all. First indexes to degrade: `trace_events_dedup_key_key` (1:1 with rows, content-hash keys landing
at random B-tree positions, on the `ON CONFLICT` hot path) and `idx_observations_type` (1:1 with rows
at near-zero selectivity, on the highest-volume table). Also: `idx_ingestion_jobs_worker_status` is
`(claimed_by, status, claimed_at) WHERE claimed_by IS NOT NULL`, and `queue.complete()` **never clears
`claimed_by`** (only `release()` does) — so it grows with total job throughput, not backlog.

---

## 3. P1 — round trips and throughput

### 3.1 `write_normalized_trajectory` was not converted, and its excuse is now obsolete

`trace_worker.py:599`. **2,003 round trips per 1,000 events**, all serial: 1 header +
1,000 `_insert_event` + 1,000 job INSERTs, inside one whole-trajectory transaction. The batched
statement that now solves exactly this sits **one screen above** at `:414-432`.

`_insert_event`'s docstring justified keeping it (`:347-351`): *"its events come from an external
trajectory adapter and have never been redacted, so that path must not be folded into the collector's
batched statement without first proving the redaction pass is still applied per row."* Pass 1
performed that proof — `_prepare_event_row` (`:253`) is now the shared redaction chokepoint, called by
both paths. **The blocker named in the docstring no longer exists.** The work is a behaviour-
preserving refactor that needs its own proving test, not a redesign.

Secondary, and more dangerous: `normalized_trajectory.py:62` defaults `dedup_key: str = ""`. An
adapter that forgets the field writes `dedup_key=''` on every event; `ON CONFLICT (dedup_key)` then
admits exactly **one** and silently discards the rest, each counted as a legitimate
`skipped_duplicate` at `:673`. The collector path cannot hit this (`_read_records:112` requires the
key); the trajectory path has no equivalent fail-fast. `openhands.py:420,433` does compute one, so
the real adapter is safe today.

### 3.2 `compile_skill_artifact` — ~590 round trips for one document

Worst case (8 procedures, 30 steps, 4 scripts, 10 references, 58 goals, 24 claims, 40 blocks):

| Phase | RT | Dominant term |
|---|---|---|
| 0 screening | 2 | 1 staleness precheck + 1 claim-cache INSERT |
| 1 provenance + goal prefetch | 239 | **4 × 58 = 232** goal resolution |
| 2 per-procedure loop (`:3027`) | 348 | 5 × 24 claims = 120; 3 × 40 blocks = 122 |
| 3 standalone goals (`:3262`) | 0 | prefetch cache hits |
| 4 completion | 1 | |

Goal prefetch (39%) and block projection (21%) are 60% of the cost. The 8 procedure rows are noise.

**The cheapest batchable win in the whole compiler is `persist_artifact_blocks`**
(`artifact_blocks.py:465`): one INSERT per block, 122 of 590 RTs, **already inside one transaction**,
already in `block_index` order, and already minting its own `uuid7()` at `:466` so it never reads back.
The only complication is `parent_block_id` (`:468-469`), which resolves against an id inserted
earlier in the same call — a two-pass problem solved by pre-minting all ids.

**The reference pattern to copy is `skill_ingestion.py:2059-2082`**, and its best feature is the one
other writers would skip — the template arity is *machine-checked*, not assumed:

```python
1996:    names = re.findall(r"\$%\(([a-z]+)\)d", _ARTIFACT_ROW_EXPRESSIONS)
1997:    if len(names) != REPO_ARTIFACT_ROW_PARAMS:
1998:        raise RuntimeError(...)
```

Two qualifications when copying it: it is **not** in a transaction (fine for idempotent metadata
upserts, not fine for a write that must be atomic with a parent), and it does **not** use
`tenant_transaction`. Copy the batching, not the connection discipline.

### 3.3 Remaining N+1s, ranked

| Site | Cost | Safe? |
|---|---|---|
| `artifact_blocks.py:465` per-block INSERT | 122 RT/doc | **SAFE** — one txn, own ids |
| `observations.py:432-443` two per-item loops | 1+N per observation | **SAFE** — `unnest` in the same txn |
| `claims.py:358-366` per-task edge INSERT | N per claim | **SAFE** — identical row shape |
| `search_projection.py:289-291` per-row outbox UPDATE | 200 per batch | **SAFE** — collect ids, one `ANY($1)` |
| `search_projection.py:229` per-row `lookup_routes` | 200 per batch | **SAFE** — `shards.py:396` is already built for N ids; the batch is in hand at `:272` |
| `repo_ingestion.py:693` per-blocked-path screening | 400 txns on an all-GPL repo | **SAFE** — `screening.py:633` writes one row per finding in its own txn |
| `repo_ingestion.py:757` per-file link UPDATE | 110 per 110-file repo | UNSAFE-UNKNOWN — the back half of the content cache (`procedure_id IS NOT NULL`) |
| `claims.py:550-555` **duplicate** `claim_sources` write | 1 per promoted observation | **UNSAFE-UNKNOWN** — provably a no-op (`capture_claim` writes it at `claims.py:373-382` in its own txn, and `PRIMARY KEY (claim_id, observation_id)`), but `test_band2_8_replayability.py:203-210` asserts on the *source text* of `observations.py`, so removing it breaks a test in another lane |
| `skill_ingestion.py:2121` / `:2486` per-script / per-reference | 2 each | UNSAFE-UNKNOWN — same shape, different lane |

### 3.4 `handle_normalize_trace_event` — 3 statements to write nothing

`ingestion_jobs.py:547`. The context lookup at `:585-588` runs **before**
`extract_deterministic_observations` at `:598`, with no `if observations:` between:

| Event | Statements |
|---|---|
| zero observations | 3 warm / 5 + 2 control cold — **and on the cold path it CREATES an `ingestion_contexts` row that is never closed** (nothing in this module calls `complete_ingestion_context`) |
| three observations | 11 warm, plus 3 separate transactions |

Marginal cost per observation: exactly 3 statements + one BEGIN/COMMIT. One of those 3 is a follow-up
`UPDATE observations SET ingestion_context_id` that exists **only** because `persist_observation` has
no such kwarg — the module says so at `:610-612`, and the callee signature (`:357-371`) confirms it.
Per CLAUDE.md's own note, `capture_claim` already does this correctly with both branches
(`claims.py:337-347`).

Two SAFE fixes, one of them not a code change at all: move the lookup after extraction and guard it
on `if observations:`; and add migration 126 for Row 2, which subsumes most of the cost.

### 3.5 Enqueue: 2N sequential round trips, and the re-run is the expensive one

`enqueue.py:49-59`, `:72-89`, `:98-113` are all `for … : await q.enqueue(…)`. No `gather`, no
`executemany`, no `UNNEST`, no `COPY`. Each duplicate costs a **second** round trip
(`queue.py:144-145`) because `ON CONFLICT … DO NOTHING RETURNING id` returns zero rows on conflict
and the id has to be recovered separately. Re-running an unchanged manifest is the documented normal
case (`enqueue.py:21`), so the count goes `1N → 2N` and the *re-run is the more expensive of the two*.
The whole manifest is also materialised in memory first, behind
`create_pool(control_database_url(), max_size=2)` (`:155`) — so there is no connection headroom to
parallelise even if someone added a `gather`.

`enqueue_repos` adds a **synchronous blocking `httpx` call from inside `async def`** at `:103`, on
every run, for every commit-less manifest line. And because the key is `repo:<sha>…<sha(commit)>`
(`:107`), a manifest that omits `commit` produces a **new job every time HEAD moves** — a slow-moving
branch silently re-ingests the whole repo. Nothing enforces or warns about pinning `commit`.

### 3.6 Two worker implementations, and the unfenced one is the automatic path

`queue.py:205` — `_OWNED = "id = $1 AND status = 'processing' AND claimed_by = $2 AND attempts = $3"` —
is threaded into every terminal write in the new path. Correct, and proven end-to-end by
`test_distributed_ingestion_e2e.py:106-117` (including the zombie-fencing case).

`ingestion_jobs.py:2652-2706` writes `"WHERE id = $1"` — **no `status`, no `claimed_by`, no
`attempts`**. Five divergences, not one:

1. Lost updates on requeue + re-lease.
2. The legacy claim (`:2598-2602`) **never sets `lease_until`**. A legacy-claimed row is
   `status='processing', lease_until=NULL`, which fails `q.lease`'s reclaim (`lease_until IS NOT
   AND`), fails `reap_exhausted`, and is counted as neither running nor expired by `ops_metrics`.
   Only manual `requeue_stuck_jobs` recovers it. One crash produces a row that no metric counts and
   no automated process reclaims.
3. The legacy claim sees only `status='pending'` (`:2587`) — the entire backoff/`retryable_failed`
   state machine is invisible to it.
4. `attempts` increments on every terminal write rather than at lease, so the two paths disagree
   about exhaustion.
5. No heartbeat, no backoff, and **every** handler exception is terminal (`:2694`) — a transient
   provider blip is permanent here and retryable on the other path.

And this is not dead code: `process_ingestion` (`api/admin.py:230`) calls `process_pending_jobs`, and
that is what `ingestion_scheduler`'s `global` mode calls every tick. **The automatic path is the
unfenced one.** So a CLI benchmark (`benchmark_projection_scale.py:199-228`, which drives
`Worker.run`) measures the fenced worker, while production-automatic runs the other. Three further
divergences in the same direction: the automatic path promotes and extracts on every tick
(`promote_limit`/`extract_limit` default 5 at `config.py:512-513`, vs 0 on the endpoint), and it also
drains `.claude/traces/*.jsonl`, which the CLI worker never does.

**They must not be run concurrently**, and the unfenced one must not be the automatic path.

### 3.7 Three job types are missing from any import that skips `app.ingestion.handlers`

`worker.py:80-88` imports `app.ingestion.handlers` for its **side effects**. A process that imports
only `ingestion_jobs` gets 11 of 14 job types. Missing: `ingest_candidate_bundle`
(`handlers.py:138`), `routing_local_refit` and `routing_refit` (`routing/jobs.py:26-27`).

A queued `ingest_candidate_bundle` is then found handlerless and **permanently failed** at
`ingestion_jobs.py:2676-2681` — `status='failed'`, `attempts` incremented, no retry. That is correct
behaviour for a genuinely unknown type and destructive for a known one that was never registered.
`test_routing_e2e.py:93` asserts a `routing_local_refit` row gets *enqueued*; nothing asserts the
handler side, so dropping `handlers.py:141-143` would leave that test green while every nightly
refit went straight to `failed`. **The gap is unpinned by any test.**

`worker.py:85` also binds the **live** `JOB_HANDLERS` dict and then mutates it, so constructing a
`Worker` has a process-global side effect (`test_benchmark_transfer_integration_offline.py:38`
depends on it).

### 3.8 Smaller worker/config findings

- `_lane` leases `limit=1` unconditionally (`worker.py:206`) — `--concurrency 8` issues 8
  single-row claims per batch where 1 would do.
- `reap_exhausted` is called at `worker.py:228` before the fan-out and `:230` after; in `--loop` mode
  the second is unreachable, so crash-looped jobs are reaped **only at process start**.
- `scheduler.py:146-153` — `ingestion_auto_mode` defaults `"local"` and takes the `else`, so the loop
  ticks every 60 s, increments `run_count`, and does nothing. A *loud* no-op, which is the right
  choice. But `config.py:489-503` still comments `"local"` as a working mode (P5 removed it), and
  `asyncio.sleep` happens *before* the first tick, so `run_count` reads 0 for a full interval.
- `validate_startup` (`config.py:71-102`) does a **blocking ADC handshake** via
  `build_provider_chain` (proved by the in-flight `_vertex_access_token` hardening elsewhere); checks
  `settings.voyage_api_key` only, so a `VOYAGE_API_KEYS`-rotation-only deployment is **refused**;
  and `worker.py:321` sets `require_skill_extraction` from a list that omits `ingest_document`, which
  needs the same client (`:498-504`) — so `--job-types ingest_document` passes validation then
  refuses every document.
- `WorkerConfig` clamps the `max(1, …)` fields but **not** `retry_base_seconds` / `retry_cap_seconds` /
  `poll_seconds`; a negative retry base yields a job due in the past, i.e. an immediate hot loop.
- `goal_abstraction_candidate_limit` / `neighbor_seed_limit` / `neighbor_limit` are read at
  `config.py:57-59` but consumed via `WorkerConfig()` at `ingestion_jobs.py:821` **constructed with no
  env** — operator overrides are silently ignored on the handler path.

### 3.9 Remaining provider-surface findings

- **Vertex embedding still builds `httpx.AsyncClient` per call** (`embeddings.py:481`). The comment at
  `:203-209` says *"both of these"* were fixed and names "Voyage **or Google**". Voyage was fixed;
  Vertex was not — same defect the comment claims is closed, on a paid path.
- `_acquire_gemini_budget` (`:569-623`) is 4 statements + a transaction **per `embed()`**, charged
  **per attempt, not per logical call**, and a key rotation's second paid attempt is unthrottled (the
  acquire is at `:528`, before the loop at `:531`). The `FOR UPDATE` is the mechanism and must stay;
  the surrounding round trips are foldable. The lock *is* correctly released before any provider HTTP
  request (`:623`, outside the `async with`).
- **The rate limiter covers Gemini only.** `vertex` and `voyage` have none — one exhaustive grep of
  `_acquire_gemini_budget` returns `:528` and its own `def`. So `embed_tpm_budget` does not bound
  embedding spend; it bounds one provider's rate.
- `record_embedding` at `:441` is **unreachable on failure** — a billed call that then raises records
  zero. `CostGovernor.record` swallows errors by design (`governance.py:583-588`); checks fail closed,
  records fail open. That asymmetry is deliberate and documented and is *not* the bug class.
- **`embed_cache.py`'s key omits provider and dimension** (`:103-104`) and never passes a provider, so
  changing `EMBEDDING_PROVIDER_CHAIN` / `USE_LOCAL_MODELS` / `EMBEDDING_DIMENSION` replays
  `backend/.cache_joint/embeddings.json` with vectors from the old space, with no version stamp and no
  dimension check on a disk hit. It is an unbounded on-disk cache rewritten in full on every call
  (`:152-153`), and `super().__init__(model=model, dimension=dimension)` leaves
  `rate_limit_pool`/`data_classification`/`policy_pool` as `None` (`:85`) — so the experiment path can
  **never** reach the policy gate or the cross-process DB limiter, making it a structurally
  incomparable baseline for the production path it is being compared against.
- `shards.py:640-645` `fanout_fetch` flattens with `[r for part in parts if part for r in part]`,
  silently dropping a timed-out shard's rows; `strict` defaults to `False`. `hydrate_rows` gets this
  right (`:518-544`, exception → `unavailable_shards`, short read → `missing_ids`) and its ordering is
  deliberate. `pools_for` (`:207`) is cached with **no TTL and no invalidation**, and there is no
  `close_shard_pools()` — `id(pool)` reuse is a live hazard.
- `preserve_repo_file_artifacts`'s blob PUT loop is **serial** (`skill_ingestion.py:2052`), so its
  `gate` is unreachable — a semaphore cannot be contended by code that awaits each acquire in turn.
  The docstring at `:2020-2025` and the repeat at `repo_ingestion.py:60-61` both claim the opposite.
  Same shape in `build_license_index` (`:615`): `fetch_gate` is decorative.
- **`handle_ingest_repo` never passes `base_commit`** (`ingestion_jobs.py:2757`), so the incremental
  commit diff — the "10-100x incremental re-ingest" claim — **never fires in production**. A silent
  capability gap: the docstring promises a feature the only production caller cannot reach.
- `capture_procedure` is one INSERT for the whole definition — steps, preconditions, failure
  conditions, invariants are all JSONB columns, and the only comprehension over them is a pure
  `any()` (`procedures.py:234`). **There is no per-step multiplier.** Its dominant cost is
  `find_or_create_goal_cached`, whose default is *no cache at all* (`goals.py:572-574`), so most of
  the 54 call sites pay an embedding + a judge call per capture that a caller-owned `goal_cache`
  would amortise to zero.
- `capture_claim`'s "skip the embedding spend when there is demonstrably nothing to anchor to"
  (`claims.py:295-303`) is a **two-tier** guarantee: verified for the pre-DB case, but the second
  refusal at `:334-336` is *after* the embed, so `task_ids=["ghost"]` — non-empty, all dead, exactly
  what a replayed ingestion produces — pays for a vector that is discarded.
- `_placement_candidate_decisions` (`ingestion_jobs.py:1047`) is the one handler helper that scales
  properly: 1 batched `judge_identity_batch` for all candidates, guarded upstream at `chain.py:166`,
  and a remembered `identity_decision_id` short-circuits to **zero** LLM calls. That is the model.

---

## 4. Stale docstrings and false claims (their own category)

These matter more than usual: several are the *reason* a future reader would "optimize" something
load-bearing.

| Claim | Reality |
|---|---|
| `trace_worker.py:60-62` "these are already-redacted (…upstream of every call in this module)" | **False for the trajectory path** — adapter-built events were never redacted. Safe only because `:288` precedes the insert. Someone trusting the comment and deleting the line leaks OpenHands secrets. |
| `_ensure_trace_header` docstring: "Same one-way COALESCE backfill discipline … for **every field** here" | `metadata` uses `||` (shallow merge, last-writer-wins), not COALESCE |
| `_insert_event` docstring: kept single-row "because … that path must not be folded into the collector's batched statement without first proving the redaction pass is still applied per row" | Pass 1 *is* that proof. The stated blocker no longer exists. |
| `_prepare_event_row` docstring: the two callers are `_insert_event` and `_insert_events_batched` | `_insert_events_batched` (`:461`) takes prepared rows and does **no** redaction; the collector calls `_prepare_event_row` itself at `:558-562`. Substance holds, attribution is one hop wrong. |
| `write_normalized_trajectory` docstring: "The **SAME** write path `process_collector_file()` uses" | Semantically true, mechanically false — 2,003 vs 9 round trips per 1,000 |
| `run_procedure_dedup_sweep` docstring: "procedures carries no tenant_id column" | `db/47_procedures_tenant_id.sql:16` added it |
| `ingestion_admission.screen_secrets` (`:298`) "mutates in place via `redact_value`" | Operates on a frozen dataclass → **no-op** |
| `IngestOutcome` (`:1405`) — 8 columns keyed on `status` | `skill_ingestion.py:2732-2735` says this function no longer produces `"new_version"`/`"duplicate"` |
| `worker.py` docstring: `Worker.reauth` on a timer | No `reauth` method exists |
| `config.py:489-503` comment describing `"local"` auto-mode | P5 removed that sweep; the mode is a recorded no-op |
| `skill_ingestion.py:827-828` "a changed source produces a NEW procedure version via `supersede_procedure`" | Contradicts `:2711-2720`, which records it as deliberately dropped. The import at `:67` is dead. |
| `repo_ingestion.py:60-61` / `skill_ingestion.py:2020-2025` "blob PUTs stay inside the same concurrency bound" | Serial loop; the gate is unreachable |
| `test_phase1_security_boundaries_offline.py:281` "no hand-written tenant filters" | Flags a compliant `UPDATE SET` assignment, via a typo'd escape hatch. See §1.11. |
| `handlers.py:67` `_norm` | Dead code, zero references repo-wide |

**And the benchmark itself.** `test_repo_ingestion_speed_offline.py:541-567`: 12 generated CSS files
of ~40 bytes each, all one domain, `FAKE_LATENCY_S = 0.002` on both the HTTP and LLM fakes, and
`concurrency=4` hardcoded. `files_per_minute` is **computed, never asserted as a value, and never
persisted** — only `> 1000` (and `> 0` on the warm side, which is vacuous). The commit that
introduced it reports a call *ratio* ("1.0 describe and 1.0 capture call per file"), not a time.
Genuinely trustworthy in that file: the 8× batching assertions, the in-flight bounds, deterministic
output order, the non-blocking probe, and the screening-before-describe ordering proof. Not
trustworthy: every throughput claim, and the 2→4 concurrency headroom is documented but never
measured at the default that production actually uses.

---

## 5. The do-not-touch list, re-confirmed

- `compute_dedup_key` (`trace_collector.py:234-245`) — hashes the **pre-redaction** payload on
  purpose, so adding a redaction pattern does not change an already-seen event's key. Change it and
  every replayed collector file re-inserts everything.
- The worker's second `redact_event` pass — redundant for collector records, **load-bearing** for
  OpenHands. A correct fix stamps a marker and proves it; it does not infer.
- The embedding rate limiter's `SELECT … FOR UPDATE` — that lock *is* the guarantee.
- The spend ledger's fail-open on record (`governance.py:583-588`) — deliberate, documented, correct.
- `drain_outbox`'s per-row SAVEPOINT (`:278`) — it is what stops one bad object poisoning 199 good
  ones. The rare design here that is exactly what it claims.
- License screening **order** (before any LLM call). Proven at `skill_ingestion.py:2788-2924`: the
  content screen, the SPDX gate, the admission verdict, and the injection backstop all `return` before
  `:2922`.
- `hydrate_rows`'s unavailable-vs-missing distinction (`shards.py:518-544`).
- `EpisodeAssembly.fingerprint` — never message content, and that is checkable.
- `parse_extraction_response` strictness — fails loudly; and its one leniency (dropping out-of-range
  citations, demoting an element that loses all of them to `uncertainties` at `:281`) is deliberate
  and disclosed.

---

## 6. Suggested order

1. **Fix the commit split** (§0.1) — `git add backend/tests/test_embedder_cache_dedupe_offline.py`.
   One command; restores hard rule 7.
2. **`gemma` → `local` pricing remap** (§1.1). A one-line table change that stops a paid provider
   being recorded as free. Highest value per byte in this document.
3. **The GitHub bearer-on-redirect** (§1.3). Reuse `gh_client.should_attach_credentials`. Add a body
   cap to `_default_http_get`. Both are small and both are credential/memory-exposure.
4. **The `phase1` tenant test** (§1.11). Delete or repoint it. It is actively costing a real signal.
5. **Migration 126: `idx_ingestion_contexts_source_uri`** (§2 Row 2). Additive, idempotent, and it
   removes the only per-event sequential scan in the pipeline.
6. **`handle_normalize_trace_event`**: move the context lookup after extraction, guard on
   `if observations:` (§3.4). Closes an orphan-row leak and 2 statements per zero-observation event.
7. **`persist_artifact_blocks` batching** (§3.2) — 122 → 1 statement per document, inside a
   transaction that already exists, with ids it already mints.
8. **The queue-fencing decision** (§3.6). Either fence `process_pending_jobs` or stop calling it from
   `process_ingestion`. Right now the automatic path has lost-update semantics and one class of crash
   is invisible to every metric.
9. **The registry gap** (§3.7). Add the three registrations to `ingestion_jobs.py` and a test
   comparing the two import paths' key sets.
10. **`write_normalized_trajectory`** (§3.1) — convert to the batched statement now that the stated
    blocker is gone, and add `if not event.dedup_key: raise`.
11. **The `guard` gaps** (§1.2) — `trajectory_semantics.py` and `procedure_extraction/`. Per the
    half-gate rule, each guard lands in the same change as the spend it guards.
12. **The `enqueue_skill_package_jobs` `CASE`** (§2 Row 6) — count
    `jsonb_typeof(payload)='string'` against real data first, then either add the expression index or
    drop the shim. Do not guess.
13. **One dated, real baseline** — a timed corpus run, `INGESTION_AUTO_MODE=global`, throwaway DB.
    Until then, "2-4x" and "10-100x" remain claims.

**Not for unattended work:** the redaction double-pass (touches the chokepoint), the cross-tenant
`write_session_episodes` fix (§1.4 — a lane/semantics question, not a perf one), and
`_derive_family` returning version-bearing family names for Qwen models, which both misprices the
budget and lets three Qwen releases pass `assert_heterogeneous`.
