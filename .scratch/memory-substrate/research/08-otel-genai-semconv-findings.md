# Findings: OpenTelemetry GenAI semantic conventions vs. spec.md's canonical trace

Evidence document for [ticket 08](../issues/08-otel-genai-semconv.md). This is
evidence-gathering only — the derive/extend/diverge call belongs to
[ticket 06](../issues/06-canonical-trace-model.md) (not yet resolved as of this
writing); nothing here is a decision.

**Summary:** the GenAI semantic conventions have been split out of the main
OpenTelemetry semantic-conventions repo into a dedicated, still pre-1.0
repository (`open-telemetry/semantic-conventions-genai`) as of June 2026. Every
`gen_ai.*` signal remains stability level "Development" (the successor label
to "experimental") — none is Stable. This repo's pin,
`OTEL_SPEC_VERSION = "1.30.0-experimental"` in `backend/app/models/trace.py`,
predates the repo split and is stale both in version number and in which repo
owns the spec. `OTEL_ACTION_MAP`'s literal key strings do not match current
attribute values (see §1). Coverage of spec.md's Identity/Environment/Intent
fields is thin outside the LLM-call-shaped fields; coverage of Events/Agent
events fields tied to tool calls and agent invocation is good; anything about
permissions, retries, provenance, or environment/repo state has no semconv
equivalent at all — semconv was built for LLM API telemetry, not for
IDE/coding-agent trace ingestion.

## 1. Current status of the GenAI semantic conventions

- As of **2026-06-12, semantic-conventions v1.42.0**, all `gen_ai.*`
  attributes, spans, metrics, and events were deprecated in the main
  `open-telemetry/semantic-conventions` repo and moved to a new dedicated
  repo, `open-telemetry/semantic-conventions-genai`. The redirect is visible
  live at the official docs page, which now reads as a stub pointing
  elsewhere: [opentelemetry.io/docs/specs/semconv/gen-ai/](https://opentelemetry.io/docs/specs/semconv/gen-ai/).
- The new repo ([open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai))
  has **no tagged release** as of this research (its Releases page is empty);
  it is versioned only by git commits on `main`, managed with Weaver against
  the core semantic-conventions schema.
- Every attribute checked (`gen_ai.provider.name`, `gen_ai.operation.name`,
  `gen_ai.agent.id`, `gen_ai.tool.call.id`, `gen_ai.conversation.id`, etc.) is
  tagged **stability: Development** — the current name for what used to be
  called "experimental." None of the GenAI-specific attributes are Stable.
  Only the borrowed general-purpose attributes (`error.type`,
  `exception.type`, `exception.message`, `exception.stacktrace`) are Stable,
  because they come from OTel's core (non-GenAI) semantic conventions.
- **Is the repo's pin stale?** Yes, on two axes: (a) `1.30.0-experimental` is
  a version number from before the repo split — that version tag lived in
  `open-telemetry/semantic-conventions`, which no longer owns GenAI content at
  all; (b) the spec has continued to evolve past 1.30.0 inside the old repo
  before the split (e.g. `gen_ai.conversation.id` and the create/invoke agent
  distinction are later additions) and now continues to evolve, untagged, in
  the new repo. There is no way to cite "1.30.0-experimental" against the
  current source of truth — that version string doesn't resolve to anything
  in the current repo.
- **Does anything in `OTEL_ACTION_MAP` now conflict?** Partially. The map's
  keys (`gen_ai.invoke_agent`, `gen_ai.execute_tool`, `gen_ai.chat`) read as
  if they were literal `gen_ai.*`-namespaced span or operation identifiers,
  but current semconv `gen_ai.operation.name` **values are bare, unprefixed
  strings** — `"invoke_agent"`, `"execute_tool"`, `"chat"`, `"create_agent"`,
  `"generate_content"`, `"text_completion"`, `"embeddings"`, `"plan"`,
  `"invoke_workflow"`, and others — not `"gen_ai.invoke_agent"` etc. (source:
  [gen-ai-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-spans.md),
  [gen-ai-agent-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-agent-spans.md)).
  So if `OTEL_ACTION_MAP` is meant to match wire values emitted by real
  `gen_ai.operation.name` attributes, its keys have a stray `gen_ai.` prefix
  that will never match. `human.review` has no semconv counterpart at all —
  human-in-the-loop review is outside GenAI semconv's scope.

## 2. Coverage against spec.md's canonical trace requirements

Coverage table. Status values: **covered** (a `gen_ai.*` or core-OTel
attribute exists with matching semantics), **covered-under-different-name**
(an attribute exists but is named/scoped differently or is expressed via
OTel's native span/trace mechanics rather than an explicit field),
**no-equivalent** (nothing in the registry addresses it).

### Identity

| spec.md field | semconv attribute | status |
|---|---|---|
| trace_id | OTel native `trace_id` (W3C trace context, not a `gen_ai.*` attribute) | covered-under-different-name |
| episode_id | — | no-equivalent |
| session_id | `gen_ai.conversation.id` (explicitly "session, thread" per its own description) | covered-under-different-name |
| parent_trace_id | OTel native span `parent_span_id` / trace context propagation | covered-under-different-name |
| parent_event_id | — (events don't have a parent-event attribute; nesting is via the enclosing span) | no-equivalent |
| agent_id | `gen_ai.agent.id` (provider-assigned; explicitly *not* meant for transient in-memory instance IDs) | covered-under-different-name |
| actor_id | — (no attribute distinguishes human vs. agent vs. system actor) | no-equivalent |
| provider | `gen_ai.provider.name` (`openai`, `anthropic`, `aws.bedrock`, `gcp.vertex_ai`, ...) | covered |
| provider_version | `gen_ai.request.model` / `gen_ai.response.model` cover model version, not provider/SDK version; no attribute for e.g. "Claude Code CLI v2.x" | covered-under-different-name |

### Intent

| spec.md field | semconv attribute | status |
|---|---|---|
| user_goal | `gen_ai.input.messages` (opt-in; captures conversation content generically, not a distinct "goal" field) | covered-under-different-name |
| task description | — | no-equivalent |
| constraints | — | no-equivalent |
| requested autonomy | — | no-equivalent |
| task identifiers | — | no-equivalent |

### Environment

| spec.md field | semconv attribute | status |
|---|---|---|
| cwd | — (not in `gen_ai.*`; OTel has generic `process.*`/`os.*` resource attributes in core semconv, not GenAI-specific, and none is "current working directory") | no-equivalent |
| repository identity/root/branch/commit SHA/dirty state | — (OTel core has `vcs.*` semantic conventions for CI/CD contexts, but they are not referenced anywhere in the GenAI conventions and are a separate, unrelated namespace) | no-equivalent |
| runtime versions | — | no-equivalent |
| application/editor identity | — (closest is `gen_ai.provider.name`, which names the model provider, not the host application) | no-equivalent |
| OS/device identity | — (OTel core `os.*`/`host.*` resource attributes exist generically but aren't part of, or referenced by, GenAI semconv) | no-equivalent |

### Events

| spec.md field | semconv attribute | status |
|---|---|---|
| sequence number | — (OTel events/spans are ordered by timestamp, not an explicit sequence attribute) | no-equivalent |
| timestamp | OTel native span/event timestamps | covered |
| event type | `gen_ai.operation.name` (for spans) / distinct event names like `gen_ai.client.inference.operation.details`, `gen_ai.client.operation.exception`, `gen_ai.evaluation.result` | covered-under-different-name |
| actor | — | no-equivalent |
| tool | `gen_ai.tool.name` | covered |
| tool call ID | `gen_ai.tool.call.id` | covered |
| parent tool/agent relationship | OTel native span parent-child nesting (an `invoke_agent` span is parent of its `execute_tool`/inference child spans); no explicit "parent agent id" attribute | covered-under-different-name |
| tool input | `gen_ai.tool.call.arguments` (opt-in, sensitive-data warning) | covered |
| tool output | `gen_ai.tool.call.result` (opt-in, sensitive-data warning) | covered |
| success/failure | `error.type` presence/absence + OTel span status (per "Recording Errors" guidance) — no explicit boolean/enum outcome field | covered-under-different-name |
| duration | OTel native span start/end timestamps | covered |
| permission state | — | no-equivalent |
| error information | `error.type` (span attribute) plus `exception.type`/`exception.message`/`exception.stacktrace` on the `gen_ai.client.operation.exception` event (these three are Stable core-OTel attributes) | covered |
| provenance | — (no attribute for "which extractor/pipeline version produced this record") | no-equivalent |
| raw provider payload reference | — (content itself can be captured via opt-in `gen_ai.input.messages`/`gen_ai.output.messages`/`gen_ai.tool.call.arguments`/`gen_ai.tool.call.result`, but there's no attribute for a *reference/pointer* to an externally stored raw payload) | no-equivalent |

### Agent events

| spec.md field | semconv attribute | status |
|---|---|---|
| model invocation | `gen_ai.operation.name = chat/text_completion/generate_content/embeddings` span | covered |
| user prompt | `gen_ai.input.messages` (opt-in) | covered |
| assistant turn | `gen_ai.output.messages` (opt-in) | covered |
| tool invocation | `execute_tool` span (`gen_ai.tool.*`) | covered |
| tool result | `gen_ai.tool.call.result` | covered |
| tool failure | `error.type` on the `execute_tool` span | covered-under-different-name |
| retry | — (spec text notes retries are absorbed into the same span's duration, not modeled as a distinct event) | no-equivalent |
| subagent start | `invoke_agent`/`create_agent` child span start | covered-under-different-name |
| subagent stop | child span end | covered-under-different-name |
| task created | — | no-equivalent |
| task completed | — (closest is span completion, but no "task" concept distinct from agent/tool spans) | no-equivalent |
| permission request/denial | — | no-equivalent |
| compaction | — | no-equivalent |
| session start/end | `gen_ai.conversation.id` scopes messages to a session but there is no explicit session-start/session-end signal | no-equivalent |

**Pattern:** everything shaped like "an LLM API call or a tool call within
one" is covered, often well (tool call id/name/args/result, token counts,
finish reasons, provider name, model name/version, error type + exception
detail). Everything shaped like "IDE/coding-agent session context" — cwd,
repo/VCS state, editor identity, task/permission/compaction bookkeeping,
provenance of the record itself — has no equivalent, because GenAI semconv's
design center is LLM-provider API telemetry (chat/embeddings/tool-call spans
for backend services), not coding-agent session telemetry.

## 3. Agent and tool spans specifically

Span kinds and nesting, per
[gen-ai-agent-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-agent-spans.md)
and
[gen-ai-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-spans.md):

- **`create_agent`** (span kind CLIENT): one-time instantiation of a *remote*
  agent (e.g. an OpenAI Assistant, a Bedrock agent). Span name
  `create_agent {gen_ai.agent.name}`.
- **`invoke_agent`** (span kind CLIENT or INTERNAL): executing an existing
  agent — CLIENT for remote agent services, INTERNAL for local frameworks
  (the doc names LangChain and CrewAI as INTERNAL examples). Span name
  `invoke_agent {gen_ai.agent.name}`. Many `invoke_agent` calls can occur
  against one `create_agent`-produced identity.
- **`plan`** (span kind INTERNAL): optional child span for an explicit
  planning step; "the LLM call that generates the plan SHOULD be a child of
  the plan span, and the tool or task spans produced from the plan are
  typically sibling operations under the same `invoke_agent` span."
- **`execute_tool`** (span kind INTERNAL): one span per tool call, attributes
  `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.call.arguments`,
  `gen_ai.tool.call.result`. Nested as a child of the `invoke_agent` span (or
  sibling of the plan span, per above).
- **`invoke_workflow`** (span kind INTERNAL): a coarser wrapper for
  multi-agent orchestration/workflow engines, separate from a single agent
  invocation.
- **Session/multi-turn:** `gen_ai.conversation.id` is the only session-scoping
  mechanism — it's a string attribute stamped onto every span/event belonging
  to the same conversation, not a distinct span or entity of its own. There is
  no "session span" that wraps multiple `invoke_agent` calls; correlation
  across turns is by attribute value, not by span parent-child nesting.

**Does this match spec.md's episode/trace/event nesting?** Not directly, and
the two hierarchies answer different questions:

- Semconv's hierarchy is **span parent-child nesting within one W3C trace**:
  `invoke_agent` → `plan`/`execute_tool`/chat spans, correlated to a
  conversation by a flat `gen_ai.conversation.id` attribute, not by any
  further containment.
- spec.md's hierarchy (confirmed by `inventory.md` and this repo's schema) is
  **episode → trace → event as separate first-class rows**, and — per the
  standing distinction this ticket was told to preserve — this repo's
  `traces` table (`backend/db/01_ontology.sql`,
  `backend/app/models/trace.py`) is a *different* concept from the HTN
  agent's own ephemeral per-run `.jsonl` execution telemetry under
  `experiments/swebench_pro/`. Neither of those two existing repo concepts is
  simply "a semconv span" — `traces` today is a flat outcome record
  (`TraceRecord`: `trace_id`, `task_node_id`, `outcome`, `action_type`,
  `cost`, `latency_ms`, `parent_trace_id`), closer in spirit to a single
  semconv span than to a whole trace tree, while `episode_id`/`episode_links`
  (mentioned in spec.md's Identity fields) have no semconv counterpart at
  all — episodes are this repo's grouping concept, wayfinding-external to
  OTel entirely.

So: semconv gives a real, usable parent-child span shape for
*agent-invocation-contains-tool-calls*, which is a good structural match for
one *slice* of spec.md's Events (an event's "parent tool/agent relationship"
field maps naturally onto OTel span parent_span_id). But semconv has nothing
that plays the role of spec.md's `episode` (a durable grouping entity
independent of any single execution) — that concept must come from this
repo's own model, not from semconv, regardless of the derive/extend/diverge
call.

## 4. Events vs. spans vs. logs, and the privacy opt-in

Per
[gen-ai-events.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-events.md)
and the OTel Python contrib docs for the GenAI instrumentation:

- Prompt/completion/tool-argument/tool-result content is **not captured by
  default** anywhere. It is gated behind an explicit opt-in.
- The mechanism has moved historically (older semconv put message content on
  separate `gen_ai.{role}.message`/`gen_ai.choice` **log-based events**;
  current semconv can instead inline the same content as **span attributes**
  — `gen_ai.input.messages`, `gen_ai.output.messages`,
  `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`,
  `gen_ai.tool.call.result` — or emit a single combined
  `gen_ai.client.inference.operation.details` event). Both shapes exist
  simultaneously in the current instrumentation ecosystem depending on which
  stability opt-in flag is set.
- Concretely, the OpenTelemetry Python GenAI instrumentation utility
  documents (per
  [opentelemetry-python-contrib GenAI util docs](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation-genai/util.html)
  and corroborated by New Relic's Strands SDK write-up) two environment
  variables that jointly control this:
  - `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` — selects the
    newer span/event attribute shapes.
  - `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` — the actual content
    gate, with values `true` (legacy: content on `gen_ai.{role}.message` /
    `gen_ai.choice` log events), `span_only`, `event_only`, or
    `span_and_event` (new-shape variants). Unset means no content capture at
    all, only structural/metadata attributes (token counts, model name,
    finish reasons, etc.).
  - Attributes carrying content are explicitly flagged in the registry docs
    as sensitive-data risks (e.g. `gen_ai.tool.description`: "may contain
    sensitive data"; `gen_ai.tool.call.result`: "may contain sensitive
    information") and are marked **Opt-In** requirement level, meaning
    instrumentations must not record them unless the user turns them on.
  - Redaction/truncation itself is explicitly *not* specified by semconv:
    "Instrumentations MAY provide a way for users to filter or truncate"
    these attributes — implementation is left to each instrumentation
    library, not standardized.

**Relevance to spec.md's raw-payload-recoverability-under-privacy-policy
requirement:** semconv's opt-in flag is a coarse global on/off (plus a
shape choice), not a policy engine — it has no concept of per-field redaction
rules, retention tiers, or "capture but encrypt at rest," all of which
spec.md's privacy language (lines ~894-912: "do NOT send raw traces to an
external LLM by default") implies this repo needs. Semconv's content-capture
switch is a reasonable low bar (off by default, explicit opt-in to turn on),
but it is not a substitute for this repo's own privacy policy layer.

## 5. Extension mechanism

Per OTel's general (non-GenAI-specific) naming rules, still authoritative for
all semantic conventions including GenAI:
[opentelemetry.io/docs/specs/semconv/general/naming/](https://opentelemetry.io/docs/specs/semconv/general/naming/)
and the source doc
[semantic-conventions/docs/general/naming.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/general/naming.md):

- The `otel.*` namespace is reserved exclusively for the OpenTelemetry
  specification itself.
- Attribute/span/metric names are structured `{domain}.{component}.{property}`
  with `.` as the only hierarchy delimiter; lowercase Latin letters, digits,
  underscore, dot only.
- **Explicit guidance against squatting on an existing semconv namespace**:
  "It is not recommended to use existing OpenTelemetry semantic convention
  namespace as a prefix for a new company- or application-specific attribute
  name, as this may result in a name clash in the future if OpenTelemetry
  decides to use that same name for a different purpose."
- **The recommended extension mechanism is a reverse-DNS company namespace**:
  "it is recommended to prefix the new name by your company's reverse domain
  name, e.g. `com.acme.shopname`." This is the general OTel-wide mechanism,
  not something GenAI-specific — there is no separate `gen_ai.`-scoped
  extension registry or process for adding vendor fields under `gen_ai.*`
  itself.
- Practical implication for this repo: any field this repo needs that
  semconv doesn't define (episode_id, task description, permission state,
  repo/VCS identity, etc.) should NOT be smuggled in as `gen_ai.something` —
  it should live under this repo's own namespace (e.g. something like
  `stealthlab.*` or a reverse-DNS form) if ever exported as OTel attributes,
  precisely so a future GenAI semconv release adding those names doesn't
  collide with this repo's meaning for them.

## 6. Ecosystem reality

Evidence is mixed — real but partial adoption, with at least one prominent
fork of the ecosystem emitting a *different*, non-standard convention set
under a similar name:

- **AWS Strands SDK** (agent framework) does emit `gen_ai.*` attributes when
  configured with both `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
  and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` — confirmed
  via New Relic's own integration write-up showing real captured
  `gen_ai.input.messages`/`gen_ai.output.messages`/`gen_ai.system_instructions`
  spans from a travel-planning agent on Bedrock AgentCore
  ([New Relic: Traces for AI Agents with OTel and Strands SDK](https://newrelic.com/blog/observability/traces-for-ai-agents-otel-strand)).
- **OpenLLMetry / Traceloop**: patches OpenAI, Anthropic, LangChain, and
  LlamaIndex clients to emit spans to any OTLP endpoint, and is widely used as
  the de facto instrumentation layer by several LLM observability backends
  (Laminar, Langfuse, Phoenix, LangSmith reportedly all ingest OpenLLMetry
  spans). **However, Traceloop's own OTLP endpoint documents that it expects
  "OpenLLMetry semantic conventions," explicitly distinct from the official
  OpenTelemetry GenAI conventions** — i.e. the most widely deployed
  instrumentation layer in this space runs its own convention set that only
  overlaps with, rather than strictly implements, the official `gen_ai.*`
  registry.
- **Langfuse**: as of the sources found, still only "publicly considering"
  full OTel integration rather than having shipped official `gen_ai.*`
  semconv emission; it and Arize Phoenix support OTel-*compatible* trace
  ingestion generally (accepting OTLP), which is a lower bar than emitting
  the specific GenAI attribute names.
- General-purpose commentary corroborates thin/uneven adoption: as of
  March 2026, most GenAI semantic conventions were still experimental, and
  "most SDK wrappers still emit wrong span names" relative to the official
  registry (secondary source, cited here only as adoption-texture evidence,
  not as spec content: [dev.to summary of OTel GenAI status](https://dev.to/x4nent/opentelemetry-genai-semantic-conventions-the-standard-for-llm-observability-1o2a)).
- Net picture: adoption exists and is real for the LLM-call-shaped subset
  (chat spans, token counts, tool-call spans) among AWS-ecosystem and some
  official OTel-contrib Python instrumentation packages
  (`opentelemetry-instrumentation-openai-v2`,
  `opentelemetry-instrumentation-google-genai`, both on PyPI and both
  targeting the official registry). But the single most widely deployed
  instrumentation path in practice (OpenLLMetry) runs a related-but-distinct
  convention set, and no coding-agent-specific vendor was found emitting the
  official registry's episode/session/environment-adjacent fields (because,
  per §2, those fields largely don't exist in the registry to emit).

## Recommendation (input to ticket 06, not a decision)

The evidence supports a **hybrid framing rather than a pure derive-or-extend
binary**, for ticket 06 to weigh:

- **For the LLM-call/tool-call-shaped slice of spec.md's Events and Agent
  events groups** (tool name/id/input/output, model invocation, token
  counts, error type, provider/model identity, agent invoke/create,
  parent-child span nesting) — coverage is real and the registry's shape
  (span kind, attribute names, opt-in content gating) is usable as-is or with
  light renaming. This is where "derive from semconv" earns its keep: the
  registry got real design attention here and multiple frameworks/vendors
  independently converged on similar shapes.
- **For spec.md's Identity, Intent, and Environment groups**
  (episode_id, actor_id, user_goal/task description/constraints/autonomy,
  cwd/repo identity/branch/commit SHA/dirty state, permission state, task
  created/completed, compaction, retry-as-event, provenance,
  raw-payload-reference) — there is no semconv equivalent to derive from at
  all; these are coding-agent/IDE-session concepts outside GenAI semconv's
  design center (built for backend LLM-API telemetry). This is necessarily
  "diverge deliberately" territory — not a compatibility gap that a future
  semconv version is likely to close, since the registry's own scope
  statement is about LLM clients, agents, tool execution, and MCP, not IDE
  session/repo/task bookkeeping.
- **Whatever fields this repo adds beyond semconv should live under this
  repo's own namespace** (§5) rather than as unofficial `gen_ai.*`
  extensions, both because that's OTel's own stated guidance and because the
  registry is actively evolving pre-1.0 (§1) — any `gen_ai.foo` string this
  repo invents today has a real chance of being claimed with different
  semantics by the official registry tomorrow.
- Whatever ticket 06 decides, `OTEL_SPEC_VERSION = "1.30.0-experimental"`
  and `OTEL_ACTION_MAP`'s exact key strings in `backend/app/models/trace.py`
  need to change regardless of the derive/extend/diverge outcome — the pinned
  version is unresolvable against the current source of truth, and the
  mapped strings don't match current `gen_ai.operation.name` wire values
  (§1). That correction is orthogonal to the bigger strategic call.

## Sources

- [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai) (repo root)
- [semantic-conventions-genai: docs/registry/attributes/gen-ai.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/registry/attributes/gen-ai.md)
- [semantic-conventions-genai: docs/gen-ai/gen-ai-agent-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-agent-spans.md)
- [semantic-conventions-genai: docs/gen-ai/gen-ai-spans.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-spans.md)
- [semantic-conventions-genai: docs/gen-ai/gen-ai-events.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-events.md)
- [semantic-conventions-genai: docs/gen-ai/gen-ai-exceptions.md](https://cdn.jsdelivr.net/gh/open-telemetry/semantic-conventions-genai@main/docs/gen-ai/gen-ai-exceptions.md)
- [opentelemetry.io/docs/specs/semconv/gen-ai/](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (redirect stub confirming the repo migration)
- [opentelemetry.io/docs/specs/semconv/general/naming/](https://opentelemetry.io/docs/specs/semconv/general/naming/) and [semantic-conventions/docs/general/naming.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/general/naming.md) (extension/namespacing rules)
- [OpenTelemetry Python Contrib: instrumentation-genai util docs](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation-genai/util.html) (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` values)
- [New Relic: Traces for AI Agents with OTel and Strands SDK](https://newrelic.com/blog/observability/traces-for-ai-agents-otel-strand) (ecosystem adoption evidence, env var corroboration)
- [Traceloop: GenAI Semantic Conventions docs](https://www.traceloop.com/docs/openllmetry/contributing/semantic-conventions) (OpenLLMetry's distinct convention set)
- `backend/app/models/trace.py` (this repo's current pin and mapping, cited throughout as the object of comparison)
