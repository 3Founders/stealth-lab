# Provider connections: models and agents behind `call_model`

A **connection** is an endpoint plus whose credential to use. A **unit** is one thing a caller can run,
named the way the recommender names it, `model|scaffold`:

| Kind | Unit example | What it is |
|---|---|---|
| `openai_compatible` | `deepseek-v3.2\|direct` | A bare model on any host that speaks `POST {base}/chat/completions` (General Compute, Together, Fireworks, OpenRouter, Groq, vLLM). |
| `a2a` | `gemma-4\|coder` | An AI agent *with its harness* behind an Agent2Agent endpoint. The scaffold is the harness name, so routing treats an agent as one more unit. |

Vertex (ADC, no key), Anthropic-native, Bedrock and Azure are **not** the OpenAI shape. They need their own
adapter: `app.providers.adapters.register_adapter(kind, adapter)`.

## Configure (today: a file; later: the Providers page)

`STEALTH_PROVIDER_CONNECTIONS_FILE=/path/connections.json` (re-read when the file changes; a broken file
fails loudly rather than half-applying):

```json
{"connections": [
  {"connection_id": "gc",
   "kind": "openai_compatible",
   "base_url": "https://api.example-host.com/v1",
   "provider": "example-host",
   "credential_ref": "env:GENERAL_COMPUTE_API_KEY",
   "credential_owner": "customer",
   "allowed_data_classes": ["PUBLIC_SOURCE", "GLOBAL_PROCEDURE", "USER_PRIVATE"],
   "units": [{"model": "deepseek-v3.2", "provider_model": "deepseek-ai/DeepSeek-V3.2",
              "input_per_mtok": 0.27, "output_per_mtok": 1.10}]},
  {"connection_id": "my-agent",
   "kind": "a2a",
   "base_url": "https://agent.example.com/a2a",
   "credential_ref": "env:MY_AGENT_TOKEN",
   "allowed_data_classes": ["PUBLIC_SOURCE"],
   "units": [{"model": "gemma-4", "scaffold": "coder", "per_call_usd": 0.05}]}
]}
```

- `credential_ref` is a *reference* (`env:NAME` today). The secret is read at call time, used for one request,
  and never stored, returned or logged. Add a store with `secrets.register_secret_resolver("gsm", ...)`.
- `owner` is `platform` (default, everyone), `org:<id>` (members of that org) or `user:<subject>`.
- `credential_owner`: `customer` = BYOK, the connection's `allowed_data_classes` is the customer's own risk
  decision; `platform` = our key, so the platform egress policy (`model_provider_policies`, fail-closed) must
  also allow it.
- Prices feed routing: `python -m app.ingestion.admin routing-sync-prices` (add `--apply` to write
  `routing_prices`). Without a price the recommender excludes the unit.

## How it is used

- `find_ways` (and the plan in it) lists connected units as candidates once any connection source is configured.
- `call_model(prompt, model="auto", instance_key=...)` runs the plan's next unit; `call_model(prompt,
  model="deepseek-v3.2")` runs any named unit for any purpose. Its reply carries the exact `report_result`
  arguments (tokens, cost) when an `instance_key` was given.

## Checks before any byte leaves (`providers/service.py`)

visibility, then data class, then platform policy (platform credentials only), then worst-case cost
(`max_cost_usd`), then endpoint safety (https only; every resolved address must be public; no redirects), then
credential resolution.

## Not done yet (do before a shared key serves other people)

- Per-user / per-org **budgets** and rate limits, and an **audit row per call** (only tracing exists).
- A database store and an admin page for connections; key storage in a secret manager.
- Streaming, and polling of long-running A2A tasks (an unfinished task returns its `state`).
- DNS-rebinding is narrowed (checked at call time) but not closed; restrict egress at the network layer too.
- The A2A adapter follows the published 1.0 spec (with a 0.x `message/send` fallback) and has not been run
  against a live agent. The OpenAI-compatible adapter has not been run against a live host either.
