# Data inventory and retention

**DRAFT.** The "Retention" column holds proposals unless marked [code]. Retention is a decision to make, and it feeds the DPA. Three rules compete: a short purge of customer data, CERT-In's 180-day log rule, and the audit evidence a gain-share contract needs. Decide them together.

| # | Data | Where (table or service) | Class | Who can read it | Retention (proposed) | Deletion |
|---|---|---|---|---|---|---|
| 1 | Account email, user id, sign-in sessions | Supabase Auth | Personal data | Supabase, the user, platform admins | While the account exists | Account deletion |
| 2 | Organisation membership and roles | `organizations`, memberships (migration 136, 99) | Personal data | Org admins | While the member exists | Removal by admin |
| 3 | Procedures and goals contributed publicly | `procedures`, `goals` | Public content | Everyone | Until withdrawn | Withdrawal closes the validity window; nothing is hard-deleted **[code: bi-temporal design]**. A hard-delete path for legal requests is **not built** |
| 4 | Private or org procedures and claims | same tables with `visibility` | Customer content | Owner or org only | Contract term plus 30 days | Needs a hard-delete and export path **[gap]** |
| 5 | Query hash and which procedures were returned | `retrieval_decisions` | Usage metadata | Platform only | 180 days (matches CERT-In log rule) **[proposal]** | Scheduled delete job **[not built]** |
| 6 | Model plan, outcome, tokens, cost, latency | `routing_decisions`, `routing_observations` | Usage metadata, no content | Owner/org by `visibility`; aggregate use in model fitting | Fitting needs history; keep with the account, aggregated when the contract ends **[proposal]** | By `owner_id` **[needs a tested path]** |
| 7 | Provider call ledger | `provider_call_ledger` | Usage and billing | Org admins | 8 years for billing records, or as tax counsel advises **[proposal]** | Not deleted during that period |
| 8 | Customer model credentials | Customer's own secret store, referenced as `env:NAME` | Secret | Not stored by us | Not applicable | Not applicable |
| 9 | Prompts and outputs of `call_model` | Memory during the call; the provider's service | Customer content | The model provider only | Not stored by us **[code]**. Provider's retention applies **[unverified]** | Provider's process |
| 10 | Audit events (publication, deletion, access denied, break-glass) | `audit_events` | Security log | Platform security | 180 days in India-hosted storage if CERT-In applies **[proposal]** | Scheduled delete job **[not built]** |
| 11 | Application logs, error reports | Host logs, Sentry if configured | Operational | Engineers | 30 days **[proposal]** | Host setting |
| 12 | Backups | Neon point-in-time restore | Copy of 3–7, 10 | Platform only | Provider's window **[unverified]** | Expires with the window |
| 13 | Embeddings of procedures | Postgres `vector` columns | Derived content | Same as the source row | Same as the source row | With the source row |

## Open items

1. **Deletion.** Withdrawal is not deletion. For a customer exit or a DPDP erasure request we need a tested hard-delete and export path, and the append-only design must be reconciled with it. Counsel should say which records may lawfully stay.
2. **Scheduled jobs.** Rows 5 and 10 name a retention but no job enforces it. Do not state a number to customers until one runs.
3. **Backups** keep deleted data until they expire. The DPA must say so.
4. **Customer data in shared model fitting.** Row 6 feeds a model shared across customers. State what is shared: it is counts and outcomes for a goal, not content. Allow an org to opt out of contributing.
