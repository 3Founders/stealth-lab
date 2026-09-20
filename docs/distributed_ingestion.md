# Distributed ingestion

One worker, any host. Cloud Run, GitHub Actions, Oracle and a laptop run the **same command**;
the queue, leases, retries and idempotency live in Postgres (`ingestion_jobs`, migration 95).
Nothing in the semantic code knows which provider it runs on.

```
python -m app.ingestion.enqueue  skill-package|raw …     # idempotent producer
python -m app.ingestion.worker   --once | --loop         # consumer (any number, anywhere)
python -m app.ingestion.admin    status|failures|retry|reindex|verify-projections|reconcile-goals|verify-dedup|…
```

## Job contract

`job_id, job_type, payload, idempotency_key (unique per job_type), source_id, scope_type,
scope_entity_id, owner_id, visibility, config_version, attempts/max_attempts, claimed_by,
lease_until, started_at, completed_at, last_error, usage(JSON)`.

Status (legacy names kept): `pending` → `processing` (leased; `lease_until`) → `done` |
`retryable_failed` (retry after `run_after` backoff) | `failed` (permanent) | `cancelled`.

* **Lease**: `FOR UPDATE SKIP LOCKED`; `attempts` increments at lease time, so a crash counts.
  An expired lease is re-leased by any worker. Attempts exhausted + expired ⇒ `failed`
  (`reap_exhausted`, crash-loop protection).
* **Fencing**: complete/fail/heartbeat apply only while `claimed_by = me AND attempts = mine`; a
  zombie worker can never overwrite a newer attempt's state.
* **Heartbeat** every `lease/3`; lost lease cancels the running handler.
* **Retry**: transient failures (judge/embedding/shard/DB/network) retry with exponential
  backoff + jitter (`INGEST_RETRY_BASE_SECONDS`, `_CAP_`); contract errors (bad payload, refused
  scope, quality rejection, V0 violation) are permanent. Unknown errors retry until `max_attempts`.
* **Idempotency**: producers pass an `idempotency_key`; consumers rely on canonical unique keys
  (`goals` normalized-name indexes, `procedures.source_key`, `identity_decisions.idempotency_key`)
  and an advisory lock per `source_key`. Duplicate delivery/concurrency/replay create no duplicates
  (`tests/test_distributed_ingestion_e2e.py`).
* **Scope**: a job carries explicit scope. `ingest_skill_package` (writes global public
  knowledge) is refused unless `scope_type in (NULL,'global')` and `visibility in (NULL,'public')`;
  private/org jobs must name an `owner_id`. Handlers receive scope from the job row
  (`payload["_job"]`), never from user-controlled payload text.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CONTROL_DATABASE_URL` (or `DATABASE_URL`) | — | control database; **required** |
| `STEALTHLAB_ENV` | `PRODUCTION` if unset | `TEST`/`STAGING` relax the provider check; PRODUCTION requires a semantic provider and an embedding provider (`--validate-config`) |
| `INGEST_WORKER_CONCURRENCY` | 4 | lanes per process. **DB pool = 2×lanes+4** (each in-flight bundle holds its advisory-lock connection and needs one more) — size `max_connections` for `workers × (2·lanes+4)` |
| `INGEST_LEASE_SECONDS` | 300 | lease length; heartbeat every third |
| `INGEST_JOB_TIMEOUT_SECONDS` | 1200 | per-job wall clock (then retryable) |
| `INGEST_MAX_ATTEMPTS` | 5 | default for new jobs (per-job override at enqueue) |
| `INGEST_RETRY_BASE_SECONDS` / `INGEST_RETRY_CAP_SECONDS` | 30 / 1800 | backoff |
| `INGEST_POLL_SECONDS` | 5 | idle sleep in `--loop` |
| `INGEST_IDLE_EXIT_SECONDS` | 0 | `--loop` exits after this long idle (0 = never) |
| `INGEST_MAX_JOBS` | 0 | exit after N jobs (batch runners) |
| `INGEST_DRAIN_PROJECTIONS` / `PROJECTION_BATCH` | 1 / 200 | drain the projection outbox after each run |
| `INGEST_RECONCILE_GOALS` / `INGEST_RECONCILE_WINDOW_MINUTES` | 1 / 30 | concurrent-paraphrase reconciliation (see dedup doc); window must exceed the longest job |
| `INGEST_WORKER_ID` | host-pid-rand | shows up in `claimed_by` |
| `GEMINI_API_KEY(S)`, `VOYAGE_API_KEY`, `JEV_BASE_URL`, `JEV_API_KEY`, `SEMANTIC_PROVIDER_*` | — | providers |
| `<SHARD>_DATABASE_URL` | — | one env var per registered knowledge shard (registry stores the *name*) |

Exit codes: `0` ok, `1` a job failed permanently, `2` configuration error.

## Concurrency guidance

* Start with 2–4 lanes per process and scale by **process count**, not lanes; each lane is
  mostly waiting on a model call.
* Total DB connections ≈ `Σ workers × (2·lanes + 4)`. Behind PgBouncer transaction pooling use a
  session-mode/direct DSN for the worker (advisory locks and `SKIP LOCKED` need a real session).
* Provider rate limits are enforced by the existing embedding token bucket and the chain's retries;
  an exhausted quota surfaces as retryable failures with backoff, not lost jobs.
* Keep `INGEST_LEASE_SECONDS` > 3× your slowest heartbeat gap and `INGEST_JOB_TIMEOUT_SECONDS` >
  the slowest job; keep `INGEST_RECONCILE_WINDOW_MINUTES` ≥ that timeout.

## Providers (wrappers only)

**Local** — `deploy/ingestion/local/run-worker.ps1` or
`cd backend && STEALTHLAB_ENV=STAGING python -m app.ingestion.worker --once`.

**Cloud Run** — `deploy/ingestion/Dockerfile.worker` + `deploy/ingestion/cloudrun/job.yaml`
(a **Job**: parallelism = independent workers; timeout 3600 s; retries handled by the queue; secrets
from Secret Manager). `gcloud run jobs execute stealth-ingest-worker --tasks 8`. One-shot vs service:
use a Job with `--once`; a service is only needed for `--loop` (min-instances=1, concurrency=1).

**GitHub Actions** — `.github/workflows/ingest-worker-batch.yml` (manual dispatch; inputs: workers,
lanes, max_jobs, job_types, optional manifest to enqueue first; secrets: `CONTROL_DATABASE_URL`,
provider keys). N matrix runners = N independent workers; artifacts hold each worker's summary;
a `report` job prints queue status/failures/projection consistency. Actions is **compute only** —
cancel or re-run freely. (The older `ingest.yml` "Wave 1" workflow is a separate legacy path; see the audit.)

**Oracle** — `deploy/ingestion/oracle/run-worker.sh` runs the same image with an env file; systemd
unit included in the script's footer. More VMs = more workers.

## Failure matrix (what the tests prove)

| Event | Result |
|---|---|
| same source enqueued/delivered twice | one Procedure/Goal/context/claim; both jobs `done` |
| two workers × 3 lanes race on overlapping sources | no duplicates; paraphrased goals reconciled to one |
| worker dies after extraction, before persistence | lease expires → other worker completes |
| worker dies after canonical persistence, before completion/projection | retry re-runs idempotently; outbox drained; verify passes |
| lease expires, zombie wakes up | its complete/heartbeat/fail are fenced (`lost`) |
| crash loop | stops at `max_attempts` → `failed` |
| embedding provider 503 | `retryable_failed` with backoff → later `done`, no duplicates |
| JEV/NLI unavailable with candidates | fail closed (retryable), no duplicate goal; resolves on recovery |
| bad payload / no handler / private scope on a public-only job | permanent, not retried |
| shard unreachable | classified retryable (`ShardUnavailable`); retrieval reports partial |

## Object storage for large raw payloads (`services/object_storage.py`)

Strings above `RAW_PAYLOAD_INLINE_MAX_BYTES` (64 KiB) in a job payload are stored in object storage at enqueue time
(content-addressed by sha256, so duplicates cost nothing); the queue row keeps `{"$blob": {sha256, locator, size}}` and
`raw_objects` records the locator. The worker fetches and **verifies the sha256** before the handler runs.

| Variable | Meaning |
|---|---|
| `OBJECT_STORAGE_URL` | `s3://bucket/prefix` (AWS S3, R2, MinIO, OCI/GCS interop; needs `pip install -r requirements-objectstore.txt`), `file:///shared/path`, `memory://` (tests only) |
| `OBJECT_STORAGE_ENDPOINT_URL` | custom S3 endpoint; standard `AWS_*` variables carry credentials |
| `RAW_PAYLOAD_INLINE_MAX_BYTES` / `RAW_PAYLOAD_HARD_MAX_BYTES` | offload threshold / refuse-without-a-store limit (1 MiB) |

Failures: store outage/throttle -> retryable with backoff; object missing or hash mismatch -> permanent (`failed`);
no store configured and payload above the hard limit -> refused at enqueue. PRODUCTION start-up refuses `memory://`
and requires `OBJECT_STORAGE_URL`. The local `.stealth` cache never uses it.
