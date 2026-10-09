# Provider and model catalog (routed open models)

What this covers: how to connect hosted open-model providers (OpenRouter, General Compute, DeepInfra, Novita) as
`openai_compatible` connections, what data-handling facts a connection records, and the model catalog that keeps licences
and prices in one place. It does **not** cover the orchestrator endpoint, agents, or harness presets (later phases).
`docs/providers.md` is a separate document owned by another lane.

Everything below marked *verify* comes from provider pages or secondary reports read on 2026-10-09. Re-read the primary page
before a customer depends on it.

## Connecting a provider

A provider is a connection record, not code. Copy `docs/providers/connections.example.json`, replace each
`REPLACE_WITH_PROVIDER_MODEL_ID` with the id that provider uses for the model, add prices, put the keys in the environment
(`credential_ref: "env:NAME"`), and point `STEALTH_PROVIDER_CONNECTIONS_FILE` at it. The file reloads when it changes.

| Provider | Base URL | Notes |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` | Per-request `provider: {zdr: true, data_collection: "deny"}` via `request_extras`. ZDR is stricter than no-training. |
| General Compute | `https://api.generalcompute.com/v1` | OpenAI-compatible; pricing seen only on its own blog. Request a DPA. |
| DeepInfra | `https://api.deepinfra.com/v1/openai` | Memory-only inference per its docs, limited debug storage in its privacy policy; US datacenters, no region pinning on serverless. |
| Novita | `https://api.novita.ai/openai/v1` | *Verify*: its docs also show an older `/v3/openai` base. Datacenter countries are not disclosed. Highest diligence burden. |

## What a connection records about data handling

`zdr`, `no_training`, `region`, `dpa_signed` on the connection (`app/providers/types.py`). Absent means **unknown**, and unknown is
never read as "yes". They are declarations by whoever configured the connection, not proof. One rule is enforced: on OpenRouter,
`zdr: true` requires `request_extras.provider.zdr: true`, so the flag cannot say more than the requests do.
`request_extras` adds JSON fields to every chat/completions body but cannot set `model`, `messages`, `stream`, `tools`,
`max_tokens` or `temperature` (the adapter owns them, and the cost cap relied on them).

## Model catalog

`app/providers/model_catalog.py`. The built-in entries are metadata only (licence, origin, tier) and are flagged unverified. Offerings
(which provider serves which model id at what price) come from the file named by `STEALTH_MODEL_CATALOG_FILE`
(`docs/providers/model_catalog.example.json`). `units_for(connection_id, entries)` produces the `units` list for a connection from priced
offerings; unpriced offerings produce no unit, because the recommender cannot rank them and org budgets refuse them.

## Using a routed model as a harness's main model: `/v1/chat/completions`

The API serves an OpenAI-compatible `POST /v1/chat/completions` (tools, streaming) and `GET /v1/models`
(`app/api/chat_completions.py`, `app/providers/chat.py`). Point a harness at `{server}/v1` with a StealthLab bearer
token; the connections the caller can see are its models. Every call goes through the same checks and organisation
ledger as `call_model`: visibility, data class, egress policy, cost cap, endpoint safety, key rotation and failover,
reserve-then-settle against the org's budgets.

- The org policy must list the tool `chat_completions` in `allowed_tools`.
- Per-request controls are headers: `X-Stealthlab-Org`, `X-Stealthlab-Data-Class` (default `USER_PRIVATE`),
  `X-Stealthlab-Max-Cost-Usd`, `X-Stealthlab-Fallback-Models`. The reply carries `x-stealthlab-unit` and
  `x-stealthlab-connection`.
- Only an allowlist of body fields is forwarded (messages, tools, tool_choice, sampling controls, response_format, stop,
  seed). `n` must be 1.
- Streaming asks the provider for `stream_options.include_usage`. A provider that sends no usage is settled at the
  reserved worst case, never zero. Failover happens only before the first byte.
- Other client shapes, translated onto the same path (`app/providers/shims.py`): `POST /v1/responses` (OpenAI Responses,
  stateless; `previous_response_id` is refused) for Codex CLI, and `POST /v1/messages` plus `/v1/messages/count_tokens`
  (an estimate) for Claude Code via `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` (sent as a Bearer token; a key sent
  only as `x-api-key` is not read). Images, reasoning/thinking blocks, server-side tools (web search) and prompt-cache
  markers are dropped or refused, not faked.
- A client's `max_tokens` is a ceiling: values above the server limit (16000) are lowered to it, not refused.
- Verified (2026-10-09, fake provider behind the endpoints, no keys; script: `backend/scripts/smoke_real_clients.py`): four real clients, each with a plain reply and, for three of them, a tool-call round trip where the client ran the tool and sent its result back, all streaming: Claude Code `claude -p` on `/v1/messages` (text only; this run found two bugs, system turns inside `messages` and `max_tokens` above 16000, both fixed), OpenCode 1.18.35 (inline `OPENCODE_CONFIG_CONTENT` provider block) and Cline 3.0.61 on `/v1/chat/completions`, Codex CLI 0.153.4 with `wire_api = "responses"` on `/v1/responses`. The Responses shim also converted Codex's function_call / function_call_output turns correctly. Not done: our own token counting and a run against a live provider.

Example harness config (OpenCode, project `opencode.json`; keys stay in the environment):

```json
{ "provider": { "stealthlab": { "npm": "@ai-sdk/openai-compatible", "name": "StealthLab",
  "options": { "baseURL": "https://your-server.example.com/v1", "apiKey": "{env:STEALTHLAB_TOKEN}" },
  "models": { "glm-5.3": {} } } } }
```

## Before a customer's code goes through a provider

1. A signed DPA, and written confirmation of retention, training, region and sub-processors.
2. The provider's resale / commercial-use clause (OpenRouter's terms reportedly bar reselling model access or building a competing service; DeepInfra's resale terms were not seen).
3. The model's licence on its Hugging Face card (GLM-5.3 reportedly left MIT; Llama and Gemma carry custom terms).
4. India (DPDP Act): we are a processor of the customer's code; US-only inference needs a cross-border-transfer position. Do not send customer code to Z.ai's own China-hosted API.
5. Per-org model allowlists: some security teams reject Chinese-origin models whatever the licence says.
6. No savings or quality numbers in marketing until the three-arm harness has measured them.
