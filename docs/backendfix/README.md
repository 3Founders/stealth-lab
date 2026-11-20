# Backend review: architecture, failure modes, and the lost chat

**Date:** 2026-11-20
**Scope:** `backend/` (FastAPI, the MCP server, Postgres), plus the desktop/engine session path where it touches the
backend.
**Method:** reading this repo (paths and line numbers are given) and Exa research on the topics below. Exa is general
research, not a claim about this code; every finding is tied to a file or a query.

---

## 1. The incident first

A chat named **GenRec** disappeared while the person was using it, then came back with a transcript but no live engine
session.

**Immediate cause:** my own verification script. Its cleanup deleted "the newest session" instead of the session the test
had created, so it called `POST /api/sessions/delete` on GenRec. That is fixed: the scripts now delete only the id they
created.

**Why one delete could do that much damage.** Three copies of the same conversation have three different lifecycles:

| Copy | Where | Who reads it |
|---|---|---|
| The app's own store | `~/.stealthlab/chats.db` (SQLite, WAL) | the sidebar and the chat history |
| The engine's old tables | `session`, `message` | the app's `@opencode/client` calls |
| The engine's current log | `session_v2`, `session_message` | the engine itself |

The delete removed the app's row (messages cascade with it) and the engine's old session row. `session_v2` survived, and
that is the only reason the transcript could be rebuilt (196 messages, 105 user + 91 assistant). The app then read the old
table, saw nothing, and showed an empty session.

Other conditions that made it fragile:

1. **Hard delete, no tombstone.** `desktop/app/api/sessions/delete/route.ts` deletes the local row, then asks the engine
   to remove its session. There is no `deleted_at`, no trash, no export before the delete.
2. **A null directory is invisible.** The restored GenRec row had `directory = null`; the sidebar lists only the open
   project's rows (`/api/local/sessions?directory=...`), so no project showed it. That is a silent-hide bug.
3. **A new chat's engine session can be lost on a kill.** The "can we analyze" chat has 4 local messages but **zero** rows
   in `session`, `session_v2`, `message`, `part` and `session_message`. The app was restarted for a build during the
   conversation; the local store kept the text, the engine never wrote a session. Continuing it fails.
4. **No snapshot.** A `VACUUM INTO` or a copy with a WAL checkpoint would have made the restore trivial.

**What was right:** the local store is independent of the engine (text survives an engine upgrade); the engine's log is
append-only with a sequence per session; the room stream reconnects with a cursor (`?after=`), so a dropped stream resumes
without duplicates. The gap is the lifecycle, not the storage.

**Direction:** soft delete with a trash; make `chats.db` the one durable source and treat the engine session as an
execution detail (the new fallback in `desktop/app/api/prompt/route.ts` recreates it under the same id and hands the model
the local prior); never hide a row with a null directory; snapshot before delete; test scripts must never delete by
"newest".

---

## 2. The topics this backend is built on

Read from `backend/` (212 migrations, about 30 packages):

| # | Topic | Where |
|---|---|---|
| 1 | FastAPI and Starlette HTTP, SSE streams, custom MCP routes | `app/main.py`, `app/mcp_server/*_routes.py` |
| 2 | MCP 2.0 (`mcp==2.0.0`, 2026-07-28 spec) and a hand-built tasks extension | `app/mcp_server/server.py`, `tasks_extension.py` |
| 3 | Postgres 15 and pgvector (1024 dimensions), HNSW indexes, FTS | `db/01_ontology.sql` |
| 4 | asyncpg pooling, PgBouncer transaction mode, bounded resets | `app/db/session.py` |
| 5 | Multi-tenancy: row-level security, `scope_predicates`, `tenant_transaction` | `app/services/access.py` |
| 6 | Append-only and bi-temporal history (invalidate and append) | `schema.md`, services |
| 7 | Retrieval: vector and lexical lanes fused by RRF, then a non-compensatory applicability cascade | `app/services/retrieval.py`, `applicability.py` |
| 8 | Model routing: Bayesian ladder, bundled prior, Thompson sampling, reliability target | `app/routing/` |
| 9 | Tool routing: gateway, provider adapters (Exa, Hinoki), registry, policies, egress | `app/tool_routing/`, `app/tool_providers/` |
| 10 | Goal submissions: goal and steps, AI review, runs, suggestions | `app/submissions/`, `app/mcp_server/submission_routes.py` |
| 11 | Economy: credits, rate limiting, cost governance, billing | `app/economy/`, `app/services/governance.py` |
| 12 | Auth: OIDC and JWT, service tokens, actor middleware, MCP OAuth resource server | `app/services/authn.py`, `oauth_resource.py` |
| 13 | Sharded knowledge, search projections, leases | `db/95`, `db/96`, `app/services/search_projection.py` |
| 14 | Observability, hermetic offline tests (a fake pool per file) | `app/observability.py`, `tests/` |

---

## 3. Findings

Severity: **P0** = data loss or breach; **P1** = wrong behaviour under load or failure; **P2** = quality or operations.

### 3.1 Conversation durability (P0)

- **Evidence:** `desktop/lib/localdb.ts` (SQLite in WAL mode, `listSessions` limit 200), `desktop/app/api/sessions/delete/route.ts`
  (local delete, then `client.session.remove`), and the engine schema (`session_v2` with `session_message` cascading on
  delete).
- **Exa:** in WAL mode a commit lives in the WAL until a checkpoint; a copy without the WAL is a different database; a long
  reader blocks checkpoint progress (https://sqlite.org/wal.html). This is why the main-DB-only copy already showed the
  restored state: the restore had been checkpointed.
- **Fix:** (a) soft delete (`deleted_at`) with a trash view and restore; (b) export a chat before any delete (the engine's
  `experimental.session.export` exists); (c) treat `chats.db` as canonical and recreate the engine session from it;
  (d) show null-directory rows under "Unfiled"; (e) snapshot the store on a timer and before any migration.

### 3.2 SSE and long-running streams (P1)

- **Evidence:** `app/mcp_server/room_routes.py::_stream` loops for `STREAM_SECONDS`, yields keepalives, and has **no**
  `request.is_disconnected()` check. `chat_routes.py` returns a `StreamingResponse` over a provider stream. The desktop's
  `next.log` showed `failed to pipe response`, `other side closed`, and a TLS `WRONG_VERSION_NUMBER` while piping to
  `mcp.kell.org.in`.
- **Exa:** Starlette's `EventSourceResponse` listens for `http.disconnect`; without that check a producer can keep writing
  to a closed socket. Backpressure is real (the transport drains), but the producer must stop when the client goes
  (https://techbobby.com/fastapi-starlette-streaming-chunk-size-backpressure-disconnected-client/). The MCP Streamable HTTP
  spec also requires cancellation and timeouts for long calls
  (https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).
- **Fix:** pass `request` into `_stream` and stop on `await request.is_disconnected()`; close the provider stream in a
  `finally` on cancellation; keep `X-Accel-Buffering: no` (already) and add a server-side time limit; log a disconnect at
  debug level, not as a pipe error.

### 3.3 MCP task state is in memory and single-worker (P0/P1)

- **Evidence:** `app/mcp_server/tasks_extension.py` states that task state lives in an in-memory dict: it does not survive
  a restart and does not work across replicas. `assert_single_worker()` enforces `mcp_worker_count == 1` (good, and the
  guard is real). `CLAUDE.md` also says `--workers 1` is load-bearing.
- **Exa:** the 2026-07-28 revision removed protocol-level sessions; state that crosses calls must be an explicit,
  server-minted handle passed as a tool argument (https://blog.modelcontextprotocol.io/posts/2026-07-28/). Long operations
  should return a job handle immediately, expose status and result tools, and accept an **idempotency key** so a retried
  call does not start (and bill) a second job
  (https://www.gmicloud.ai/en/blog/long-running-operations-in-mcp-job-tracking-polling-and-idempotency).
- **Fix:** move task state to Postgres (one row per task: id, owner, status, cursor, payload hash, idempotency key,
  deadline); keep the dict as a fast path but rehydrate from the DB; then allow more than one worker. This is the biggest
  scale blocker.

### 3.4 RLS and tenant binding (P1, mostly right)

- **Evidence:** `app/services/access.py` binds the tenant with `set_config('app.tenant_id', $1, TRUE)` inside
  `tenant_transaction`: transaction-local, parameterized, no cleanup path. `app/db/session.py` sets
  `statement_cache_size=0` when the DSN is a transaction pooler (correct for PgBouncer).
- **Exa:** a session-level `SET` leaks the tenant onto the next borrower of a pooled connection; `SET LOCAL` or
  `set_config(..., true)` is the only safe form
  (https://www.geospatial-api.com/securing-geospatial-apis-authentication-authorization/row-level-security-for-multi-tenant-postgis/setting-tenant-context-in-asyncpg-connections/).
  RLS policies are evaluated per row; `current_setting()` and role functions should be wrapped in a scalar subquery so the
  planner hoists them to an `InitPlan` (https://docs.capydb.dev/docs/guides/postgres/rls-performance).
- **Fix:** audit every `CREATE POLICY` for the `(SELECT current_setting(...))` form (for example `db/136`, `db/139` use
  `sl_org_strict(...)`; check that it is `STABLE` and subqueried); add a test that a pooled connection cannot see another
  tenant's rows after a rollback; keep `tenant_scope` with no default (already).

### 3.5 pgvector and filtered search (P2)

- **Evidence:** `db/01_ontology.sql` uses `VECTOR(1024)` and HNSW, with a good comment on why not IVFFlat. I found **no**
  `hnsw.ef_search` or `iterative_scan` setting anywhere. Retrieval always filters by scope (RLS) and sometimes by a
  coarse-route exclusion, which is the filtered-search case.
- **Exa:** HNSW is the right default; `hnsw.ef_search` is the recall/latency dial; filtered searches can return fewer rows
  than requested, and `hnsw.iterative_scan = strict_order|relaxed_order` keeps scanning after filters remove candidates
  (https://www.paradedb.com/learn/postgresql/tuning-pgvector).
- **Fix:** set `hnsw.ef_search` per pool (for example 100) and enable iterative scans for filtered queries; measure recall
  against a brute-force sample; keep 1024 in sync with `embedding_dimension` (already a hard rule).

### 3.6 RRF fusion depth (P2)

- **Evidence:** `app/services/retrieval.py` has `RRF_K = 60` (the paper's value) and `fuse_rrf` keys on
  `(node_id, table, rank)` (correct: fuse on the canonical id, not the chunk). But each lane is fetched at `top_k * 2`
  (12 for the default `top_k=6`) before fusion.
- **Exa:** RRF needs each list deep enough for cross-lane agreement to appear; fusing only the top 10 means a document at
  dense-rank 15 and lexical-rank 12, exactly the agreement RRF exists to reward, contributes nothing. Retrieve wide
  (100-200 per query), fuse wide, truncate last (https://mmubeen.dev/writing/reciprocal-rank-fusion).
- **Fix:** fetch `max(50, top_k * 8)` per lane, fuse, then take `top_k`. Keep the applicability cascade after fusion (it
  already is). Measure the effect on the retrieval property tests.

### 3.7 Bi-temporal history and projections (P2)

- **Evidence:** the repo uses invalidate-and-append; `db/95` says the `*_search_index` tables are rebuildable projections,
  not a second canonical store; physical foreign keys cannot span databases, so projections reference
  `(stable id, home_shard_id)`.
- **Exa:** a bi-temporal event needs two clocks: `valid_at` (when it happened) and `recorded_at` (when the system learned
  it), and each projection must choose which clock builds it; sorting by `valid_at` breaks write-order assumptions
  (https://quak.com.pl/en/blog/bitemporal-events/). Cross-shard consistency belongs in a **transactional outbox** written
  in the same transaction as the canonical row
  (https://www.databasepartitioning.com/cross-partition-querying-aggregation-strategies/distributed-transactions-cross-shard-consistency/transactional-outbox-for-cross-shard-event-publishing/).
- **Fix:** confirm every bi-temporal table has both clocks and that `recorded_at` is server-set; rebuild projections from
  the outbox or the canonical log after a failover; add a drift test that compares projection counts to canonical per
  shard.

### 3.8 Sharding (P1)

- **Evidence:** `db/95_control_plane_shards_projections.sql` (shard registry, rendezvous-hash weight, `dsn_env` never the
  DSN, projection rows, lease-based ingestion jobs), `db/96_sharded_canonical_writes.sql`. No physical foreign keys across
  databases; `search_projection.verify_projection` is the checker.
- **Exa:** application-level sharding needs an outbox or an idempotent relay and a verification pass; the canonical write
  and its event must commit together, or a crash between them leaves a projection that never catches up.
- **Fix:** write the projection event in the same transaction as the canonical row (outbox), drain with
  `FOR UPDATE SKIP LOCKED`, and alert on projection lag (a per-shard `max(seq)` gap), not only on a manual verify.

### 3.9 LLM gateway: retries and fallbacks (P1)

- **Evidence:** `desktop/lib/pooled.ts` sets `x-stealthlab-fallback-models` (the ladder); the engine tries the next model on
  a provider failure; `backend/app/tool_routing/gateway.py` is the governed path. The plugin's ladder is one layer (the
  gateway).
- **Exa:** retry at exactly one layer; an SDK with its own `max_retries` inside a gateway multiplies calls (9 upstream
  calls where the gateway thinks 3) and breaks its breaker and metrics; a fallback is a behaviour change, not a transparent
  failover: check tools, structured output, context length, data residency and quality before adding one
  (https://zdenovo.com/blog/the-llm-gateway-ii-retries-breakers-fallbacks-and-deadlines).
- **Fix:** set `max_retries=0` on every provider SDK inside the gateway (one retry layer, with the deadline and the
  breaker); log a fallback as a first-class event (which model, why); never fall back on a 400 or a malformed request.

### 3.10 Background tasks and shutdown (P1)

- **Evidence:** `app/main.py` starts `asyncio.create_task(...)` for the goal catalog warm-up, the goal-ranking loop, and
  the search-projection drain. The lifespan `finally` calls `.cancel()` but does **not await** the cancelled tasks (the
  ingestion scheduler is awaited). `app/submissions/api.py` keeps a strong reference set (`_BACKGROUND`), which is right.
- **Exa:** a bare `create_task` is not awaited on shutdown, its failures are invisible, and the loop keeps only a weak
  reference; `BackgroundTasks` waits for response-coupled work, but a long loop needs a lifespan-managed task that is
  cancelled **and awaited**
  (https://async-concurrency.com/network-io-protocol-handling/asgi-servers-and-frameworks/running-background-tasks-in-fastapi-safely/).
- **Fix:** `await asyncio.gather(*tasks, return_exceptions=True)` after `cancel()`; give each loop a name and a health
  counter; show "last pass" in `/health`.

### 3.11 Auth and MCP OAuth (P1)

- **Evidence:** `app/services/authn.py` validates the signature, `iss`, `aud`, `exp`/`nbf` and `sub`, which is the right
  shape. `app/mcp_server/oauth_resource.py` is the MCP resource-server side.
- **Exa:** an MCP server is an OAuth resource server: it must reject tokens not issued for **it** (the `aud` must be the
  server URL or identifier), return `401` for invalid or expired tokens and `403 insufficient_scope` for a missing scope;
  for opaque tokens, introspect and check `aud`; treat an introspection outage as an outage, not as "allow"
  (https://modelcontextprotocol.io/docs/draft/tutorials/security/authorization).
- **Fix:** add a test that a valid token minted for another resource is rejected at `/mcp`; confirm the audience is the MCP
  URL and not a shared application id; keep the service-token path separate (already).

### 3.12 Rate limiting (P1)

- **Evidence:** `app/services/governance.py` enforces with an in-process token bucket per `(scope_key, endpoint)`; Postgres
  is an audit ledger (batched drain, replay-or-block). The docstring itself notes the multi-process caveat, and unlike
  `tasks_extension` I found no `assert_single_worker` for the limiter.
- **Exa:** a per-process bucket behind N workers is N times the limit; the enforcement point must be shared (a DB row with
  an atomic update, Redis, or a single worker). The audit-ledger split is good for the hot path.
- **Fix:** either enforce `mcp_worker_count == 1` for the limiter too (the same boot guard as tasks), or move the bucket to
  Postgres with `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` and a short statement timeout. The fail-closed behaviour
  on a drain failure is right.

### 3.13 Testing and operations (P2)

- **Evidence:** `pytest.ini` fast suite, `slow_tests.txt`, hermetic offline tests with a fake pool per file, and a
  schema-drift test that skips without a database. This is a strength. The gaps are the failure paths above (disconnect,
  worker count, pooled RLS).
- **Exa:** mocks lie for terminal and session state; the same is true for a session store. Test the delete and restore path
  against a real SQLite file, not a fake.

---

## 4. Priorities

| Priority | Fix | Where |
|---|---|---|
| P0 | Soft delete, trash, export before delete; one canonical store; recreate the engine session from it | `desktop/lib/localdb.ts`, `api/sessions/delete`, `api/prompt` |
| P0 | Durable MCP task state in Postgres with idempotency keys; allow more than one worker | `tasks_extension.py`, migration |
| P1 | Stop SSE producers on client disconnect; cancellation and timeouts | `room_routes.py`, `chat_routes.py` |
| P1 | Cross-shard outbox for projections, plus a lag alert | `db/95`, `search_projection.py` |
| P1 | One retry layer; `max_retries=0` inside the gateway; log fallbacks | `tool_routing/gateway.py` |
| P1 | Cancel and await background tasks on shutdown | `main.py` |
| P1 | Shared rate limiter (or enforce one worker) | `governance.py` |
| P1 | Null-directory chats visible ("Unfiled") | `api/local/sessions`, sidebar |
| P2 | RRF retrieve wide (50-200 per lane), fuse, then truncate | `retrieval.py` |
| P2 | `hnsw.ef_search` and iterative scans for filtered vector search | `db/01`, pool init |
| P2 | RLS policy subquery form, plus a pooled cross-tenant test | `db/136`, `db/139` |
| P2 | Bi-temporal two clocks and projection rebuild from the outbox | schema and services |

---

## 5. What is already right (do not regress)

- `set_config(..., TRUE)` inside `tenant_transaction`, parameterized, with no cleanup path.
- `statement_cache_size=0` for a transaction pooler, bounded connection resets, and a command timeout.
- HNSW (not IVFFlat) for embeddings, with the reason written down.
- RRF `k=60`, fusing on the canonical id, and the applicability cascade kept separate from fusion.
- The MCP tasks extension built to the real 2026-07-28 shapes, with its in-memory limit **enforced** by
  `assert_single_worker()` rather than only documented.
- The rate limiter's fail-closed contract (replay or block) and the audit-ledger split.
- The local store independent of the engine; the cursor-based SSE reconnect.

---

## 6. Sources (Exa)

- Starlette SSE and disconnect: https://fastapi.tiangolo.com/reference/sse/ ·
  https://techbobby.com/fastapi-starlette-streaming-chunk-size-backpressure-disconnected-client/
- MCP 2026-07-28 (no sessions; explicit handles): https://blog.modelcontextprotocol.io/posts/2026-07-28/ ·
  https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- MCP security and OAuth resource server: https://modelcontextprotocol.io/docs/draft/tutorials/security/authorization
- MCP production practices: https://thenewstack.io/15-best-practices-for-building-mcp-servers-in-production/
- Long-running MCP jobs and idempotency:
  https://www.gmicloud.ai/en/blog/long-running-operations-in-mcp-job-tracking-polling-and-idempotency
- asyncpg, RLS and PgBouncer:
  https://www.geospatial-api.com/securing-geospatial-apis-authentication-authorization/row-level-security-for-multi-tenant-postgis/setting-tenant-context-in-asyncpg-connections/
- RLS performance: https://docs.capydb.dev/docs/guides/postgres/rls-performance
- pgvector tuning: https://www.paradedb.com/learn/postgresql/tuning-pgvector
- RRF pitfalls: https://mmubeen.dev/writing/reciprocal-rank-fusion
- Bi-temporal events: https://quak.com.pl/en/blog/bitemporal-events/
- Transactional outbox:
  https://www.databasepartitioning.com/cross-partition-querying-aggregation-strategies/distributed-transactions-cross-shard-consistency/transactional-outbox-for-cross-shard-event-publishing/
- LLM gateway retries and fallbacks:
  https://zdenovo.com/blog/the-llm-gateway-ii-retries-breakers-fallbacks-and-deadlines
- FastAPI background tasks:
  https://async-concurrency.com/network-io-protocol-handling/asgi-servers-and-frameworks/running-background-tasks-in-fastapi-safely/
- SQLite WAL: https://sqlite.org/wal.html
- opencode session schema: https://github.com/anomalyco/opencode/blob/f6e0c2d0/packages/core/src/session/sql.ts

---

*Exa returned two rate-limit errors (429) for the first batch of 12 queries; those queries were re-run in a smaller batch.
The repo claims above come from the cited files, read on 2026-11-20.*
