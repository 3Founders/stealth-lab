# Ingestion pipeline shape

Type: grilling
Status: resolved
Blocked by: 06, 07

## Question

What is the shape of the ingestion pipeline, and what runs its asynchronous half?

spec.md's preferred architecture: Claude Code hook → local HTTP/event collector → raw event persistence → normalization → episode assembler → semantic memory compiler. It requires retries, idempotency, duplicate handling, ordering, missing events, late events, provider version changes and schema versioning. It states that **memory correctness must not depend on hooks firing perfectly**, that the raw trace must be recoverable and replayable, that the synchronous agent execution path must not become dependent on the memory compiler, and that memory ingestion should be able to lag behind execution.

The relevant existing facts:

- **There is no background job infrastructure.** No Celery, no RQ, no APScheduler, no cron, no broker. Deployment is a single `uvicorn` web process.
- The whole backend contains exactly **two** `asyncio.create_task` calls, both in the MCP tasks extension. FastAPI `BackgroundTasks` is never used.
- Long-running work today runs **synchronously in-request**: `POST /v1/admin/scan` runs an entire debate loop inline.
- MCP task state is an in-memory dict; `--workers 1` is load-bearing and documented as such.
- `POST /v1/traces` already demonstrates the right ingestion discipline: per-record validation, `ON CONFLICT DO NOTHING`, and per-record rejection so one bad row cannot stall a batch. Worth preserving as a pattern.
- Nothing today reads `episodes`, so there is no existing consumer to keep working.

Decide:

- What runs the async half? Options, roughly in order of weight: an in-process asyncio worker (fits the single-process deployment, dies with the process); a Postgres-backed job table with `SELECT ... FOR UPDATE SKIP LOCKED` polling (no new infrastructure, durable, fits local-first); a real broker (durable and scalable, contradicts local-first simplicity). Local-first deployment compatibility is a stated requirement and constrains this hard.
- Where is the collector, and what is its transport? A hook firing per tool call is high-frequency; an HTTP POST per event from a shell hook has real latency cost inside the user's coding loop. Options include batched POSTs, append to a local file that a worker tails, or a Unix socket.
- What is the idempotency key, and at which stages does it apply — raw persistence, normalization, compilation? Replay means the same raw events get reprocessed deliberately, so "already processed" must be distinguishable from "processing again on purpose."
- What is the backpressure behaviour when ingestion falls behind: drop, sample, block, or queue unboundedly?

Grill these:

- **The hook is inside the user's editing loop.** If the collector is slow or down, does the hook block Claude Code? spec.md says memory must not depend on hooks firing perfectly — which cuts both ways: the hook must also be allowed to fail without the user noticing. What is the hook's timeout and failure behaviour, and what is the reconciliation path that catches what it dropped (ticket 07 investigates the transcript file as that backstop)?
- Is a durable queue actually needed in milestone 1, or is "write raw events to Postgres synchronously, compile on a schedule or on demand" sufficient? The raw write is the only step that cannot be redone; everything downstream is replayable by definition, which is a strong argument for keeping the pipeline simple.
- Ordering: hooks may deliver out of order, but events carry sequence numbers within a session. Is ordering a *storage* concern at all, or purely an assembly-time concern that sorts on read?
- The repo's own precedent — `admin/scan` running a full debate loop inline — is exactly the failure mode to avoid. What structurally prevents the memory compiler from being called on a request path?

## Answer

Every file:line citation below was verified directly against the current code while writing this;
claims sourced from ticket 07's research rather than my own verification are marked as such.

**What runs the async half: a Postgres-backed job table polled with `SELECT ... FOR UPDATE SKIP
LOCKED`, driven by an in-process asyncio worker started in `main.py`'s lifespan.** Not a broker,
not in-memory-only. This is an elimination, not a preference:

- **A real broker** contradicts local-first deployment compatibility, a stated spec.md
  requirement. Eliminated on a constraint.
- **A pure in-process worker with in-memory state** is a pattern this repo already has:
  `InMemoryTaskStore` (`app/mcp_server/tasks_extension.py:140-144`), whose own docstring states
  task state does not survive a restart and does not work across replicas. That is acceptable for
  a foreground MCP tool call a user is watching complete; it is not acceptable for ingestion,
  where spec.md explicitly requires the raw trace be recoverable and replayable. Repeating that
  design here would repeat a known limitation in the one place the limitation actually bites.
- **A Postgres job table** is durable, adds no new infrastructure, works offline, and `SKIP
  LOCKED` makes a multi-worker deployment possible later without redesign. Durability is what
  decides it.

**Collector location and transport: append to a local file, tailed by the worker — not an HTTP
POST per event.** Per ticket 07's research (not independently verified here): hooks are
best-effort with no documented retry, `PreToolUse` defaults to a 600s timeout but
`UserPromptSubmit` is 30s and `MessageDisplay` is 10s, and a timed-out hook is silently dropped
rather than retried. An HTTP POST per tool call places network latency directly inside the user's
editing loop, where a slow or unreachable collector degrades the coding experience — the exact
concern this ticket's first grilling question raises. A local append is fast, needs no network,
and survives the backend being down entirely. It is also the natural place to run ticket 18's
client-side-redaction-before-transmission requirement, which needs a local process anyway.

**Idempotency key and the stages it applies to:** reuse `/v1/traces`' existing, working pattern —
per-record validation with per-record rejection (`app/api/ingest.py:51-61`, `:82`) and
`ON CONFLICT (trace_id) DO NOTHING` (`app/api/ingest.py:69`), so one bad record cannot stall a
batch. Per ticket 07, hooks carry no native event ID, so the collector computes a deterministic
composite key (`session_id` + hook event name + monotonic sequence + payload hash) at append
time rather than trusting one to arrive.

The key applies at **raw persistence only**. Normalization and compilation are keyed by *job*,
not by event — which is what cleanly separates "already processed" from "deliberately
reprocessing": a replay is a new job row over the same immutable raw events, so re-running is
explicit and recorded rather than indistinguishable from a duplicate delivery.

**Backpressure: a bounded local file, dropping oldest, with a recorded drop counter.** Never
block — that would stall the user's coding loop, the failure this ticket explicitly warns
against. Never unbounded — that fills the user's disk. What matters more than the policy choice
is that *what was dropped and how much* is recorded: silent loss is what makes memory correctness
unauditable later.

**Grill 1 — hook timeout and failure behaviour, and the reconciliation path.** The hook must never
block: append and exit, fail silently, non-zero exit only if it genuinely cannot write. Ticket 07
identifies the transcript file (`~/.claude/projects/<project>/<session-id>.jsonl`) as the intended
backstop, and that is the right design. Stated honestly, with ticket 07's own caveat preserved
rather than dropped: whether the transcript is a true superset of hook payloads is an **open
empirical question, not documented fact**, and its per-line schema is explicitly undocumented and
unstable across Claude Code releases. So reconciliation is a real, designed-in path whose actual
completeness is unverified — ticket 11's transcript work is what would answer it.

**Grill 2 — is a durable queue actually needed in milestone 1?** Largely no, and the ticket's own
reasoning holds: the raw write is the only step that cannot be redone, and everything downstream
is replayable by definition. But "write raw events to Postgres synchronously" is not available
here, because the collector cannot assume the backend is reachable (local-first, laptop possibly
offline). So the durable step is the **local append**, and the job table exists to track
compilation work — a smaller and more defensible claim than "a queue is needed for reliability."

**Grill 3 — is ordering a storage concern?** No; purely assembly-time. Sort on read by (session,
sequence). Storage must never reject or reorder on arrival: late events are normal rather than
exceptional here, and rejecting them would directly violate spec.md's missing/late-events
requirement.

**Grill 4 — what structurally prevents the compiler from being called on a request path?** This
deserves a cautious answer rather than a confident one, because discipline is not a structural
guarantee. The only real barrier available is that the compiler's entry point takes a *job row*,
not a payload — so invoking it from a request handler requires first inserting a job and then
polling for it, which is visibly wrong in review. That is a genuine speed bump, not a guarantee
of the kind `GENERATIVE_OP_TYPES` provides for generated change sets. The evidence that discipline
alone is insufficient is already in this repo: `app/api/admin.py:92-94` runs
`await orchestrator.run(trigger_id)` inline, in a loop, over every recorded trigger, inside the
request handler.

**A verified issue this ticket does not mention, which directly affects an in-process worker.**
`app/main.py:27` assigns `app.state.pool = await create_pool()`, but `create_pool()`
(`app/db/session.py:31`) never sets the module-global `_pool` — only `get_pool()`
(`app/db/session.py:41`) does. `close_pool()` therefore operates on a different pool object than
the one the app actually uses, or on `None` if `get_pool()` was never called. An in-process worker
started in the same lifespan and sharing the app's pool inherits this: on shutdown its
connections may not be cleanly released. Small, real, verified, and worth fixing as part of this
work rather than discovering it under load.