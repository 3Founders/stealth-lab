# Ingestion pipeline shape

Type: grilling
Status:
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
