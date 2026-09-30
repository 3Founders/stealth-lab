# Ingestion pipeline: analysis + optimization pass 1

Date: 2026-09-27. Lane: unclaimed — **see §6, this needs a board decision before pass 2.**
Scope of the code change: `backend/app/services/trace_worker.py`,
`backend/app/services/ingestion_jobs.py`, `backend/app/services/embeddings.py`, plus two new
proving-test files and one strengthened existing test.

Framing: **Research** (Band 3 measurement) with a Product cost angle. The question is not
"make ingestion fast" — it is "which of the slow things are actually waste, and which are
load-bearing safety work that only looks like overhead."

---

## 0. Read this first: the headline is a measurement problem, not a code problem

**There is no trustworthy wall-clock baseline for any ingestion pipeline in this repo.** The
only throughput number anywhere is `assert cold_metrics["files_per_minute"] > 1000` at
`backend/tests/test_repo_ingestion_speed_offline.py:567` — a *floor assertion*, with no
recorded value and no "before" run, over **12 generated CSS files** at **2 ms fake latency**
with `concurrency=4` hardcoded. It is a smoke test.

Every speedup figure in `.scratch/repo_ingestion_speed_report.md` is a **call-count ratio**,
not a timing ("reduces description provider calls by 83%" is real; "end-to-end expected
speedup is 2-4x" is explicitly labelled *expected* and was never measured).

So this pass deliberately optimizes **round trips and provider calls**, not wall clock. Those
are the quantities actually being removed, they are deterministic, and they can be asserted
offline so the property cannot rot back. A real timed corpus run is still owed — see §7.

---

## 1. What "all the other ingestion pipelines" actually means

15 registered job types, ~14 distinct pipelines. **Only one has ever been optimized**
(`ingest_repo` → `backend/app/services/repo_ingestion.py`). Batchable work concentrates in
three shared modules, which is where a pass pays off multiplicatively:

| Tier | Module | Pipelines sharing it |
|---|---|---|
| A | `skill_ingestion.compile_skill_artifact` (3563 LOC) | skill packages, documents, direct CLI — **3x** |
| B | `trace_worker` (trace/agent_traces/episodes writer) | collector, OpenHands trajectories, transcripts — **3x** |
| C | `observations.py` + `capture_claim` / `capture_procedure` | observations, claims, procedures, skills, repos, consolidation — **7x** |

Cross-cutting: `Embedder` (every capture path, a paid Voyage/Vertex call), `access.py`,
`ingestion_context.py`, `ingestion/queue.py`, `ingestion/worker.py`.

Two corrections to the board's own picture, both verified:

- **`.scratch/build-board.md:4739` says the document path is "completely dormant." That is
  wrong.** `ingest_document` **is** registered (`ingestion_jobs.py:1512`), is listed in
  `queue.py:32 PUBLIC_ONLY_JOB_TYPES`, and is reachable from `app.ingestion.enqueue document`
  and from the ops CLI (`scripts/ops/stealth_ops/ingest.py:26-30`). What *is* genuinely dead
  is `document_ingestion.py::ingest_canonical_document` (185 LOC, zero non-test callers).
- **A real latent bug in the ops CLI:** `manifest_kind` returns only `"document"` or
  `"skill-package"`, never `"repo"`. A repo-shaped manifest is silently enqueued as skill
  packages. Not a perf issue, but it will corrupt any repo-ingest benchmark run through
  `stealth-ops`.

---

## 2. What landed in this pass

### 2.1 `process_collector_file`: 4 round trips per event → 4 per 500-record chunk

The previous pass fixed the pool-acquisition and header-upsert N+1 and then explicitly
declined the rest, in its own docstring: *"Per-event INSERTs are still individual round trips
(real bulk/executemany batching with per-row ON CONFLICT...RETURNING is a further
optimization, not attempted here — flagging that honestly rather than claiming this is fully
batched)."* This is that optimization.

Each event cost BEGIN + `INSERT ... RETURNING` + `INSERT ingestion_jobs` + COMMIT = **4
strictly serial** round trips. At the module's documented 50k-line worst case that is
~200,000. Now: one `INSERT ... RETURNING` + one job `INSERT` per 500-record chunk.

| Records | Before (round trips) | After |
|---|---|---|
| 1,200 | ~4,801 | **13** |
| 50,000 (documented default) | ~200,001 | **~401** |

Design notes worth keeping:

- **`jsonb_to_recordset`, not an 18-argument `unnest`.** One parameter, so the statement
  cannot drift out of sync with its own column list; JSON nulls and mixed types
  (`sequence BIGINT` beside `tool_name TEXT`) are native.
- **The three jsonb columns are declared `text` and cast in the SELECT** (`r.tool_input::jsonb`).
  `_prepare_payload_columns` already holds them as JSON *text*; handing a Python `str` to a
  jsonb column would encode it as a JSON **string literal**. That is the `episodes 50 -> 100`
  double-encode class documented at `RUNBOOK.md:53`, and there is a test pinning it.
- **`inserted` is still derived from RETURNING**, so a `dedup_key` that loses the
  `ON CONFLICT` race is counted as a duplicate and gets no downstream job — identical to the
  old per-event `None`-means-duplicate behavior.
- **Transaction granularity is per chunk, not per event.** A crash leaves a committed *chunk*
  prefix instead of a committed *event* prefix. Both are at-least-once and re-running the file
  is safe because `dedup_key` suppresses what is already committed. The per-chunk unit exists
  specifically so that property is preserved rather than relying on one giant transaction.
- **The redaction chokepoint was extracted, not bypassed.** `_prepare_event_row` is now the
  single place `redact_event` runs, and both the batched path and the single-row
  `_insert_event` call it. See §3 for why this was the dangerous part of the change.

### 2.2 Per-job ADC discovery and client construction

`_vertex_oauth_client` ran `google.auth.default()` (full credential chain, metadata-server
probe on GCP) **and** `credentials.refresh()` (a synchronous HTTPS POST) on **every job**,
and `_general_compute_client` built a fresh `OpenAI(...)` per key per job — a new httpx pool
and a new TCP+TLS handshake, per job.

Now: credentials resolved once per process (refreshed only when invalid) and clients cached
per `(base_url, key)`. `ingestion_jobs.py` had no such cache; `embeddings.py` already had
exactly this pattern at `_vertex_credentials_sync`, so this copies an in-tree fix rather than
inventing one.

### 2.3 The event loop is no longer blocked by job setup

`_general_compute_client()` is still **sync** — three tests monkeypatch it as a sync lambda,
and making it async would have churned them. Instead the three async call sites now do
`await asyncio.to_thread(_general_compute_client)`. Same signature, no test churn, and the
blocking ADC/network work leaves the loop.

This is the fix that makes `--concurrency 8` mean something. `worker.py:229` gathers N lanes
onto **one** event loop in **one** process, so a single two-minute blocking `git show` or
`httpx.get` stalls every other lane.

### 2.4 `Embedder.embed`: hash once, and dedupe misses

Two fixes in the cache pass:

- The cache key hashes the **whole text** and was computed **twice** per text (lookup, then
  store). Now once.
- Misses were **not deduplicated within a call**, so a text repeated *k* times in one batch
  went to the provider *k* times. Ingest batches repeat text constantly (identical claim
  statements, identical boilerplate) and the batch API is rate-limited, so each repeat was a
  paid, rate-limited round trip for a vector already in hand. The experiment-path embedder in
  `embed_cache.py:140` has always deduped here; this closes the gap.

### 2.5 Provider clients cached per configuration

`voyageai.AsyncClient` and `genai.Client` were constructed inside the per-call loop — a fresh
connection pool and TLS handshake to Voyage/Google on **every** `embed()` call. Now cached.

**The cache key covers every constructor argument, not just the api key** — `(api_key,
max_retries)` for Voyage. That is not fastidiousness: `VOYAGE_MAX_RETRIES` is a setting, and a
client cached under an older value keeps retrying the old number of times. I got this wrong
first (keyed on api_key alone) and `test_voyage_embedding_retry.py` caught it immediately —
5 calls where 3 were expected. That is the test suite doing its job, and it is why the
`max_retries` test exists now.

The api key stays in the key because both loops rotate across keys on failure: sharing one
client across keys would send one key's traffic on another key's connection and defeat the
per-key quota independence the rotation exists for.

---

## 3. What was deliberately NOT optimized

This is the part that matters most. The wins and the safety properties are frequently the
same code, and the load-bearing list is long and specific.

| Not touched | Why | Where |
|---|---|---|
| `compute_dedup_key` serialization | The hash is computed from the **pre-redaction** payload *on purpose*, so adding a redaction pattern does not change an already-seen event's key. Change it and every replayed collector file re-inserts everything. Idempotency contract. | `trace_collector.py:234-245` |
| Removing the worker's second `redact_event` pass | It is genuinely redundant **for collector records only** — but `write_normalized_trajectory` feeds the same insert from adapter-built OpenHands events that were **never** redacted, so the second pass is load-bearing there. A correct fix stamps a marker on collector records and proves it; it does not infer. | `trace_worker.py` insert path |
| `redact_event` per-record in the batched path | Kept in `_prepare_event_row`, still once per record. Batching the SQL must not batch or skip the scrub. A test asserts a planted secret is absent from **every** bound row, not just the first. | new test file |
| The embedding rate limiter's `SELECT ... FOR UPDATE` | That row lock *is* the limiter. Only the surrounding round trips were candidates. | `embeddings.py:508-543` |
| The ingest-budget spend ledger | The daily cap is a hard product guardrail; buffering spend records opens a window where real spend is unrecorded. | `ingest_budget` |
| Per-row savepoint in projection drain | Explicitly there so one bad object cannot poison the batch. | `search_projection.py:276-292` |
| License / content screening order | Screening must complete before every LLM call. The repo-local SPDX **allowlist** landed for repo ingestion only; the document/skill path still runs a **denylist** known to pass `NOASSERTION` licenses like CC BY-NC-ND. Making that faster is how it gets deleted. | `screening.py::_SPDX_DENYLIST` |
| `capture_claim`'s per-claim embedding | `capture_claim` refuses to anchor before spending, deliberately ("avoid the embedding spend when there is demonstrably nothing to anchor to"). Batching needs per-claim refusal decisions and a V0-relevant refactor. **Biggest remaining win, needs a founder call.** | `claims.py:298-306` |
| Chunking `handle_consolidate_local_episode` | `INSERT ... RETURNING` does not guarantee output order and `observations` has no unique discriminator to join on. Also: "an LLM failure mid-pass leaves the deterministic Observations already committed" is a documented property. Hoisting the connection is the safe subset; chunking is **UNSAFE-UNKNOWN** pending a call. | `ingestion_jobs.py:2213-2226` |
| `enqueue_skill_package_jobs`' `CASE jsonb_typeof` shim | It defeats `idx_ingestion_jobs_pending_skill_identity` and almost certainly turns an index probe into a per-ref scan — but it exists because double-encoded payloads existed in real rows. Batch around it; do not delete it until `SELECT count(*) WHERE jsonb_typeof(payload)='string'` says zero. | `ingestion_jobs.py:1527-1553` |
| `scope_predicates` / `tenant_transaction` internals | The audit lists these as **verified correct** and as "natural audit targets [that] should not be 'fixed' without new evidence." | `access.py` |

---

## 4. Known correctness defects sitting under the optimized paths

These are **not** performance work and should be sequenced before pass 2, because they make
the fast path look better than it is:

1. **Repo ingestion permanently under-captures after a partial fetch or LLM failure.** Non-200
   fetches are all counted as a benign `fetch` skip, so re-enqueuing the same repo+commit is a
   permanent duplicate. The happy path is idempotent; the partial-failure path is not, and it
   *presents* as a duplicate. Making the happy path cheaper makes this quieter, not safer.
2. **`ingested_artifacts` has overlapping unique indexes with different conflict arbiters**
   (migrations 40, 101, 105). A data combination can raise a `UniqueViolation` the chosen
   `ON CONFLICT` clause cannot intercept — and the batched 64-row artifact write lands
   directly on that table.
3. **Double-encoded job payloads silently disable two paid-job dedupe guards**
   (`ingestion_jobs.py:1711-1723`, `:1891-1911`; correct pattern at `:1530-1552`). Idempotency
   claims in the surrounding comments and docstrings are false.
4. **Three paid extraction paths record spend but never call the budget guard**
   (`skill_extraction/grounded.py:358`, `ungrounded.py:282`, `claim_extraction.py:510`) — a
   direct half-gate violation.
5. **Two hand-rolled `trace_events` INSERTs** (`scripts/distill_banking_docs.py:157`,
   `scripts/recover_distill_trace.py:79`) bypass `trace_worker` entirely: no redaction
   chokepoint, no dedup_key, no enqueue, no tenant transaction.

---

## 5. Test results — and an honest caveat

New: `tests/test_trace_ingestion_batching_offline.py` (13), `tests/test_embedder_cache_dedupe_offline.py` (11).
Strengthened: `tests/test_trace_payload_cap.py` (was asserting over one chosen statement; now over
**every** bound row — checking a single statement is exactly how a redaction or cap regression hides
behind the other 999 rows of a 1,000-row batch).

```
55 passed  (the 6 directly-affected files: 24 new + 31 existing/strengthened)
full offline suite with these changes: 3903 passed, 15 failed, 678 skipped
collection: 4570 -> 4594 (+24, exactly the two new files; no production change affects collection)
```

**The caveat, and it is not small.** I could not get a trustworthy full-suite *before* number.
Reversing only my four files to measure a true baseline produced, 20 minutes later:

```
15 failed, 3871 passed, 678 skipped, 8 errors  in 14:20
```

against my run's `15 failed, 3903 passed, 678 skipped` in **4:46**. A 3x wall-clock difference
and 8 errors appearing in the run *without* my changes cannot be explained by my four files.
The reason is in §6. Until that is resolved, treat "15 failures, unchanged set" as the only
defensible claim here, and do not quote a suite-wide speedup.

---

## 6. BLOCKER: another agent is editing this working tree, in the same subsystem

`git status` at the start of this pass was **not** clean, and the uncommitted work overlaps
this pass's area directly:

Modified: `config.py`, `db/session.py`, `execution/intent_resolution.py`,
`mcp_server/server.py`, `services/agent_decision.py`, `applicability.py`,
`applicability_judge.py`, `claim_extraction.py`, `code_review.py`, `observations.py`,
`procedure_extraction/strategies.py`, `semantic/providers.py`, `step_grounding.py`,
`tests/test_vertex_embedding_offline.py`.

Untracked and new: **`backend/app/utils/aio.py`** and
**`backend/tests/test_nonblocking_llm_offline.py`** — including a test named
`test_vertex_credentials_built_once_and_refreshed_only_when_expired`, which is *precisely* the
optimization I independently landed in `ingestion_jobs.py`. In the baseline run that test
**failed**, and 8 tests in `test_vertex_embedding_offline.py` **errored**.

Consequences, all of which need a decision rather than a workaround:

1. **Two agents are converging on the same fix from different directions.** Whoever lands
   second will either duplicate the work or conflict with it. This needs a board note *now*.
2. **The suite baseline is not a constant.** Counts moved between two runs twenty minutes
   apart on a tree nobody had committed to. Any "before/after" number produced in this state
   is unreliable — including the one in §5.
3. **CLAUDE.md hard rule 4** ("stay inside your lane's granted paths; a commit that edits
   outside them gets reverted") cannot be satisfied: SHIP owns `packaging/**`, and the
   INTEGRATOR's ingestion-speed grant (`.scratch/build-board.md:2850`) is explicitly
   `repo_ingestion.py` only. `trace_worker.py`, `ingestion_jobs.py` and `embeddings.py` are in
   nobody's declared grant. This pass **has not been committed** for exactly that reason.
4. `experiments/harness/` does not exist in this checkout, so 33 of the 62 `packaging/tests`
   failures and 8 docs/CI references to it are stale. Unrelated to this pass, but it breaks
   the packaging baseline too.

---

## 7. Next steps, in order

1. **Founder call on the concurrent work** (§6). Until it is settled, do not start pass 2 —
   the two efforts will collide in `embeddings.py` and `ingestion_jobs.py`.
2. **Fix #1 and #2 in §4 before adding more batching.** Repo-ingest partial-failure
   idempotency and the `ingested_artifacts` index conflict are both underneath code this pass
   makes faster, and #2 is a live correctness risk against the 64-row batch write.
3. **Publish one dated, real baseline** — a timed corpus run over a real repo corpus and a real
   document set, `INGESTION_AUTO_MODE=global`, with the DB on a throwaway container. This is
   audit recommendation #10 and it is still outstanding. Without it, "extreme" has no target.
4. **Remaining safe wins, in value order:** batch the enqueue paths (`enqueue.py`, 2N→2);
   `handle_normalize_trace_event` — move the context lookup after extraction so events
   producing zero observations stop paying for it, add `ingestion_context_id` to the
   `persist_observation` INSERT to drop a follow-up UPDATE, and add the missing index on
   `ingestion_contexts(source_uri, status)` (migration 41+); batch the per-row outbox UPDATE
   in `search_projection.drain_outbox`.
5. **Check the double-encode question at `ingestion_jobs.py:1711` before optimizing near it.**
   If it is double-encoding, the `NOT EXISTS` guard is dead and that is a correctness bug
   wearing a performance costume.
6. **D1 — batch embeddings on the claim write path.** Largest remaining wall-clock win
   (potentially 10-50x fewer provider calls). Needs a real refactor of a V0-gated write path
   plus a founder decision on precomputing vectors. Not a drop-in.
7. **The redaction double-pass**, via an explicit collector marker rather than inference.
   Real CPU win on the hottest path; routes past a reviewer because it touches the chokepoint.
