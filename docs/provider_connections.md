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

## When a model is not working (`providers/health.py`)

- **Same model, another endpoint (automatic).** List the same unit on several connections (for example
  `deepseek-v3.2` on two hosts). When one is down -- connection error, timeout, HTTP 408/425/429/5xx, or an
  account refusal 401/402/403 -- the call goes to the next connection, and every check above runs again for it.
  A failure that says the request itself was bad (400, 404, 422, empty reply) is not retried anywhere. A
  connection refused by policy (data class, egress policy, cost cap) is skipped too, so another approved one can
  serve the call.
- **Cool-down.** A down endpoint is left out for 30 s, doubling to 5 min on repeated failures; an account refusal
  for 15 min. After the cool-down one call is let through; a success closes it. When every endpoint of a unit is
  cooling down, the one that failed longest ago still gets the call. Rate limits and server errors are tracked per
  (connection, unit); connection errors and account refusals take the whole connection out.
- **Another model (only when the caller allows it).** `call_model(model="auto")` moves on to the plan's next rung
  when every endpoint of the current rung is down; with a named model, `fallback_models=[...]` lists what may be
  used instead. Without it a named model is never swapped. The reply's `unit` is what answered; `fell_back` and
  `endpoints_failed_first` say what failed, and when the answering model is not the plan's default rung the
  `report_result` arguments name it.
- **Plans and recommendations.** `find_ways`' `model_plan` and `recommend_models` keep their ladder (a quality
  decision) but add `availability`, `next_available` and a note when a rung has no working endpoint. Units this
  deployment does not serve are `not_served` (their health is unknown here). An outage is never recorded as a
  failed attempt of the model.
- **Several keys per endpoint.** `"credential_refs": ["env:KEY_A", "env:KEY_B"]` instead of `credential_ref`. A key
  that is rate-limited (429) or refused (401/402/403) is rested (same cool-down) and the next key serves the same
  call; a missing key is skipped. A 5xx or a timeout is the endpoint's problem, not the key's, so it moves on to the
  next connection instead.
- **Latency.** `"timeout_s"` (1-600, default 120) bounds every call to that endpoint; a call past it counts as the
  endpoint being down. Each endpoint's typical latency is tracked (moving average of successful calls): an endpoint
  above its `"slow_ms"` -- or, without one, more than twice the fastest alternative and over 2 s -- is tried after
  the fast ones. A caller can set its own budget with `call_model(..., max_latency_ms=N)`: past it the call moves to
  the next endpoint, then (with `model="auto"` or `fallback_models`) to the next model; a missed budget does not
  rest the endpoint. Plans mark a model `slow` when every working endpoint is over its `slow_ms`.
- **Limit:** the state is per server process (like the semantic judge chain's provider suspension): instances
  learn about an outage separately, and a restart forgets it.

## Not done yet (do before a shared key serves other people)

- Per-user / per-org **budgets** and rate limits, and an **audit row per call** (only tracing exists).
- A database store and an admin page for connections; key storage in a secret manager.
- Streaming, and polling of long-running A2A tasks (an unfinished task returns its `state`).
- DNS-rebinding is narrowed (checked at call time) but not closed; restrict egress at the network layer too.
- The A2A adapter follows the published 1.0 spec (with a 0.x `message/send` fallback) and has not been run
  against a live agent. The OpenAI-compatible adapter has not been run against a live host either.
