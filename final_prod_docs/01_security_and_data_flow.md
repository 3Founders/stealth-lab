# Security and data flow (one page)

**DRAFT. Tags: [code] checked in the repository, [config] true if deployed as documented, [unverified] not yet confirmed.**

## What the service is

A hosted MCP server. A coding agent (Claude Code, Cursor, Codex, opencode, ChatGPT) calls tools to find proven procedures for a task and to choose a model. Source: `backend/app/mcp_server/server.py`. Seven tools: `find_ways`, `recommend_models`, `report_model_run`, `report_result`, `call_model`, `report_discovery`, `submit_way`.

## 1. Are prompts or code stored on your servers?

| Tool | What we receive | What we store |
|---|---|---|
| `find_ways` | A task description, optional repository facts | A SHA-256 hash of the query, the viewer id, which procedures were returned, and whether the result was degraded (`retrieval_decisions`, migration 117) **[code]**. We do not store the query text in that table **[code]**; confirm no other log keeps it **[unverified]** |
| `recommend_models`, `report_model_run`, `report_result` | Model name, whether the attempt was accepted, token counts, cost, latency | Those numbers, tied to a goal, a model and the caller (`routing_observations`, `routing_decisions`) **[code]**. No prompt or output text |
| `call_model` | The prompt and any system text, sent to the model provider the customer configured | Tokens, cost, provider, model, latency and status, not the prompt or the output (`provider_call_ledger`, migration 136) **[code]**. Prompt text is held in memory for the call. Provider-side retention is the provider's **[unverified: depends on each provider's terms]** |
| `report_discovery`, `submit_way` | Content the user chooses to contribute | The contribution itself, after a content screen, with the contributor's visibility choice (private, org, public) **[code]** |

**We do not offer a zero-data-retention guarantee.** We store derived data (procedures, outcomes, costs) by design. What we can say is narrower and checkable: customer prompts and model outputs sent through `call_model` are not written to our database, and `find_ways` stores a query hash rather than the query. Whether a model provider keeps a prompt is that provider's setting. We request zero-retention and no-training settings where the provider offers them **[unverified]**. A stronger sentence needs a signed agreement with each provider, which `06_claims_register.md` tracks.

Redaction: trace text passes one redaction function before storage (`backend/app/services/trace_redaction.py`). It catches known secret shapes and is a best-effort floor, not a guarantee **[code]**.

## 2. How is data encrypted?

- **In transit:** the service listens on plain HTTP behind the host's TLS terminator. The version and ciphers are the host's, so we cannot state "TLS 1.3" until the live endpoint is tested **[unverified]**. Database connections require `sslmode=require` in the documented setup **[config]**. Test with `nmap --script ssl-enum-ciphers` or SSL Labs after deployment and record the result here.
- **At rest:** database and object storage encryption is the provider's default (Neon, Cloudflare R2) **[unverified: confirm in each provider's documentation and record the link]**. No application-level encryption of stored content **[code]**.
- **Secrets:** customer model keys are never stored in our database, only a reference such as `env:NAME` that a resolver turns into the key at call time (`backend/app/providers/secrets.py`) **[code]**.

## 3. Where does it run?

| Component | Where | Source |
|---|---|---|
| MCP server | One container; host documented as Railway (`railway.mcp.json`, `docs/deploy/hosted-mcp.md`). Region not set **[unverified]** | repo |
| Database | Neon Postgres, us-east-2 **[unverified: from deployment notes, re-check the project's region]** | notes |
| Embeddings, judge models | Google Vertex AI (`global` / `us-central1`) **[unverified]** | notes |
| Object storage | Cloudflare R2 **[unverified]** | notes |
| Sign-in | Supabase Auth (OAuth 2.1 with consent page) | `backend/app/mcp_server/oauth_resource.py` **[code]** |

We do not claim India-only or any single-country processing. The database and some model calls run in the United States today. The customer agreement points to the DPA for locations.

## Access control and isolation **[code]**

- Every request is authenticated by an OAuth bearer token. The shared token works only in single-user mode.
- Private and organisation content is filtered by one shared function, `scope_predicates()`, and Postgres row-level security is the second layer. An end-to-end test (`backend/tests/test_routing_isolation_e2e.py`) checks that one user cannot read or report on another's instance.
- A model-plan handle (`instance_key`) is bound to the caller who received it.
- Retrieved content is screened for links, hidden characters and instruction phrases, escaped, and marked as untrusted data in tool results.
- `call_model`: egress policy, server-address checks against SSRF, a worst-case cost cap before each call, per-organisation budgets, a kill switch and an audit ledger (migration 136).
- The consent page refuses to be framed and warns on a localhost redirect or an unnamed app.

## Known gaps (full list in `06_claims_register.md`)

No independent penetration test. No SOC 2 or ISO 27001. Single instance, so no failover. Database roles with least privilege are documented in `docs/security_runbook.md` but not yet created. No single sign-on or SCIM for customer staff.
