# StealthLab setup — enterprise rollout

Who this is for: whoever at a customer (for example a company of about 2,000 people) decides how StealthLab reaches
their developers, and whoever at StealthLab runs the rollout. It describes what is in the repo, how a rollout uses it, and
what is **not** done. Where something is unproven it says so.

**Status in one paragraph.** The flow below is built end to end and tested offline: the company's admin connects the
company's provider key once, applies a policy and a budget, and creates an invite; a developer runs one command. It has
has been run end to end **locally** (section 11) against a scratch Postgres and one real provider call, but **not** on staging, with a real identity provider, or inside a real Cursor, and nothing is deployed. Section 8
lists the exact gaps.

---

## 1. The idea in one picture

```
   CTO / admin (browser)                         StealthLab server                          model provider
┌──────────────────────────┐   1 connect key   ┌─────────────────────────────┐
│ admin console  /start    │──────────────────►│ org_provider_keys (encrypted)│
│  2 apply policy + budget │──────────────────►│ org_policies                 │
│  3 create invite         │──────────────────►│ org_invites (hash only)      │
└──────────────────────────┘                   │                              │
                                               │  /v1/join                    │
 developer (terminal)                          │  /v1/chat/completions        │──── company's key ───►
┌──────────────────────────┐  join <invite>    │   policy → budget → ledger   │
│ npx stealthlab-mcp join …│──────────────────►│                              │
│ sign in with company login│  model list      └─────────────────────────────┘
│ no provider key here     │◄──────────────────
└──────────────────────────┘
```

The provider key lives in one place (the server, encrypted). A developer's machine holds only their own StealthLab sign-in.
Every model call goes through the gateway, so the company's policy and budget apply to it and every call is in the ledger.

## 2. What the developer does

```powershell
npx -y stealthlab-mcp join <invite> --url <https://mcp-host/mcp> --api <https://api-host> [--cursor-model <model>]
```

The admin console prints this whole line, so a developer copies and runs it. It then:

1. signs the person in (browser, the company login) unless a token is already saved;
2. redeems the invite: the server adds them to the organization as a `member` and answers with the models, their prices and
   the gateway address;
3. writes `~/.stealthlab/exec.json` with one profile per model and `key_from: "stealthlab"` — no provider key, no environment
   variable. A file the person wrote themselves is kept as `exec.json.before-join`, never overwritten;
4. registers their agents (Cursor, Claude Code, ...) with the hooks and dispatch;
5. makes **one tiny call through the gateway** with the cheapest model. That proves the sign-in works, the policy allows the
   model and the stored key works, and its ledger row is what the dashboard counts as "ready".

`--no-exec` connects and tests but leaves cheap-model dispatch off. It ends with a readiness list. **Exit 0 = ready, 3 = something is missing**, with the fix next to each item (gate a
deployment script on that). `--dry-run` changes nothing. Re-running is safe.

Still manual: name the Cursor model (`--cursor-model`; `auto` is not a model, so dispatch stays off without one), and restart
Cursor completely.

The older `setup` command (developer-held keys: the person sets `GENERAL_COMPUTE_API_KEY` themselves) still exists for
people with no organization; it recognises `join` profiles and does not ask for a key.

## 3. What the CTO / admin does

Sign in to the admin console (`admin/`, port 3200) and open **Get started** (`/start`). Owner or admin role is needed.

| Step | What you do | What happens |
|---|---|---|
| 1. Connect your provider key | Provider, API address (https), the key, and the models with their prices | The key is encrypted (AES-256-GCM) and stored; it is **never shown again** — the page keeps only the last four characters. Remove it any time; that deletes the ciphertext. |
| 2. Set the budget | Monthly and per-developer-daily dollars (there is no default; 0 means no spend) | **Apply policy** builds the policy from exactly what you connected: those providers, those models, the tools the gateway uses, the data classes you declared, your budgets. It takes effect at once. Fine-tune under **Policy**. |
| 3. Invite developers | A label, how many days, how many people | Shows the command **once**. Only a hash is stored. Revoke any time (people who already joined stay members). |
| 4. Watch | The four numbers at the top | Provider keys, policy applied, joined, **ready** (made a successful call). Plus a plain "Next:" line. Usage, Calls, Denials, Audit, People and the kill switch are the existing pages. |

Set `NEXT_PUBLIC_KEL_MCP_URL` for the console so the invite command carries the MCP address.

## 4. What the operator (StealthLab) does, once per customer

There is no self-serve sign-up yet.

1. Deploy the backend, run migrations (`python scripts\migrate.py`; this adds **151**), and set in `backend/.env`:
   - `ORG_KEY_ENCRYPTION_KEY` — base64 of 32 random bytes (command in `.env.example`). **Back it up**: without it the stored
     keys cannot be read and admins must re-enter them.
   - `PUBLIC_API_URL` — the address developers' machines reach the API at.
2. The customer's owner signs in to the admin console once (that creates their user).
3. Create the organization and make them the owner:
   ```powershell
   python scripts\provision_org.py --name "Example Company" --slug example --owner-email cto@their-company.com
   ```
   (`--dry-run` first. It refuses an unknown email and creates no policy: until the owner applies one, every call is denied.)
4. Tell the owner to open **Get started**.

## 5. Phased rollout for a ~2,000-person company

| Phase | Who | Change | Move on when |
|---|---|---|---|
| 0. Prepare | StealthLab | Host the server, publish the package, run the flow on our own machines in a real Cursor | the join self-test passes on a real machine |
| 1. Watch | 10–20 volunteers | Invite with a small budget and `join --no-exec` (connected and tested, dispatch off) | a week of real tasks |
| 2. Dispatch | same group | Dispatch tries cheap models first; the check decides | pass rate and added time acceptable to them |
| 3. One team | 100–200 | A second invite with a larger cap | a measured saving the CTO can read |
| 4. Everyone | up to 2,000 | The company's IT pushes the one command | the §8 items for scale are done |

Check first with the customer: if they use Cursor on flat seats for decks and Q&A (as the pilot plan records), a dollar saving
may be zero, and the dashboard's spend then measures only the gateway's metered calls. Decide the measure before phase 1.

## 6. Files

### On a developer machine

| Path | What it is |
|---|---|
| `~/.stealthlab/config.json` | MCP address, `api_url`, `organization_id`, token and refresh token (0600), `cursor_model`, `dispatch` |
| `~/.stealthlab/exec.json` | the models and prices from the server; `managed_by: stealthlab-join`; **no key** |
| `~/.stealthlab/exec.json.before-join` | the person's own file, if there was one |
| `~/.stealthlab/hooks.log`, `runs/`, `outbox/`, `hooks/` | hook failures, dispatched runs, queued reports, ids only |
| `~/.cursor/hooks.json`, `~/.claude/settings.json` | where the hooks are registered |
| `<repo>/.kell/` | the plain-text knowledge files (`claims.md`, `procedures.md`, `run.md`, ...) |

### In the repo

| Path | Role |
|---|---|
| `packaging/npm/lib/join.mjs`, `bin/stealthlab-mcp.mjs` (`cmdJoin`) | the `join` command |
| `packaging/npm/lib/executors/profile.mjs` | `key_from: "stealthlab"` profiles |
| `packaging/npm/lib/setup.mjs` | older `setup` (developer-held keys) and its checklist |
| `packaging/npm/test/join.test.mjs` | 13 tests, including the whole command against a stub server |
| `backend/db/151_org_provider_keys_and_invites.sql` | `org_provider_keys`, `org_invites` (strict row-level security) |
| `backend/app/services/secret_box.py` | AES-256-GCM, bound to the org and connection |
| `backend/app/services/org_keys.py` | put / list / delete keys; the store and `orgkey:` resolver the gateway uses |
| `backend/app/services/org_onboarding.py` | invites, redeem, client config, recommended policy, checklist numbers |
| `backend/app/api/org_admin.py` | the routes (below) |
| `backend/scripts/provision_org.py` | create an organization and its owner |
| `backend/tests/test_org_onboarding_offline.py` | 41 offline tests |
| `admin/app/start/page.tsx`, `admin/lib/onboarding-api.ts`, `admin/tests/onboarding-api.test.ts` | the console page |
| `backend/app/api/chat_completions.py`, `app/providers/` | the existing governed gateway the calls go through |

### Routes

Admin (owner/admin): `GET|PUT|DELETE /v1/orgs/{org}/provider-keys[/{id}]`, `POST /v1/orgs/{org}/policy/recommended`,
`GET /v1/orgs/{org}/onboarding`, `GET|POST /v1/orgs/{org}/invites`, `DELETE /v1/orgs/{org}/invites/{id}`.
Any member: `GET /v1/orgs/{org}/client-config`. A signed-in person with an invite: `POST /v1/join`.

## 7. What leaves a developer's machine, and who can see what

- **Provider key:** never on a developer machine. The server decrypts it for one request; it is not returned by any route, not
  in audit rows (they record the provider, base address, models and whether the key was replaced), not in logs.
- **Invite code:** sent only to `<api>/v1/join`; stored as a SHA-256 hash; one generic error for unknown, expired, revoked or
  used-up codes.
- **Prompts:** with central keys, the cheap-model prompts go through the company's own gateway to the company's provider. The
  Cursor hook separately sends the prompt (up to 1,500 characters) and `.kell/claims.md` to the StealthLab endpoint for the
  lookup, while `packaging/npm/README.md` says only run reports leave. That contradiction is **still unresolved** and a
  security reviewer will find it; there is no per-organization switch for it yet.
- **Admins** see usage by person, calls, denials and the audit log (hash-chained, append-only), all existing pages.

## 8. What is not done

| Gap | Today |
|---|---|
| Not run for real | Run locally (section 11): migration 151 on a scratch Postgres 17, the API, a local test identity, the real `join` command and one real provider call. Not run: staging or production, a real identity provider (Supabase sign-in), a real Cursor, the admin console in a browser, or concurrent invite redemption. |
| Deployment | Nothing is hosted. `PUBLIC_API_URL`, the package's default address and the install links are unset or placeholders; the npm package is unpublished and `UNLICENSED`. |
| Two addresses | The MCP server and the API are separate services, so `join` needs both (`--url`, `--api`); the console prints them. A single address would need the two behind one host. |
| Key handling | One encryption key, no rotation job, no customer KMS, no test call when a key is saved (the first real check is the join self-test). The server process can read every key. |
| Multi-organization people | A person in two organizations gets an ambiguous-membership error on identity routes; the self-test sends `X-Stealthlab-Org`, but the hooks' dispatch calls do not yet. |
| Membership | No removal, role change or SSO/SCIM provisioning; a leaver keeps access until a database edit. Everyone joins as `member`. |
| Claude Code via the gateway | Central keys serve the stealth/opencode-style profiles (OpenAI shape). Claude-Code-driven runs need an `anthropic_base_url` profile; the `/v1/messages` route exists but is not wired into `join`. |
| MCP `call_model` | The org key store is registered in the API app only, not in the separate MCP server process. |
| Wallet and billing | Spend is the company's own provider bill; StealthLab does not meter or invoice it. |
| Compliance | SOC 2, a data processing agreement, a pen test, India data residency and a load test of the abuse limiter are open. |
| Evidence | No saving measured on a customer's real sessions. |

## 9. Commands

```powershell
# backend (from backend\)
python scripts\migrate.py --status
python -m pytest tests\test_org_onboarding_offline.py -q          # 41 pass
python scripts\provision_org.py --name "<Company>" --slug <slug> --owner-email <email> --dry-run
# client (from packaging\npm)
node bin\stealthlab-mcp.mjs join <invite> --url <mcp> --api <api> --dry-run
node --test test\join.test.mjs                                    # 13 pass
# console (from admin\)
npx vitest run ; npx tsc --noEmit
```

## 10. The shared plan (`.kell/collab/`)

The work above is also written as a plan that people and coding agents can pick up, in the `plan_and_run` format the rest of
`.kell/` uses: `claims.md` (18 facts, each with file, line and blob sha), `procedures.md` (the operator / owner / developer
procedure) and `run.md` (14 nodes). It is the only part of `.kell/` tracked in git (`.gitignore` ignores `.kell/*` except
`collab/`); everything else there is a generated, machine-local projection. `stealthlab-mcp plan validate` passes on it (14 nodes,
5 steps, 0 errors). To take a node: set it `running`, do it, run its `check=`, set it `done`, add what you learned as a claim.

| Node | State | What |
|---|---|---|
| N-1 .. N-5 | done | migration 151; backend services, routes and script; `join` client; the Get started page; this document |
| N-6 | ready | apply migration 151 to staging |
| N-7 | ready | real-database e2e test (row-level security, single-use invite, encrypted round trip) |
| N-8 | ready | one statement of what leaves a developer's machine, plus a per-organization switch |
| N-9 | blocked on N-6 | host the API and MCP server, set `PUBLIC_API_URL`, publish the package |
| N-10 | blocked on N-9 | first run of `join` on a real machine with Cursor |
| N-11 | ready | register the organization key store in the MCP server process |
| N-12 | ready | send `X-Stealthlab-Org` from hook dispatch calls |
| N-13 | ready | member removal, role change and SSO provisioning |
| N-14 | ready | key rotation and a test call when a key is saved |

This folder is pushed, so it holds no secret, customer name or price.

## 11. Run the whole thing locally

Everything below runs on one machine, with no hosted database and no hosted identity provider. It was run on 2026-10-22 and
passed (`ALL OK`); that run found and fixed two real bugs the offline tests could not (below).

```powershell
# 1. a scratch Postgres with pgvector (PostgreSQL 17 here), on loopback only
pg_ctl -D C:\Users\<you>\pg-scratch\data -o "-p 55432 -c listen_addresses=127.0.0.1" -l pg.log start
psql -h 127.0.0.1 -p 55432 -U kel_owner -d postgres -c "CREATE DATABASE kel_orgtest"
cd backend ; $env:DATABASE_URL="" ; python scripts\migrate.py --dsn postgresql://kel_owner@127.0.0.1:55432/kel_orgtest

# 2. the REST API with a local test identity and DEPLOYMENT_MODE=shared (refuses any non-loopback database)
python experiments\abtest\run_org_stack.py --mint cto ; python experiments\abtest\run_org_stack.py --mint dev
python experiments\abtest\run_org_stack.py                       # http://127.0.0.1:8000, leave it running

# 3. the whole flow, with the real CLI in a throwaway home (needs GENERAL_COMPUTE_API_KEY in the environment or backend\.env)
python experiments\abtest\org_flow_check.py
```

`org_flow_check.py` signs in two test users, provisions the organization, connects the provider key as the CTO, applies the
policy, creates an invite, runs `stealthlab-mcp join` as the developer (one real 8-token call through the gateway, a fraction of a
cent), then checks: the key is not on the developer machine or in plain text in the database, one settled call is in the ledger,
the dashboard counts show 2 joined / 1 ready / running, a member cannot read the keys, a wrong or revoked invite gets 404, and the
kill switch stops the gateway and resumes it.

Not covered locally: the admin console in a browser (it signs in through Supabase; its code is covered by vitest), a real Cursor,
concurrent invite use, and the MCP server (`join` does not call it).

Two bugs this run found, both fixed:
1. Saving a key failed with a database CHECK error: the units were JSON-encoded before the pool's jsonb codec encoded them again,
   so they were stored as a JSON string. The offline fake could not see this.
2. In any mode other than `shared`, the gateway does not govern calls, so an organization's key was spent with **no policy, budget
   or ledger row** (the call worked and nothing recorded it). The key store now serves organization keys only in `shared` mode; a
   hosted deployment must set `DEPLOYMENT_MODE=shared`.
