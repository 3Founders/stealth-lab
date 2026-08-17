# Canonical trace model

Type: grilling
Status: claimed
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
