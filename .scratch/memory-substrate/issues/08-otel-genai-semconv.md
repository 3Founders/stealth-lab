# OpenTelemetry GenAI semantic conventions

Type: research
Status: resolved
Blocked by:

## Question

Should the canonical trace format derive from the OpenTelemetry GenAI semantic conventions, and does the current version actually cover what spec.md requires?

This repo has already made a partial bet on it: `backend/app/models/trace.py` pins `OTEL_SPEC_VERSION = "1.30.0-experimental"` and maps its `action_type` values through an `OTEL_ACTION_MAP`. Whether that bet should be extended or abandoned is a decision (ticket 06 owns it); this ticket supplies the facts.

Establish, against the OpenTelemetry specification and semantic-convention registry:

1. **Current status of the GenAI semantic conventions.** What is the latest version, is it still experimental, and what changed since 1.30.0? Is the version pinned in this repo stale, and does anything in the mapping now conflict?
2. **Coverage against spec.md's canonical trace requirements.** Field by field, does semconv define an attribute for: session id, agent id, tool call id, tool name, tool input, tool output, parent tool/agent relationship, permission state, error information, provider and provider version, token counts, duration, and outcome? List what is covered, what is covered under a different name, and what has no equivalent.
3. **Agent and tool spans specifically.** How does semconv model an agent invocation, a tool call, and a multi-turn session? Is there an established span hierarchy for agentic runs, and does it match the episode/trace/event nesting spec.md wants?
4. **Events vs spans vs logs.** Where does semconv put prompt and completion content, and what does it say about capturing it at all (there are privacy opt-in flags in this area)? Relevant because spec.md requires raw payload recoverability under a privacy policy.
5. **Extension mechanism.** How are vendor- or application-specific attributes meant to be added without colliding with future reserved names?
6. **Ecosystem reality.** Do the agent frameworks and observability vendors actually emit these conventions today, or is adoption thin enough that following the spec buys interoperability that does not exist in practice?

Deliverable: a cited Markdown findings file with a coverage table (spec.md field → semconv attribute → status), and a plain recommendation with its reasoning: derive from semconv, extend it, or diverge deliberately.

## Answer

Full findings, coverage table, and sourcing:
[research/08-otel-genai-semconv-findings.md](../research/08-otel-genai-semconv-findings.md).

Key facts:

1. **The GenAI semantic conventions moved repos.** As of 2026-06-12
   (semantic-conventions v1.42.0), all `gen_ai.*` content was deprecated out
   of the main `open-telemetry/semantic-conventions` repo into a dedicated
   `open-telemetry/semantic-conventions-genai` repo, which has no tagged
   release yet — every `gen_ai.*` signal is stability level "Development"
   (post-rename "experimental"). This repo's pin,
   `OTEL_SPEC_VERSION = "1.30.0-experimental"` in `backend/app/models/trace.py`,
   is stale and doesn't resolve against the current source of truth.
   `OTEL_ACTION_MAP`'s keys (`gen_ai.invoke_agent`, `gen_ai.execute_tool`,
   `gen_ai.chat`) also don't match current wire values — actual
   `gen_ai.operation.name` values are bare (`"invoke_agent"`,
   `"execute_tool"`, `"chat"`, ...), not `gen_ai.`-prefixed. `human.review`
   has no semconv counterpart at all.
2. **Coverage is bimodal.** Everything shaped like an LLM API call or a tool
   call within one — tool name/id/input/output, model invocation, token
   counts, error type + exception detail, provider/model identity,
   agent invoke/create, span parent-child nesting — is covered, often well.
   Everything shaped like IDE/coding-agent session context — episode_id,
   actor_id, user_goal/task description/constraints/autonomy, cwd, repo/VCS
   identity, permission state, task created/completed, compaction,
   retry-as-event, provenance, raw-payload-reference — has no semconv
   equivalent, because the registry's design center is backend LLM-provider
   API telemetry, not coding-agent session bookkeeping. Full field-by-field
   table is in the findings file.
3. **Span hierarchy vs. this repo's episode/trace/event nesting are not the
   same shape.** Semconv gives a real, usable parent-child span structure for
   `invoke_agent` → `plan`/`execute_tool`/chat spans within one W3C trace,
   correlated across turns only by a flat `gen_ai.conversation.id` attribute
   value (no session span). This repo's `episode` concept — and the fact
   that this repo's `traces` table is a separate, distinct concept from the
   HTN agent's own ephemeral per-run `.jsonl` execution telemetry — has no
   semconv counterpart; episodes are a durable grouping entity semconv
   doesn't model at all.
4. **Content capture is opt-in, coarse-grained, and not a privacy policy
   engine.** Prompt/completion/tool-argument/tool-result content is
   uncaptured by default; enabling it needs both
   `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` and
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (values `true` /
   `span_only` / `event_only` / `span_and_event`). Redaction/truncation is
   explicitly left to each instrumentation library, not standardized —
   semconv's switch is a reasonable off-by-default gate but not a substitute
   for spec.md's privacy-policy requirements.
5. **Extension mechanism is a general OTel rule, not GenAI-specific:** don't
   prefix new fields with an existing semconv namespace (risk of future
   collision); use a reverse-DNS company namespace instead (e.g.
   `com.acme.shopname`). There is no separate `gen_ai.`-scoped process for
   registering app-specific extensions.
6. **Ecosystem adoption is real but partial, and fragmented.** AWS Strands
   SDK confirmed emitting official `gen_ai.*` attributes (via New Relic's own
   integration write-up). But the most widely deployed instrumentation layer
   in this space, OpenLLMetry/Traceloop, runs its own distinct "OpenLLMetry
   semantic conventions" rather than strictly the official registry; Langfuse
   was still only "publicly considering" full OTel integration as of the
   sources found.

**Recommendation carried to ticket 06** (not decided here): a hybrid framing
— derive from semconv for the LLM-call/tool-call-shaped slice where coverage
and convergence are real, and diverge deliberately for the
Identity/Intent/Environment slice where no semconv equivalent exists and
isn't likely to appear (out of registry scope). Any repo-specific extension
fields should sit under this repo's own namespace, not as ad hoc `gen_ai.*`
additions. Independent of that call, `OTEL_SPEC_VERSION` and
`OTEL_ACTION_MAP`'s literal strings in `backend/app/models/trace.py` need
correcting regardless of outcome.
