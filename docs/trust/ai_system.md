# AI system description and model inventory

**Draft for a security or procurement review.** Everything stated here was read from this repository on 2026-10-06. Items
marked **UNVERIFIED** are provider-side facts (retention, training use, region) that must be confirmed against the
provider's current terms before this is shown to a customer. Nothing here is deployed yet.

## What the system is

StealthLab is a shared memory of verified procedures ("ways") for coding agents, served over MCP. An agent asks for the known
ways to do a task (`find_ways`), may be told which model to try (`model_plan`), reports whether each attempt passed its own
check (`report_result`), and can run a prompt on a connected model or agent (`call_model`). It does not edit the customer's
code, does not run customer code, and is not given repository contents: repository facts are request-scoped and never
stored.

## Data flows per tool

| Tool | What the server receives | What it sends to third parties | What it stores |
|---|---|---|---|
| `find_ways` | the task text; optionally repository facts (claims) | the task text and facts to the embedding provider and the semantic judge models | a row per request: SHA-256 of the query, caller id, outcome, timings, shard counts. Not the text. Not the repository facts. |
| `submit_way` | text a person contributes, as a shared way | the text to the content-screen models and the embedding provider | the way (visible to others once screened); links, hidden characters and injection phrases are rejected before any model sees it |
| `report_discovery` | a private note about a procedure | the text to the embedding provider | a private claim owned by the caller |
| `recommend_models` / `report_model_run` / `report_result` | goal and procedure ids, model names, pass/fail, token counts, cost | none | routing observations and decisions (ids, verdicts, tokens, cost) |
| `call_model` | the prompt (and optional system text) | the prompt to the model or agent endpoint the organisation connected, using that connection's credential | a ledger row: organisation, caller, unit, provider, data class, four token counts, cost and its source, timing. **Not the prompt or the reply.** |

Tracing carries only ids, counts, timings and model names by design (`app/telemetry.py`); a redaction step runs on trace
text before it is stored (`trace_redaction.py`).

## Controls an organisation has over those flows

- A **data-class policy** per organisation and per connection: a call is refused unless the connection and the
  organisation both allow the data class, and platform-owned credentials also pass the platform egress policy.
- **Allowlists** for provider, model and tool; a **kill switch**; **monthly and per-user daily budgets** held before each
  call. All default to deny.
- Content returned by `find_ways` is labelled `content_trust` (untrusted data, not instructions).
- Every administrative change is audited in a tamper-evident log the organisation can export.

## Model inventory

| Role | Model | Provider and region | Data it sees | Retention / training |
|---|---|---|---|---|
| Embeddings | `gemini-embedding-2` (1024 dimensions) | Google Vertex AI, location `us-central1` | task text, way text, claim text | **UNVERIFIED**: confirm Vertex data-governance terms and any zero-retention setting |
| Extraction and chat | `google/gemini-3.8-flash` | Google Vertex AI, location `global` | text being extracted during ingestion; not customer prompts | **UNVERIFIED** |
| Semantic judge | `gemma-4-31B-it` (setting `GENERAL_COMPUTE_JUDGE_MODEL`) | General Compute (region unknown) | the task text and candidate goals or procedures | **UNVERIFIED**: no agreement reviewed |
| Semantic judge (alternate) | a JEV/NLI service at `api.typesafe.ai` | third party (region unknown) | the same | **UNVERIFIED**; answered 402 (unpaid) on 2026-10-02 and is skipped |
| `call_model` targets | whatever the organisation connects (any OpenAI-compatible endpoint or A2A agent) | chosen by the customer | the prompt | governed by the customer's own agreement with that provider |

**Residency:** the database is Neon in AWS us-east-2, object storage is Cloudflare R2 (region not set), embeddings are
processed in Google's `us-central1`, and the judge is outside India. The system is **not** currently India-resident, and
the data-processing agreement must say so or the deployment must change first.

## Known limits

- Retrieval can degrade without telling the caller why (see the fallback inventory in `ship_readiness_plan.md`).
- Organisation erasure removes governance data today but not yet the core knowledge tables (same document).
- The quality of recommendations depends on recorded attempts; with few, they come from priors.
- The A2A and OpenAI-compatible adapters have only been exercised against mocks.
