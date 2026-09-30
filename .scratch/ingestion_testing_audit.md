# Ingestion and testing codebase audit

Date: 2026-09-27
Audited revision: `24f1b48`, re-verified unchanged at `24c8d23` (the intervening commit touched only `docs/ingestion_sources_plan.md` and `experiments/`, none of the flagged files, and the new plan doc documents no credentials, so the docs findings stand).
Method: six independent read-only subagent audits covering ingestion correctness, worker/queue operations, database/sharding boundaries, security/privacy, test quality, and documentation accuracy. A seventh agent verified the ten highest-severity claims against current code: 8 confirmed, 1 split (containment gap real, sha-bypass refuted), 1 partially confirmed (2 real, 1 refuted).

No application or test files were modified. The only new file is this audit.

## Measured baseline

One audit agent ran the suites with `DATABASE_URL` unset:

| Suite | Result |
|---|---|
| `backend/` | 3,867 passed, 677 skipped, 14 failed, 4,556 collected, 5m57s |
| `packaging/` | 62 passed, 33 failed |
| `experiments/harness/` | Directory does not exist |

The 14 backend failures are concentrated in auth posture, Goal/Benchmark API test fakes, claim visibility, economy authentication overrides, migration upgrade, tenant-SQL hygiene, procedure-embedding hygiene, and startup configuration.

## Executive summary

The ingestion substrate has good bones: the queue's attempt fencing is sound, shard hydration reports unavailable shards separately from missing rows, projection drain uses per-object isolation, and the core provenance model is consistent. The problems are concentrated in five places:

1. Safety controls exist in code but are bypassed on some entry paths.
2. Paid work is not always guarded, deduplicated, or retried correctly.
3. Cross-database writes are not atomic and have incomplete repair paths.
4. Tests often prove strings and call counts rather than the behavior named in the test.
5. Documentation and deployment configuration have drifted enough to misdirect operators.

# P0 — Fix before more production ingestion

## 1. Offline tests can still connect to production through `settings.database_url`

**Confirmed.** `backend/tests/conftest.py:89-98` removes `os.environ["DATABASE_URL"]` after loading dotenv, but `backend/app/config.py:43-45` reads `backend/.env` directly through pydantic settings. The conftest nulls Supabase/OIDC settings but not `database_url` or `search_database_url`.

Any bare `create_pool()` call or `control_database_url()` in an "offline" test can therefore use the real Neon URL from `backend/.env`. The documented `TEST_DATABASE_URL` workflow can also set `os.environ["DATABASE_URL"]` to a disposable database while the settings singleton still points at production.

**Impact:** an offline test or CLI can write to production. This invalidates the suite's central safety claim.

**Fix:** null `settings.database_url` and `settings.search_database_url` in `pytest_configure` when the run is offline. Add a test asserting the settings singleton carries no DB DSN in an offline run.

## 2. `backend/.env.bak.20260927141635` is untracked and not gitignored

**Confirmed in the current working tree.** `.gitignore` covers `.env` but not `.env.bak*` / `.env.*`. The file is a timestamped copy of the real backend environment and can contain the database, MCP, GitHub, and model-provider credentials.

**Fix immediately:** delete the backup and add `.env.*` / `*.env.bak*` to `.gitignore`. Never stage it.

## 3. GitHub bearer token follows redirects to any public host

**Confirmed.** `backend/app/services/ingestion_sources/github_corpus.py:59-80` creates one headers dict containing `Authorization: Bearer $GITHUB_TOKEN` and reuses it for the initial request and every redirect hop. `assert_safe_locator` is an SSRF check, not a host-identity check.

GitHub legitimately redirects raw content to object storage/CDN hosts, so an off-host redirect is expected, not hypothetical.

**Impact:** a redirect to an attacker-controlled or unexpected public host discloses the live GitHub token.

**Fix:** send `Authorization` only to an explicit GitHub-owned host set. Strip it unconditionally on every other redirect hop. Add a redirect test that records headers per hop.

## 4. `/v1/procedures/from_text` sends USER_PRIVATE text to an external embedder without the provider policy gate

**Confirmed.** `backend/app/api/procedures.py:267-278` calls `ingest_skill_md` without an embedder. `backend/app/services/skill_ingestion.py:714` constructs `Embedder()` with no `DataClass.USER_PRIVATE` and no policy pool, so `embeddings.py:299` short-circuits the provider check. The structured sibling endpoint correctly passes `DataClass.USER_PRIVATE`.

The same text path never calls `classify_admission`, despite the admission module naming `ingest_skill_md` as a gated entry point.

**Correction to the initial audit:** the resulting Procedure is not public/unowned; it is forced to `visibility="private"` with owner and user scope. The confirmed problems are missing secret screening and an inert external-provider policy.

**Fix:** run admission screening before capture and construct the embedder with `DataClass.USER_PRIVATE` plus the control pool.

## 5. Collector traces default to public with no owner

**Confirmed.** `backend/app/api/admin.py:218` and `backend/scripts/run_ingestion.py:101` call `process_collector_file(pool, path)` without `owner_id` or `visibility`. The function defaults to `owner_id=None, visibility="public"` and threads both to trace headers and events.

The endpoint is admin-gated and events are redacted, which limits exposure, but the resulting rows are world-readable and unattributable. Erasure cannot match rows whose owner is null.

**Fix:** derive owner/visibility from the authenticated admin principal or collector identity. Make the safe default private/non-null.

## 6. Three paid extraction paths record spend but never call the budget guard

**Confirmed.** These files call `ingest_budget.record_completion` with no `ingest_budget.guard` anywhere in the module:

- `backend/app/services/skill_extraction/grounded.py:358`
- `backend/app/services/skill_extraction/ungrounded.py:282`
- `backend/app/services/claim_extraction.py:510`

Every other paid path guards before spending: embeddings, semantic judging, and repo descriptions.

**Impact:** the daily cap is post-hoc for the most expensive per-call paths. A long job can overspend by many calls before the next job-level budget check.

**Fix:** add the guard in the same change as each spend record. This is a direct half-gate violation.

## 7. Double-encoded job payloads silently disable two paid-job dedupe guards

**Confirmed.** `ingestion_jobs.py:1711-1723` and `:1891-1911` pass `json.dumps(dict)` into a JSONB column whose asyncpg codec already encodes with `json.dumps`. The stored value is a JSON string scalar, so `payload->>'observation_id'` and `payload->>'episode_id'` return null and never match.

The correct pattern already exists in `enqueue_skill_package_jobs` at `:1530-1552`.

**Impact:** repeated claim-promotion and procedure-extraction jobs, each capable of a real LLM/embedding call. Idempotency claims in comments and docstrings are false.

**Fix:** pass the dict, and defensively normalize pre-existing double-encoded rows in the guards.

## 8. The canonical-touch trigger writes control rows into shard databases for Claims

**Confirmed.** `db/95_control_plane_shards_projections.sql:277` initializes `shard='K000'`. The Claim branch at `:283-285` never reads a `home_shard_id` because `knowledge_nodes` has no such column, so the remote-shard guard at `:290-292` can never fire for Claims. The trigger then writes `object_routes` and `projection_outbox` on whichever database received the Claim.

All outbox drainers query the control database. The shard-local rows are never drained and are invisible to control-plane lag metrics.

**Fix:** make the Claim branch shard-aware, drop the trigger on shard databases, or add a control-only guard. Add a two-database e2e test proving a remote Claim writes no shard-local control rows.

# P1 — Correctness and durability

## 9. Prior Procedures are marked stale before replacements are written, with no rollback

**Confirmed.** `skill_ingestion.py:2995-3011` marks every prior Procedure stale before replacements are captured at `:3113`. The operations are independent autocommit statements, not one tenant transaction.

A mid-loop failure leaves the old versions de-listed and only a partial replacement set.

**Fix:** wrap staleness and replacement capture in one `tenant_transaction`, or write replacements first and tombstone old versions only after the new set succeeds.

## 10. The projection outbox can permanently lose an update while every verifier reports clean

**Confirmed.** `search_projection.py:255-259` uses `ON CONFLICT ... DO NOTHING` for pending rows. A canonical writer can conflict with a pending row already locked by the drainer, drop its request, and the drainer then marks the row applied. The projection retains the pre-write state.

Verification compares row presence and shard routes, not projection content, so this staleness is invisible.

**Fix:** use an upsert that refreshes the pending row, or record a canonical version/timestamp and re-enqueue on mismatch in the drainer.

## 11. Remote Procedure capture is three transactions across two databases with no repair path

**Confirmed.** `procedures.py:312-320` writes the route and row route before the canonical shard insert at `:374`. A crash between them leaves permanent dangling routes that route every read to a shard with no row. Detection exists; automatic repair does not.

**Fix:** compensate route writes on shard-insert failure and add `admin repair-routes` for existing dangling rows.

## 12. Cross-shard `source_key` uniqueness does not hold

**Confirmed.** `idx_procedures_live_source_key` is per database, while shard selection uses a freshly generated `procedure_id`. Concurrent ingests of one source can choose different shards and create two live Procedures. The duplicate lookup queries only the losing shard and may raise `TypeError` when the winner is remote.

**Fix:** derive shard placement from the stable source key when present, resolve duplicates across shards, and alert on `live_procedures > 1` for a source key.

## 13. Orphaned `goal_names` claims can recurse indefinitely and re-spend judge calls

**Confirmed.** A remote Goal claims its global name before the canonical shard write. A crash can leave a ghost claim. `find_or_create_goal` treats "held by a live K000 Goal" and "ghost" identically and recursively retries the whole function, including embedding and LLM judging.

**Fix:** make the retry bounded and distinguish live holder from ghost. Add a `goal_names` verifier and repair command.

## 14. Transient extraction failures become permanent `done` jobs

**Confirmed.** `skill_ingestion.py:2929-2948` converts exhausted `SkillExtractionTransientFailure` into `status="rejected"`. The handler returns normally, so the worker marks the job done. A 429 or timeout is indistinguishable from genuine abstention and unreachable by recovery paths.

**Fix:** return a distinct `extraction_failed` status and raise from the handler so the existing retry classifier applies.

## 15. Repo ingestion permanently under-captures after a partial fetch or LLM failure

**Confirmed.** Non-200 fetches are all counted as a benign `fetch` skip. Partial LLM failure is only raised when nothing was captured. Re-enqueuing the same repository and commit is a permanent duplicate.

**Fix:** distinguish 403/429/5xx from 404, and make partial failure on selected files retryable with a stable per-file completion record.

## 16. `ingested_artifacts` has overlapping unique indexes with different conflict arbiters

**Confirmed.** Migrations 40, 101, and 105 create overlapping unique indexes. Writers name different arbiters. A data combination can raise a `UniqueViolation` that the selected `ON CONFLICT` clause cannot intercept.

**Fix:** keep one authoritative identity index and point every writer at it. Add a static test that all artifact inserts name the same arbiter.

## 17. Terminal jobs permanently occupy their idempotency key

**Confirmed.** `idx_ingestion_jobs_idempotency` has no live-status predicate. A permanently failed or cancelled job makes every later submission a silent duplicate.

**Fix:** narrow the index to live states or raise a clear terminal-state error from `enqueue`.

# P1 — Queue and worker operations

## 18. `--loop` never runs periodic maintenance

**Confirmed.** `worker.py:229` gathers lane loops that never return in loop mode. The maintenance block at `:231-266` runs only at process start and shutdown. Oracle and long-running deployments therefore accumulate an undrained outbox and do not reconcile goals/claims.

**Fix:** run maintenance in its own periodic task.

## 19. Cloud Run job arithmetic cannot drain 500 jobs

**Confirmed.** The manifest combines `timeoutSeconds: 3600`, `--max-jobs 500`, and a 3,000-second per-job timeout. At four lanes, a real backlog is killed by the container timeout before the tail is reached.

**Fix:** remove the fixed job cap and exit on idle, or size the container timeout from measured p95 job duration and lane count.

## 20. One transient error outside the handler task can kill every lane

**Confirmed.** `_lane` has no outer exception boundary around lease, payload hydration, complete, fail, or release. One pool-acquisition timeout propagates through `asyncio.gather`, abandoning sibling lanes and skipping pool/telemetry cleanup because shutdown is not in `finally`.

**Fix:** wrap each lane iteration, track consecutive infrastructure failures, and always close pools/telemetry in `finally`.

## 21. Heartbeats renew already-expired leases

**Confirmed.** The heartbeat ownership predicate does not require an unexpired lease. A job past its lease can renew itself, making expired-lease alerts blind to overruns.

**Fix:** require `lease_until > now()` in the heartbeat predicate.

## 22. Graceful shutdown never releases in-flight jobs

**Confirmed.** The signal handler sets an event but does not cancel lane tasks, so the `CancelledError` path that calls `queue.release` is unreachable in deployed configurations.

**Fix:** cancel and await lane tasks on SIGTERM/SIGINT so attempts are refunded and handlers stop promptly.

## 23. `claim_jobs` sets no lease and no max-attempt filter

**Confirmed.** Rows moved to `processing` with null `lease_until` are invisible to lease recovery, stats, reaping, and alerts. A crashed consumer strands up to its batch limit.

**Fix:** set a lease, enforce `attempts < max_attempts`, and alert on `processing` rows with null lease.

## 24. `BudgetExceeded` is classified retryable by the generic fallback

**Confirmed.** `is_retryable` has no explicit `BudgetExceeded` entry. Safety currently depends on the worker checking it before calling the classifier.

**Fix:** classify `BudgetExceeded` as non-retryable in the classifier, while keeping the worker's attempt-refund path.

## 25. Cost recording fails open with no alert

**Confirmed.** `governance.CostGovernor.record` swallows write failures; the cap then reads an under-reported ledger and reports healthy headroom. Metrics read the same ledger, so the dashboard confirms the false health.

The Gemma fallback is also mapped to zero-dollar local pricing in the ingestion budget.

**Fix:** expose an `unreliable` budget state when recording fails and use one provider-family pricing derivation everywhere.

# P1 — Test-suite accuracy

## 26. The main compiler fake accepts any SQL

**Confirmed.** `tests/test_skill_ingestion_offline.py:360-519` returns `[]`/`None`/`"OK"` for unknown `fetch`, `fetchrow`, and `execute` calls. A new or rewritten statement is invisible.

**Fix:** add terminal `AssertionError` branches, then triage every hit. This is the highest findings-per-line testing improvement in the audit.

## 27. The process-global semantic judge can silently defeat the offline guard

**Confirmed.** `identity_resolution._DEFAULT_JUDGE` caches a judge globally. Tests install a lenient all-matching judge and reset it in fixture teardown. An interrupt or setup failure can leave that provider live for the rest of the session, making later identity tests pass vacuously.

**Fix:** reset the global before and after every test with an autouse fixture; expose lenient installation as a context manager.

## 28. Several tests do not test their stated behavior

Confirmed examples:

- `test_ingest.py` claims to pin `ON CONFLICT DO NOTHING` but never inspects SQL.
- `test_skill_ingestion_offline.py` claims to prove a non-null embedding but never inspects the captured parameters.
- Trajectory dispatch fakes absorb all scope/provenance kwargs in `**kwargs`.
- Redaction tests inspect only `conn.calls[0]`, not every emitted statement.
- Observation and episode-selection tests assert prompt substrings or source-text fragments rather than behavior.

**Fix:** assert SQL text, parameter bindings, every emitted call, and observable outputs.

## 29. Three scheduler tests use fixed event-loop spin budgets

**Confirmed.** `test_ingestion_scheduler_offline.py:118-180` yields the loop 200-400 times and asserts state afterward. A loaded runner can fail with an unrelated `NoneType` error.

**Fix:** use `asyncio.Event` plus `wait_for`.

## 30. Shard e2e tests leak environment variables across the session

**Confirmed.** Three shard/claim e2e files assign `os.environ["K001_DATABASE_URL"]` directly without guaranteed restoration. A failed assertion can leave an empty or live shard DSN for every later test.

**Fix:** use `monkeypatch.setenv`.

## 31. Test dependencies and pytest behavior are implicit

**Confirmed.** There is no pytest config, strict asyncio mode is accidental, un-awaited coroutines remain warnings, and `requirements.txt` and `pyproject.toml` have diverged.

**Fix:** add explicit `testpaths`, `asyncio_mode`, `filterwarnings`, and synchronize dependencies.

# P2 — Documentation and packaging inaccuracies

## 32. `experiments/harness` was deleted, but CI and docs still route to it

**Confirmed.** The live rigs are `experiments/swebench`, `swebench_rebench`, `ds1000`, `bigcodebench`, and `experience_transfer`. `.github/workflows/ci.yml` still runs `pytest experiments/harness`, and `CLAUDE.md`/`proj_status.md` still describe the old tree.

## 33. The shippable packaging suite is broken: 33 of 95 tests fail

**Confirmed.** `packaging/src/stealthlab_connect/_bootstrap.py:100` requires the deleted `experiments/swebench_pro/agent.py`. The replacement is `backend/app/execution/coding_agent.py`. Packaging CI is red.

## 34. MCP tool counts are wrong

**Confirmed.** The default v1 surface registers five tools, not 29. The legacy v2 surface registers 50. `README.md` and `commLLM.md` also list product-model tools that do not exist.

## 35. `proj_status.md` describes nonexistent files and stale counts

**Confirmed.** It cites a missing `bootstrap.py`, missing `test_publish_e2e.py`/`test_bootstrap_live.py`, 33 migrations instead of 127, and suite counts roughly half the current measured values.

## 36. `TEST.md` is unrelated legal-research prose

**Confirmed.** It contains no test documentation. `CLAUDE.md` still describes it as a stale test-count document.

## 37. Ingestion operations docs omit required production credentials and misstate exit codes

**Confirmed.** `INGEST_SERVICE_TOKEN`, `SERVICE_TOKEN_KEYS`, General Compute, GitHub, and Vertex settings are absent from the main ingestion setup docs. Exit code 3 for budget-stop is undocumented; credential failures exit 1, not 2.

## 38. The docs disagree on the number of accepted pre-existing failures

**Confirmed.** `ingestion_problems.md` says 8-9, the launch runbook table says 27, and the measured offline baseline is 14. Use one dated source of truth.

# Verified clean

These are natural audit targets and should not be "fixed" without new evidence:

- Queue attempt fencing by `(job id, claimed_by, attempts)` is correct.
- `SKIP LOCKED` leasing is sound.
- Shard hydration distinguishes unavailable shards from missing rows.
- Projection drain isolates per-object failures with savepoints.
- Migration checksum enforcement and transaction-local ledger writes are correct.
- `scope_predicates` and `tenant_transaction` themselves behave as documented.
- The SSRF checks in the URL document adapter cover redirects and body size.
- Claim/procedure identity replay keys exist, even though some callers fail to use them correctly.

# Recommended fix order

1. Remove and ignore `.env.bak*`; close the `settings.database_url` offline-test hole.
2. Fix the GitHub redirect token leak and the USER_PRIVATE provider-policy bypass.
3. Add budget guards to the three extraction paths; make collector ownership non-null/private by default.
4. Fix the double-encoded paid-job payloads and the shard-local Claim trigger.
5. Make staleness/replacement atomic; fix the projection outbox lost update.
6. Repair cross-shard Procedure source-key placement and route compensation.
7. Make the worker loop maintenance periodic; fix lane exception containment and shutdown release.
8. Harden the shared fakes: strict unknown-SQL errors, global judge reset, every-call redaction assertions.
9. Fix the documentation/package blockers: deleted experiment tree, packaging bootstrap, MCP counts, nonexistent files, credential/exit-code docs.
10. Re-measure and publish one dated suite baseline.

# Suggested first proving-test batch

These tests can be written without new schema and would prevent most of the highest-severity regressions:

- Offline run leaves `settings.database_url` and `settings.search_database_url` empty.
- No `.env.bak*` or `.env.*` file is trackable.
- GitHub redirect to a non-GitHub host receives no Authorization header.
- `/v1/procedures/from_text` rejects screened secrets and constructs a USER_PRIVATE policy-gated embedder.
- Grounded, ungrounded, and claim extraction call `guard` before `record_completion`.
- Duplicate promotion/extraction enqueue makes no second paid job.
- Remote Claim creation writes no shard-local `object_routes` or `projection_outbox` row.
- A failed replacement capture leaves the prior live Procedure un-staled.
- A projection enqueue racing the drainer leaves either a new pending row or a current projection.
- `CompilerFakePool` raises on an unknown statement.
- Every SQL statement emitted by redaction contains no raw secret, not only the first.
