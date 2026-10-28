# Account deletion (ledger anonymised, the rest deleted) and a global spend ceiling that scales

Written 2026-10-28 from the code. Nothing here is built yet except where it says "exists". Founder decisions today: on account deletion, anonymise the usage ledger and delete the rest; the global daily ceiling needs a proper plan, not the single $30 monthly cap.

## Part A. Account deletion and export

### What exists (this corrects an earlier note that no route was found)
- `backend/app/services/data_rights.py` has `export_user_data`, `plan_user_deletion` and `delete_user_data`. `backend/app/api/me.py` serves `GET /v1/me/export`, `GET /v1/me/deletion` (dry run) and `POST /v1/me/deletion`. Requests are recorded in `data_requests`; a legal hold blocks deletion.
- It covers private procedures (physical delete, or tombstone if published), the opt-in contributor profile, and an audit trail. It does NOT cover the provider usage ledger, denials, org memberships, the personal fields of the `users` row, or about 70 other tables.
- Those routes are on the full API app only. The hosted MCP service, which the site talks to, does not serve them.

### Inventory
75 tables in `backend/db/*.sql` carry a user key (`owner_id`, `actor_subject`, `actor_id`, `user_id`, `created_by`, `email`, `contributor_id`). By what to do:

| Group | Examples | Rule |
|---|---|---|
| Identity | `users` (email, display_name), `org_memberships`, `contributor_profiles`, `username_history`, `platform_role_grants`, `break_glass_grants`, `contact_requests` | delete memberships and profile rows; blank `users.email` and `display_name`, set `is_active = false`; keep the row id and (issuer, subject) so foreign keys hold and the person cannot silently re-attach |
| Usage ledger | `provider_call_ledger.actor_subject`, `org_denials.actor_subject` | ANONYMISE: replace actor_subject with `deleted:` plus 12 hex of sha256(subject + a server-side pepper). Spend totals and org budgets stay true; the link to the person goes. The pepper is a server secret so subjects cannot be guessed back |
| Audit | `audit_events.actor_subject` | hash-chained and append-only: do NOT rewrite. Keep the event with its opaque identifier, say so in the Privacy Policy, and fix the retention period with counsel |
| Private content | traces, evidence, goals, procedures, observations, routing_observations, executions, change_sets, ingestion, search indexes (owner_id) | delete rows the person owns where visibility is private. Several tables refuse deletes by trigger (`tg_procedures_refuse_delete`, `tg_evidence_append_only`, `tg_change_sets_append_only`, `tg_executions_frozen`); each needs an erasure branch in its trigger first, the same pattern as the ledger's `sl_erasure_authorized` |
| Public contributions | published procedures, Global candidates | stay (existing rule in `data_rights.py`): the commons is not deleted when a person leaves; cut the creator link by anonymising `created_by` |
| Credits | `credit_ledger_events.contributor_id` | financial record: keep, with the anonymous id, per tax rules |

### Build order (each step ships with a proving test against a REAL Postgres, not the fake-pool tests)
1. Migration (next free number is 156): add `sl_account_erasure_authorized(p_subject)` (a transaction-local setting `app.account_erasure` equal to the subject, and no legal hold on it). Relax `tg_ledger_guard` and `tg_denials_guard` to permit exactly one UPDATE shape: `actor_subject` changing to a `deleted:%` value with every other column unchanged. Migrations are immutable once applied, and the container applies new ones at start, so test this on a Neon branch first. It is deliberately NOT committed yet.
2. Service: extend `delete_user_data` to run, in one transaction, the memberships delete, ledger and denials anonymisation, users PII blanking and profile delete. Return a manifest listing every table NOT yet covered (the `ERASURE_REGISTRY` idea in `org_governance.py`). A request reaches `completed` only when nothing is blocked, so we never print a false "your data is deleted".
3. Mount `GET /v1/me/export` and `GET|POST /v1/me/deletion` on the MCP host behind the website-session gate and CORS (like `/v1/delegate`). The last step is deleting the Supabase auth user through its admin API.
4. Frontend: Account, "Delete my account": shows the dry run, requires typing the email, calls POST, signs out.
5. Work through the blocked private-content tables one at a time (trigger erasure branch, registry entry, test).

Until step 5 is finished the Privacy Policy must say plainly which data is deleted at once and which is anonymised or retained.

## Part B. Global spend ceiling that scales

Today: one org `kell-pilot`, $1 per user per day, $30 a month for everyone. That is a smoke detector, not a plan. With 1,000 sign-ups $30 is three cents each, and one heavy day can exhaust it for everybody.

### Layers, cheapest first
1. **Provider side (hard, already true).** Both providers are prepaid credits, so spend can never exceed the balance. Set low-balance alerts on both consoles and keep the second provider funded.
2. **Free pool and paid pool.** Separate organizations. `kell-free` holds free accounts with a budget we choose as a marketing line. Paid users sit in per-user or per-org pools funded by their own payments. When the free pool is spent, free users get a clear message ("free allowance for today is used; resets 00:00 UTC") and paid users are unaffected. This means generalising `pilot_enrol` to choose a pool.
3. **Daily org ceiling.** `org_policies` has only `monthly_budget_usd` and `per_user_daily_budget_usd`. Add `daily_budget_usd` (a migration, plus one check in the reservation path in `providers/service.py`, beside the monthly check). Set monthly to about 30 times daily so one bad day cannot eat the month.
4. **Tiered per-person allowance.** New accounts get a small allowance (for example a quarter of the daily cap) until the email is verified and the Google or GitHub account is older than a threshold; then the normal cap. This blunts throwaway-account farming.
5. **Velocity breaker.** If spend in the last hour exceeds a set fraction of the daily ceiling, or one address or person is a large share of it, automatically switch the free pool off with its kill switch and alert the founder. The kill switch exists (org policy and admin API); the breaker is a small scheduled check over the ledger.
6. **Sizing.** Budget = expected active users x observed average spend x 2. Measure the average over the first two weeks (the ledger records per-call cost) before choosing; most people will use far less than the cap.

### Numbers needed from the founder
Monthly marketing budget for free usage; whether free stays limited to the two models now offered; whether paid credits start at launch.

### Order
Layers 3 and 5 before any public sign-up; layer 2 together with payments; layer 4 with sign-up friction; layer 6 after two weeks of data.
