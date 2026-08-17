# Canonical trace model

Type: grilling
Status: resolved
Blocked by: 01

## Question

What is the canonical, provider-independent trace and event model, and how does it relate to the existing `traces` table?

spec.md requires a canonical format carrying identity (trace/episode/session/parent-trace/parent-event/agent/actor/provider/provider_version), intent, environment, events (sequence, timestamp, type, actor, tool, tool call id, parent tool/agent relationship, tool input, tool output, success/failure, duration, permission state, error, provenance, raw payload reference), artifacts, state, outcome, and evidence. The raw provider payload must remain recoverable. The normalized schema must not be provider-specific.

The relevant existing facts:

- `traces` is **one span-like row**, not a trajectory: `trace_id` TEXT PK, `timestamp`, `task_node_id` (FK), `actor_id`, `action_type` (3 values), `outcome` (3 values), `cost`, `latency_ms`, `parent_trace_id` (a string, no FK), `ingested_at`.
- Field names mirror OpenTelemetry GenAI semantic conventions, mapped in `OTEL_ACTION_MAP` with `OTEL_SPEC_VERSION = "1.30.0-experimental"` pinned in `backend/app/models/trace.py`.
- The `task_node_id` FK is load-bearing and hostile to external ingestion: an external agent cannot self-register work, because a task node must already exist.
- `POST /v1/traces` does per-record validation with `ON CONFLICT (trace_id) DO NOTHING` and per-record rejection so one bad row cannot stall a batch. That idempotency discipline is worth preserving.
- `traces` has two real consumers: `triggers.py` (threshold rules over error/rework/cost/latency windows) and `eval/layer2.py`.
- `episodes` exists with a `content`/`content_ref` split explicitly intended as the non-lossy raw audit layer — and is unused for that purpose.

Decide:

- Extend `traces`, add a raw-event table beneath it, or introduce a new normalized-event table and reposition `traces` as a compatibility view? All three preserve existing data; they differ in what breaks.
- Where does the raw provider payload live — `episodes.content_ref` with blob storage, a JSONB column, or a separate raw table? What is the retention/size story? Tool outputs from a coding agent are large.
- What is the identity/dedup key for an event, given hooks can fire twice, out of order, or not at all?
- How is schema version carried, so a provider changing its payload shape does not corrupt normalization?

Grill these:

- Should the canonical format **derive from** OTel GenAI semconv rather than being invented? The repo already pins a version and maps to it. Ticket 08 researches whether the current semconv actually covers what spec.md needs — but this ticket owns the decision of whether to follow it, extend it, or diverge deliberately.
- The `task_node_id` FK: drop it, make it nullable, or keep it and require a synthetic task node per ingested session? Each has consequences for `triggers.py`, which currently joins through it.
- Is an "event" and a "trace" one table with a type discriminator, or two tables? spec.md treats TRACE as "the detailed causal execution history" and lists events as its contents — which reads as trace being a container, not a row. The existing table uses `trace_id` as a *row* id.
- What is the minimum viable event set for milestone 1? spec.md lists ~14 agent event types; not all need first-class handling on day one.

## Answer

**Structure: three tables, not "extend `traces`" or "`traces` as compat view."** Add two new tables
alongside the existing, untouched `traces`:

- **`trace_events`** — the atomic event log (spec.md's Events/Agent-events groups: tool
  invocation, tool result, model call, etc.). One row per event.
- **A new trace-header table** (name TBD, deliberately not reusing `traces` — see the naming
  note below) — one row per causally-connected execution (roughly: one `invoke_agent` turn,
  or one subagent's complete run), rolling up start/end, outcome, cost, and a reference to its
  constituent `trace_events` rows.
- **`episodes`** (existing, unchanged schema) — becomes the real target for episode assembly,
  linked to trace-headers the same way `episode_links` already links to other node types today.

**Why not one table with a discriminator, or two:** spec.md lists `trace_id` and `episode_id` as
*separate* Identity fields on every event — if trace meant "this episode's events, queried
together," you wouldn't need a distinct `trace_id` per event, you'd group by `episode_id`
directly. That implies trace is a real grouping *below* episode, not identical to it: an episode
is made of multiple traces, not one. Ticket 08's own research corroborates the boundary:
`gen_ai.conversation.id` correlates multi-turn work by a flat attribute value, not by a trace
containing smaller traces — one OTel trace naturally scopes to one causally-connected operation
(one turn / one subagent run), matching spec.md's episode-boundary heuristics (subagent
hierarchy, git commit, session boundaries), all of which describe grouping *across* multiple
turns, never within one.

A trace-header row and an event row also have genuinely different shapes and volumes (low-volume
header vs. high-volume per-tool-call), and the repo's own established pattern for genuinely
different shapes is separate tables (`knowledge_nodes`/`task_nodes`/`edges`/`episodes` are four
tables, not one with a type column) — `edges`' single-table-with-discriminator design works
*because* every edge type shares identical columns, which isn't true here.

**Naming note, flagged rather than silently resolved:** the *existing* `traces` table's row-level
meaning (one flat span/outcome record) is actually closer to spec.md's **EVENT** concept than to
spec.md's **TRACE** concept (a causal history/container). Worth an explicit decision — separate
from this ticket — on whether that's confusing enough to warrant a rename, or left as-is since
`traces` keeps serving its own unrelated purpose below.

**Existing `traces` table: untouched, including its `task_node_id` FK.** It keeps serving its
real, current consumers exactly as they are:
- `triggers.py:71` (`GROUP BY task_node_id` over a time window, threshold rules)
- `eval/layer2.py:196-199` (`WHERE task_node_id = $1`)

Neither needs richer event data, and forcing `trace_events`/the new header table to share
`traces`' schema (built for company-task-execution monitoring) would recreate the exact problem
the ticket names as "hostile to external ingestion" — a Claude Code session on a user's laptop
has no reason to require a pre-existing company `task_node`. `trace_events` and the header table
simply don't carry that FK at all; two tables, two purposes, no retrofit.

**Raw payload storage:** reuse `episodes.content_ref`'s existing pattern (Supabase Storage path)
rather than inventing a second mechanism. Small events inline in JSONB on `trace_events`; large
tool outputs (spec.md's own callout: "Tool outputs from a coding agent are large") get a
`raw_payload_ref` pointer field, same idiom already proven at the episode level.

**Event identity/dedup key:** reuse `POST /v1/traces`' existing, real, working idempotency
pattern (`ON CONFLICT (trace_id) DO NOTHING` + per-record rejection) rather than a new mechanism.
For the Claude Code adapter specifically: ticket 07 already found hooks carry no native event ID
or schema-version field, so the dedup key can't be trusted from the payload — the adapter
computes a deterministic composite key at normalization time (e.g. `session_id` + hook name +
payload hash), not something assumed to arrive pre-formed.

**Schema/extractor versioning:** this repo stamps its own schema/extractor version at
normalization time, on every `trace_events` row — forced by ticket 08's own finding that
`OTEL_SPEC_VERSION` is already unresolvable against the current spec and no provider supplies a
version field at all. Matches spec.md's explicit ask directly ("Claim C17 was produced by
extractor version X from trace E42").

**Derive vs. diverge from OTel semconv:** adopting ticket 08's own recommendation as the decision
here — hybrid. Derive naming/shape for the LLM-call/tool-call-shaped slice of events (tool
id/name/args/result, token counts, error type — real convergence, per ticket 08 §2/§6). Diverge
deliberately, under this repo's own namespace (not `gen_ai.*` — per ticket 08 §5's citation of
OTel's own anti-squatting guidance), for Identity/Intent/Environment fields with no semconv
equivalent (episode_id, cwd, repo/branch/commit, permission state).

**Minimum viable event set for milestone 1:** scope to what episode assembly and tool-call
tracking actually need — session start/end, tool invocation/result/failure, task created/
completed (the subset ticket 07 confirmed are real, current Claude Code hook events). Defer
retry, compaction, and permission request/denial as later increments; spec.md itself says not
every event type needs first-class handling on day one.

**One genuine open question, not resolved here:** the exact boundary rule for what starts a new
trace. "One turn" is the OTel-aligned default, but "one subagent's complete run" is also
defensible and not always the same boundary (a single turn can spawn a subagent whose own work
might deserve its own trace). Treating this the same way spec.md treats episode boundaries itself
— multiple heuristics plus later semantic segmentation, not one rigid rule now.