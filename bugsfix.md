# bugsfix.md — agent-harness failure catalog vs. StealthLab's own harness

Source: a pasted catalog of ~55 known failure modes from general-purpose agent
harnesses (ticket refs like `workers#507`, `iii#2006` are external — not this
repo's issue tracker; they're cited here only as-given, for traceability back
to the original list). This doc does **not** claim to fix any of them in
whatever system they originally happened in. It answers a narrower, real
question: **for each failure class, does StealthLab's own execution harness
(`backend/app/execution/durable_run.py`, `durable_resume.py`,
`goal_execution.py`, `app/mcp_server/tasks_extension.py`, `app/stealth/
journal.py`/`atomic.py`) already have a real, existing mechanism that would
catch or prevent it — or is it a real, disclosed gap.**

Every ✅/⚠️/❌ below is backed by an actual file/function read, not inferred
from the bug description alone. Where a claim needed the code checked, it was
checked.

Legend: ✅ real, existing protection · ⚠️ partial protection / different
shape than the failure · ❌ real gap, nothing here catches this · — n/a,
doesn't map to this system's architecture.

---

## Crash / dangling state / poisoned queue

- **crash mid tool call left a dangling call with no result that poisoned
  every later request** (`workers#507`, `workers#630`) — ✅ Every node claim
  carries `worker_id` + `lease_expires_at`. `_drive()` in `durable_run.py`
  detects a node stuck `running` past its lease and either safely re-arms it
  (pure node) or parks the whole run at `paused` — **never silently retried**
  if it's side-effecting. `execution_runs`/`execution_run_nodes` (Postgres)
  are the durable source of truth, not in-process state, so nothing "poisons"
  a later request the way in-memory dangling state would.
- **resumed turn raced the worker booting and called a function that wasn't
  registered yet** (`workers#507`) — ⚠️ Lease ownership prevents a second
  worker from stealing a run/node another worker legitimately holds. But
  there's no equivalent guard for "the MCP process itself just booted and a
  tool isn't registered yet" — `tasks_extension.py` only asserts worker
  *count* (`assert_single_worker`), not tool-registration readiness. Real,
  narrower gap than #1.
- **no durable queue for turns so wedged ones sat in running forever**
  (`workers#464`) — ✅ for durable runs (Postgres tables ARE the queue,
  `run_status()` gives full visibility, `resume_run`/`retry_node` are
  explicit unstick paths). **❌ for MCP task state specifically** —
  `tasks_extension.py`'s own docstring admits it plainly:
  `InMemoryTaskStore` "does not survive a server restart." This is the one
  place in this codebase that discloses the gap outright rather than papering
  over it, and it's boot-gated (`assert_single_worker` refuses to start a
  second worker that could split-brain this).
- **poison message reflushed on every reconnect** / **queue faithfully
  redelivering the poison** (`workers#1175`) — ✅ `_node_claim`'s
  `WHERE status IN ('pending','failed','resumable')` plus `max_attempts`
  means a node that keeps failing lands in `failed` and **stops** being
  auto-retried (`_drive`: `if status == "failed": continue`). Requires an
  explicit `retry_node` call — never auto-redelivered in a loop.
- **startup sweep rerunning the same doomed call after every boot** — ✅ same
  mechanism as above; a `failed` node is excluded from the sweep by
  construction, not by an added special case.
- **one 18mb directory listing wedging the whole agent runtime through
  restarts** (`workers#1175`) — ❌ No payload-size bound found on node
  input/output in the reviewed modules. The lease/timeout model (below) would
  eventually recover a *stuck* worker, but nothing here caps or rejects an
  oversized single payload before it causes the stall in the first place.
- **10mb session log replayed synchronously under a mutex** (`workers#1119`)
  — ❌ No equivalent found. Real gap if StealthLab ever grows a comparable
  "replay a large log under a lock" path.

## Malformed / truncated output treated as success

- **cut stream parsed as null args and got reported as a successful empty
  turn** (`workers#878`) — ✅ This is a *fixed*, documented instance of
  exactly this bug class in `tasks_extension.py`: "if `.cancel()` races ahead
  of this task's very first scheduling tick, the wrapped coroutine can be
  torn down WITHOUT EVER EXECUTING A SINGLE LINE OF ITS BODY" — fixed via
  `add_done_callback`, which fires regardless of whether the body ran.
- **seven providers treating any connection close as done** (`workers#878`)
  — ⚠️ Related but not identical — no provider-abstraction layer was in the
  reviewed files to check this specific claim against. Flag as unverified,
  not claimed as caught.
- **wake notifications cutting json at 600 chars, mid array** (`workers#1167`)
  — ❌ No truncation-detection on serialized payloads found.
- **large tool calls ending with incomplete args, then re emitted**
  (`workers#1123`) — ✅ same class as the first item above — the
  done-callback fix is specifically about not treating an incomplete/never-
  executed call as a real result.
- **null serialized as "no result", sdk saw undefined, infinite loop** — ✅
  same mechanism as above.
- **five sub agents hitting the turn limit, all reporting completed** — ⚠️
  `RETRYABLE_ERROR_CLASSES`/`NON_RETRYABLE_ERROR_CLASSES` (`classify_error`)
  distinguish real failure types, but "hit a limit and reported success
  anyway" is a status-honesty bug at the caller layer, not something the
  retry classifier itself would catch.

## Retry budgets and compaction

- **retry budget of 1 killing turns one step from done** (`workers#552`) —
  ✅ `RETRYABLE_ERROR_CLASSES` vs `NON_RETRYABLE_ERROR_CLASSES` mean a
  `timeout`/`network`/`rate_limit`/`conflict` retries, `validation`/`auth`/
  `logic` fails fast — attempts aren't burned uniformly. `retry_node(force=
  True)` is also an explicit operator override to bump `max_attempts` on an
  already-exhausted node rather than losing the work outright.
- **compaction returning an empty context** (`workers#552`) — — Not
  applicable to this system's architecture as reviewed: there's no LLM
  context-window compaction step in the execution harness (its "durable
  state" is Goal/Procedure graph progress, not a conversation window). Real
  caveat: nothing here would catch it if a compaction step were added later.
- **16 compactions, then a 5m char hook result, "compaction succeeded"** —
  same — not applicable / unverified against this codebase.

## Child / sub-agent lifecycle

- **child agent death silent by construction** (`workers#552`) / **parent
  parked forever waiting on a child that died out of band** — ⚠️ Real,
  partial. `_record_child_run_completed_on_parent` fires when a child run
  reaches a real terminal status, and `recursion_guard.py` enforces a
  `WallClockBudgetExceeded` over the whole ancestor chain. But a child that
  crashes and is simply **abandoned** (never resumed, never terminal) leaves
  the parent in `WAITING_CHILD` with **no lease/timeout on the wait itself**
  — only the child's own internal node leases would eventually go stale, and
  nothing automatically surfaces that to the parent. This is the single
  clearest concrete gap this review found with a name attached to it.
- **lock order deadlock between parent retasking a child and the child
  finishing** (`workers#973`) / **ci only dodged the deadlock by scheduling
  luck** — ⚠️ No named deadlock-prevention primitive exists, but the design
  shape lowers the risk structurally: per the `durable_run.py` module
  docstring, "each node transition is its OWN short transaction and the
  `run_node` callback runs OUTSIDE any transaction" — specifically so a
  slow/blocked operation can't hold a lock across a parent/child boundary.
  Lower exposure than a typical shared-mutex design, but not a proven
  guarantee.
- **sub agents not inheriting reasoning effort** (32 min → 18 min, $1.16 →
  $0.50, same score) / **unknown model fell to an 8k window, sub agents had
  about 6k usable** — ❌ No mechanism found at all — zero matches for
  reasoning-effort or model-capability inheritance anywhere in `app/`.
  `recursion_guard.py` governs *whether* a child run may start (cycle/depth/
  budget), never *what config* it inherits. Real gap, though it's worth
  noting StealthLab's "child run" today is a re-executed Procedure, not an
  LLM sub-agent with tunable inference settings — so this may not be a live
  exposure yet, but there's no guard if it becomes one.

## Duplicate execution / stop races

- **two parallel approvals both waking one turn, shell command executed
  twice** — ✅ `start_run`'s `request_id` idempotency key: a second call with
  the same `request_id` returns the *same* run, including a race-handled path
  (`UniqueViolationError` → return the winner, not a duplicate). Documented
  explicitly as "an idempotency key, not a lookup convenience."
- **stop button clobbered by a stale write from the step it was stopping**
  (`workers#313`) — ✅ `tasks_extension.py::_handle_cancel` is explicit that
  `asyncio.Task.cancel()` is cooperative, not a guarantee — disclosed, not
  silently assumed. On the durable-run side, every status transition is
  `WHERE status IN (...)`-guarded (`tag != "UPDATE 0"` checks), so a stale
  write can't silently clobber a newer one — it becomes a no-op instead.
- **stop command vanished because registrations weren't replayed after
  reconnect** — same root cause as the in-memory task-store gap above (❌,
  disclosed).
- **approval hook registered 171 times because the instance count lagged** /
  **resuming past only the first duplicate hook, held forever** (`workers#797`)
  — ❌ No equivalent registration-dedup mechanism found for this shape of bug.

## Timeouts

- **provider stream stalled mid delta, keepalive pings counted as activity**
  / **120s idle guard that never fired, 300s hard kill with an opaque error**
  / **no read timeout on upstream clients** — ⚠️ `NODE_LEASE_SECONDS=300` /
  `RUN_LEASE_SECONDS=600` mean a hung call doesn't wedge the *system*
  forever — the next `_drive()` pass detects the stale lease and recovers.
  But this is detection-on-resume, not a hard kill of the in-flight call
  itself, and it wouldn't distinguish a real keepalive from a stalled stream
  faking activity — that's a real, unaddressed gap within the lease window.

## Token / cost accounting

- **cached tokens double billed on six providers** (`workers#740`) — ❌ No
  cache-vs-fresh token distinction found anywhere in `app/`. `record_run_usage`
  does real atomic accumulation (`tokens_used = tokens_used + $2`, safe under
  concurrency), but if a provider bills cache reads separately, nothing here
  separates that out.
- **file reads counted twice so we saw phantom context overflow** /
  **41% more tokens, 95% of it repeated context cache reads** (`workers#552`,
  `workers#1123`) — ❌ same gap as above.
- **first turn system prompt 14 tokens different, second cache write every
  session** — ❌ same gap; no cache-key stability tracking found.

## Observability / tracing volume

- **a span emitted per token plus a persisted state write per event**
  (`workers#205`) / **generated code polling state 140 times a second, 29.7k
  spans in one trace** / **761mb of traces at 20mb a minute in a quiet
  session** — ✅ `app/telemetry.py` has a real, configurable
  `stealth_trace_sample_rate` setting plus a tail-keep exporter — sampling is
  a named, real mechanism here, not hardcoded capture-everything. Doesn't
  prevent a pathological polling loop from generating events in the first
  place, but does bound how much of it gets persisted/exported.
- **trace cache eviction went quadratic under a lock every thread needed,
  kill -9** — ❌ No equivalent cache-eviction path was in the reviewed files
  to check against; flagged as unverified/likely gap.

## State ownership / desync

- **two components each persisting a registration token, desync no restart
  could fix** — ✅ This is the one category with a *named design decision*
  against it. Migration 36's header explicitly explains why
  `execution_runs`/`execution_run_nodes` are kept separate from the
  append-only `executions` table rather than a second competing mutable
  copy: "NOT a second scheduler... this records what happened."
  `finalize_after_verification` is documented as "the only function that may
  advance a run OUT of `awaiting_verification`" — one real write path per
  state transition, guarded by `tag != "UPDATE 0"` so a duplicate finalize
  can't run twice. This discipline recurs across the file; it's a deliberate,
  enforced pattern, not incidental.
- **registrations lost when the runtime reloaded a different worker**
  (`workers#666`) — split: ✅ durable runs survive a full process restart
  (that's the entire point of the Postgres-backed migration-36 design); ❌
  MCP task registrations do not (same disclosed gap as above).
- **two runtimes on one port, function ownership flapping, oom sigkill** — ❌
  No port/ownership-collision guard found; `assert_single_worker` prevents
  *multiple MCP workers in one process group* from splitting task state, but
  doesn't address two separate runtimes contending for one port.

## Not directly checked / out of scope for this pass

These don't map cleanly onto the execution-harness files reviewed, or need a
different subsystem checked before answering honestly rather than guessing:

- agent uninstallable because its dependency graph had 65 edges and the limit
  was 64 (`iii#2006`) — a packaging/install-time constraint, not execution
  runtime; not reviewed here.
- websocket reset loop every 2.8 seconds — no websocket transport layer was
  in the reviewed files.
- agent restarting the very process hosting its own turn — no self-restart
  logic found or ruled out; would need the process-supervision code checked.
- "confirm before restarting" in the prompt, not a guarantee — a real,
  general principle (prompt text is not a control-flow guarantee) rather
  than a specific mechanism to check for — worth keeping in mind as a design
  rule generally, including in this codebase's own prompts.
- malformed deny rule failing open — security-policy parsing wasn't in the
  reviewed files; this is a category worth a dedicated look (fail-open on a
  malformed deny rule is a serious class of bug if it exists anywhere in
  `app/services/access.py`'s scope predicates — not confirmed either way
  here).
- two ids that differed by one character colliding, second message silently
  dropped — no ID-collision handling was in the reviewed files.
- 79% of tool schema tokens were prose / any result under 2000 chars immortal
  in context, 63k–76k token floor / no completion trigger so agents used
  `sleep 60` as a wait primitive / 48% of root turns were status polls — all
  four are LLM-context/prompt-engineering efficiency issues, not durability
  bugs; out of scope for the execution-harness files reviewed here.
- failure cleanup timing out and logging skipped, same as a no-op — cleanup/
  logging timeout behavior wasn't isolated in the reviewed modules.

---

## Bottom line

StealthLab's durable-run layer (`durable_run.py`) is the strongest part of
this story — lease-based crash recovery, idempotency keys, guarded state
transitions, and a documented "one real write path per transition"
discipline cover a solid majority of the crash/dangling-state/duplicate-
execution/desync failure classes above, with real citations, not just design
intent.

The honest gaps, in priority order:
1. **MCP task state is in-memory** — explicitly disclosed in the code itself,
   boot-gated to a single worker, but a real restart-loses-everything
   exposure for anything routed through `tasks_extension.py` rather than the
   Postgres-backed durable-run path.
2. **No cache-vs-fresh token/cost accounting** — real risk of the exact
   double-billing/phantom-overflow bugs in the source list if StealthLab's
   own LLM calls hit a caching provider.
3. **Silent-abandonment of a child run** — the parent has no timeout on
   "child never checked back in," only on the child's own internal leases.
4. **No sub-agent config inheritance mechanism** — not yet a live exposure
   (current "child run" = re-executed Procedure, not a tunable LLM sub-agent)
   but nothing would catch it if that changes.
5. **No payload-size bound** on node input/output — the 18mb-listing class of
   bug has no guard here.
