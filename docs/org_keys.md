# One place for the organisation's keys: what to build for the pilot, and what not yet

Written 2026-10-16. The idea (from the owner): the enterprise enters its provider keys once, in one interface, and developers
get one key, so no developer handles a provider key. A reply from another assistant described this as "org-level virtual API
keys / a key vault" and called it the gold standard and "low-risk BYOK". This note checks that against the repo and a live test.

## What the proposal gets right

- Developers should not hold provider keys: leaks, offboarding and rotation all get worse with one key per person.
- One interface for the admin, per-team keys with their own budgets, one-click revocation, attribution per team.
- Pluggable secret stores (a cloud secrets manager, the customer's KMS) for the strictest customers.

## Where it is wrong or too loose for this product

1. **"Keeps the deal in the low-risk BYOK category" is false.** Holding a customer's provider keys makes us a credential
   custodian: one breach exposes every customer's keys, and it needs encryption at rest, a KMS, access auditing and a
   security review. The saved product decision is BYOK by default plus a managed tier of open models on OUR contracts; custody of
   customer keys is a third thing, and the heaviest.
2. **Traffic now passes through our servers.** Prompts and code snippets reach us in transit, so we are a data processor
   (DPA, subprocessor list, residency). The local-call design (key on the developer's machine, call the provider directly) does
   not have that exposure. The gateway trades key sprawl for data-path responsibility.
3. **"Breaks the task down via HTN"** is not how dispatch works; do not promise it.
4. **A virtual key is still a secret on each developer's machine.** It shrinks the blast radius (scoped, revocable, budgeted) but
   does not remove the need to keep it out of git. It also needs to be short and revocable, which the current 600-character JWT
   is not (see "Virtual keys" below).
5. **"Integrations with AWS Secrets Manager / HashiCorp Vault"** is a later enterprise feature, not a pilot one.

## What already exists (verified 2026-10-16)

- `app/api/chat_completions.py`: an OpenAI/Anthropic/Responses-compatible gateway at `/v1` on the MAIN API app (not on the MCP
  server app that the hooks talk to; in local runs that is `app.main:app` vs `app.mcp_server.server:app`).
- `app/providers/*`: governed provider connections. A connection stores a credential REFERENCE (`env:NAME`), never the secret;
  `credential_owner` is `platform` or `customer`; `secrets.py` has a pluggable resolver ("an encrypted column, a customer KMS can be
  added"); connections can come from a JSON file, a database table the Providers page writes (org-owned connections shadow platform
  ones), or an agent registry.
- `app/services/provider_policy.py`: a fail-closed egress policy table (`model_provider_policies`): no row, no call.
- Org governance (`app/api/org_admin.py`): policy, kill switch, usage per org and per user, budget, calls, denials, audit, legal holds.

## Live test (scratch Postgres, local servers, 2026-10-16)

- A platform-owned General Compute connection (key only in the SERVER's environment) plus one `model_provider_policies` row.
  `GET /v1/models` listed the three models with prices; `POST /v1/chat/completions` with only a StealthLab token returned 200 with
  `x-stealthlab-cost-usd`. Without the policy row it answered 403 `policy_denied` (fail closed, as designed).
- With `stealthlab-mcp setup --gateway` (this change), a Cursor-style dispatch ran end to end with NO provider key anywhere on
  the developer side: "Done by minimax-m2-7 in 58 s ... $0.0007", change applied, tests pass. The gateway log shows the
  `/v1/chat/completions` calls.
- A warning to fix: `unknown provider 'generalcompute' for cost estimation; using worst-case rate` (the governance estimator does not know
  the provider, so budget checks use a worst-case rate).

## Verdict for the pilot

Build the part that removes key handling from developers WITHOUT taking custody of the customer's provider keys:

1. **Managed gateway mode (done here):** the provider keys sit on the server (ours, under our contracts), the developer holds only the
   StealthLab token, and `setup --gateway` writes `exec.json` from the gateway's own model list. The admin controls which models
   exist (the connection list) and where data may go (the policy table). The pilot's spend is tiny, so we can carry it.
2. **Not yet:** customer-owned provider keys in our store. When a customer insists, add a per-tenant encrypted secret resolver
   (`secrets.py` already anticipates it) with a KMS-wrapped data key, write-only entry in the admin UI, access auditing, and rotation.
   Until then a customer who wants its own keys can run its own connection file (`credential_owner: customer`, `env:` reference) on
   a server it operates.

## Still to build (needs the backend/auth owner; not done here)

- **Virtual keys:** opaque, short (`sl_` + 32 random characters), stored hashed, one per team or project, with scopes, an expiry,
  a per-key budget, instant revocation and `last_used_at`. They would also replace the 600-character JWT the hooks use. This touches
  the authentication middleware and the usage ledger (attribute spend to a key), so it needs the auth lane's review; no table for it exists.
- **Admin form for keys** (only when custody is decided), and **per-team budgets** (budgets are per org and per user today).
- **Deployment shape:** the gateway and the MCP server are different apps/ports; a hosted deployment must expose both under one host
  (the CLI derives the gateway as the server's origin + `/v1`, or takes `--gateway-url`).
- Seed the `model_provider_policies` rows (and the provider connection) per environment; without them every call is denied.
