# Team and org UI + onboarding research

Date: 2026-11-17
Scope: `routing_kell_frontend/`, `admin/`, the org APIs in `backend/app/api/org_admin.py` and
`backend/app/services/org_onboarding.py`, plus the existing notes in
`docs/team_enterprise_admin_research.md`, `docs/ship_readiness_plan.md`, `docs/frontend_remaining.md`,
`docs/security_runbook.md`, and `docs/ui_audit_2026-11-08.md`.
Method: read the code and the docs above, then a web research sweep of vendor documentation (Zapier,
Microsoft 365/Entra, AWS, Google Workspace/Gemini Enterprise, Slack, GitHub, Linear, Vercel, Cursor,
Notion, Atlassian, WorkOS, OpenAI, Anthropic, Microsoft Foundry, AWS AgentCore) and onboarding/trust/
billing UX guidance. Vendor facts are cited at the end. Where a source was a summary rather than a
primary page, it is marked. Nothing here is legal or security advice.

---

## 0. Executive summary

kell is strong at the **single developer + chat + routing** surface and weak at the **organisation
setup** surface. The market has converged on a small set of patterns:

1. **A role-based checklist, not a product tour.** Three to six binary tasks, auto-completed anywhere
   in the product, a count and a bar, the next step expanded, finished steps ticked, hideable.
2. **Empty states that explain and act.** Every zero-state says what will appear, why it is empty, and
   gives one action (create, template, import). Sample data is labelled.
3. **An invite state machine.** Pending / accepted / expired / revoked, with the role explained at the
   point of invitation, resend and revoke, and a clear distinction between "invitation sent" and
   "member added".
4. **Identity onboarding is a sequence.** Verify the domain (DNS TXT), enable SSO, optionally turn on
   SCIM, test joiner/mover/leaver, then enforce. Most vendors put SSO on Teams and SCIM/audit on
   Enterprise; WorkOS sells the whole flow as an embeddable Admin Portal.
5. **An admin centre with five groups.** Insights (usage, audit, streams), Governance (permissions,
   data policy, approvals, model providers), User management (members, teams), Security (domains,
   SSO, SCIM, MFA), Organisation (billing, profile, features).
6. **Agent onboarding is identity onboarding.** The serious platforms give each agent its own
   identity, owner/sponsor, scoped permissions, credential, budget, and audit trail. Foundry uses an
   Entra agent identity; AgentCore uses an agent identity directory + token vault + on-behalf-of
   exchange; Gemini Enterprise shows a SPIFFE ID or Agent Registry resource ID.
7. **Trust is a page, not a badge.** Scope, dates, subprocessors, DPA, pen-test summary, SLA history,
   incident disclosures, and an explicit public-vs-NDA boundary.
8. **Billing is role-specific.** Workspace admin, billing contact, finance/AP, and ordinary user each
   need a different view; upgrades apply now with proration, downgrades at renewal; seat additions
   prorate and removals take effect next cycle; a downgrade lists what turns off, goes read-only,
   stays over limit, or is deleted, with counts and dates.

kell's biggest UI/onboarding gaps, in order:

1. **No org first-run checklist.** `/org` opens on a dashboard; setup is scattered across Policy,
   Members, Keys, and the chat.
2. **No member lifecycle UI.** Invites exist, but no role choice, no email, no remove/deactivate, no
   session/key revocation, no pending-invite state machine.
3. **No agent onboarding or registry.** Agents are added inside chat; there is no page to see owner,
   sponsor, scopes, data ceiling, budget, status, or revoke.
4. **No approvals inbox.** The web/desktop approval cards were removed in `ac227bef`; the backend
   `/v1/tools/approvals` still exists.
5. **No evidence/receipt surface.** Procedures, checks, outcomes, and the signed audit chain are not
   visible to an admin.
6. **No trust page, status page, or security contact page.**
7. **No org billing/seat page.** Plans live on `/pricing` and `/account/billing`; a team org has no
   page for seats, invoices, tax IDs, or plan changes.
8. **No MFA setup for org owners/admins.** The only TOTP path is the separate `admin/` console's
   billing gate.

---

## 1. The onboarding model

### 1.1 Stages

| Stage | Time | Goal | Kell surface |
|---|---|---|---|
| 0. Evaluate | minutes | Understand the product, the price, the data path, the trust posture | `/`, `/pricing`, `/docs`, `/connect`, legal pages, trust page (missing) |
| 1. Create account / org | minutes | Establish identity and the workspace | `/sign-in`, `/join`, operator provisioning, Pilot team order |
| 2. First value | minutes–hours | One real task through the gateway, with a result the person trusts | `/connect`, `/console`, `/chat`, `/route` |
| 3. Team setup | hours–days | Invite the team, connect provider keys, set policy | `/org` (missing checklist) |
| 4. Governance | days–weeks | Roles, approvals, audit, retention, SSO/SCIM | `/org` + `admin/` (partial) |
| 5. Expansion | weeks | More agents, more tools, more teams, chargeback/showback | `/org/agents`, `/org/evidence`, billing (missing) |

The market benchmark for B2B SaaS activation is **38% median / 61% top quartile**, and time-to-value
varies sharply by contract size: **11 minutes** for accounts under $5K ARR, **2.4 days** for $5–25K,
**9 days** for $25–100K, and **23 days** for $100K+. kell's natural activation event is not "signed
up"; it is **one verified task through the gateway** (a call that passed its check), plus **a second
seat invited** for a team.

### 1.2 Roles

The market converges on five human roles plus a non-human principal:

| Role | Job in onboarding | Sees | Cannot |
|---|---|---|---|
| **Owner** | Create org, connect billing, assign owners, transfer ownership | Everything | — |
| **Admin** | Setup, integrations, policy, users, agents | Everything except billing/ownership | Change billing, transfer ownership |
| **Billing** | Plan, invoices, payment method, tax IDs | Billing + usage totals | Read prompts, traces, agent content |
| **Member** | Use the product, connect their own tools, run tasks | Own work + shared rooms | Invite, change policy, see others' private work |
| **Viewer** | Read-only | Shared results | Write, run, invite |
| **Agent** | Act for a person or org | Only its granted tools/data | Anything outside its grant |

kell today has `owner/admin/member/viewer` in `auth_context.py` and `org_admin.py`, but no Billing
role, no role-change route, and no agent principal. `docs/team_enterprise_admin_research.md` already
recommends splitting Billing from Admin and adding a two-owner rule.

### 1.3 Role-based checklists

The checklist should be filtered by **role first, permission second, task third**, with a handoff
when the current role hits a limit:

- **Owner/Admin:** name the org, connect a provider key, set policy and budgets, invite members,
  connect an agent, run a first verified task, review the audit log.
- **Billing:** confirm the plan, add a payment method, add tax IDs, download the first invoice.
- **Member:** accept the invite, install kell, connect their tool, run a first task, see the result.
- **Viewer:** accept the invite, open a shared room, read a result.

Do not show a member the provider-key step or the billing step. Do not show an admin the "install
kell" step if they do not write code.

### 1.4 Metrics to instrument

Define the kell funnel now, so the UI can be judged:

```
org_created
  → provider_key_connected
  → policy_set
  → invite_sent
  → invite_accepted
  → agent_connected
  → first_call
  → first_verified_task          ← activation
  → second_member_active
  → 7-day return
```

Properties: `org_id`, `role`, `plan`, `agent_id`, `model`, `tool`, `data_class`, `outcome`,
`source` (operator / pilot / self-serve). Keep prompts and replies out of analytics.

---

## 2. What the market does

### 2.1 Vendor matrix

| Product | Onboarding spine | Admin centre | Identity | Audit | Billing | Trust | Notable |
|---|---|---|---|---|---|---|---|
| **Zapier** | Templates, guided templates, Copilot, blank Zap, drafts | Yes: dashboard, Insights, Governance, Users, Security, Organisation | Domain management, SSO, SCIM | Audit log + log streams (Enterprise) | Plans, usage | Trust centre | Governance includes app permissions, data policy, approval settings, AI model providers |
| **Microsoft 365 / Entra** | Setup wizard: apps, domain, users, licences; Setup page later | Entra admin centre + M365 admin centre | Entra ID, SAML/OIDC, Conditional Access, PIM | Entra sign-in/audit logs | Licences, billing | Trust centre | Tenant creation, domain, roles, access reviews |
| **AWS** | New sign-up (GitHub/Google/Amazon/Apple), first project, free plan; advanced sign-up for full control | Console, IAM Identity Center, Control Tower | IAM Identity Center, permission sets, account factory, SSO | CloudTrail, Config, Audit Manager | Billing, Cost Explorer | Trust centre | AgentCore: agent identity directory, token vault, OBO exchange |
| **Google Workspace / Gemini** | Admin console, domain verification, SSO, inbound SCIM | Admin console + Gemini Enterprise Agents page | Google identity, SSO, SCIM, SPIFFE/Agent Registry ID | Audit log | Billing | Trust centre | Gemini Enterprise registers ADK/A2A/Dialogflow agents; agent identity visible |
| **Slack** | Workspace creation, invites, channels | Org/workspace admin | SSO, SCIM, 2FA enforcement | Audit logs, 2-year retention, CSV/JSON, API | Plans, seats | Trust centre | SCIM creates/deactivates members and guests; billing starts on first sign-in |
| **GitHub** | Create org, invite, repositories | Org/enterprise settings | SAML/OIDC SSO, SCIM, 2FA, EMU | Audit log, streaming to SIEM | Billing managers, seats | Trust centre | IAM checklist: auth, provision, SSO, SCIM, 2FA, external collaborators, IdP groups, joiner/mover/leaver |
| **Linear** | Create workspace, invite, first issue | Settings > Administration | SAML + SCIM (Enterprise) | Audit log (~3 months) | Plans, seats | Trust centre | SCIM provisioning roles via IdP groups; admins cannot manage users once SCIM is on |
| **Vercel** | Create team, invite, import project | Team settings | SAML SSO, SCIM (Enterprise) | Audit log | Plans, seats | Trust centre | Eight team roles; owner-only role assignment; pending invites 7/30 days |
| **Cursor** | Admin setup guide: trust centre, SSO, SCIM, org/teams/groups, Git, MDM, dashboard | Admin dashboard | SSO Teams+, SCIM Enterprise | Audit logs Enterprise, SIEM, OTel | Team limits, pooled usage, billing groups | Trust centre | Closest comparable; explicit Team vs Enterprise split |
| **Notion** | Enterprise quickstart: verify domain, SAML, default login, link workspaces | Workspace settings | SAML, SCIM, JIT, allowed domains | Audit log (~90 days, third-party) | Plans, seats | Trust centre | Domain verification via DNS TXT; JIT vs SCIM mismatch warning |
| **Atlassian** | Create org, verify domain, invite, products | Atlassian Administration | SSO, SCIM via Guard | Audit log 7 days default, up to 1 year, Events API | Plans, seats | Trust centre | Org admin vs site admin; third-party app activity logs |
| **WorkOS** | Admin Portal: verify domain, configure SSO/SCIM | Embeddable portal | SSO, Directory Sync, Audit Logs, Log Streams | Audit logs, streams | — | — | Setup link 30 days or API link 5 minutes; intents for sso/dsync/audit/domain |
| **OpenAI** | Sign up, org, project, API key | Platform settings | Org owner/reader, project owner/member | Audit logs (plan-dependent) | Spend limits, consolidated billing | Trust centre | Projects scope keys, files, models, spend; service accounts |
| **Anthropic** | Sign up, org, Console/Claude | Console settings | SSO Team/Enterprise/Console, WorkOS | Compliance logs | Plans, credits | Trust centre | Domain verification via DNS TXT; one IdP per org; JIT/SCIM |
| **Microsoft Foundry** | Create project, define prompt/hosted agent | Foundry portal | Entra agent identity per agent, federated credential, Conditional Access | Entra logs by agent | Azure billing | Trust centre | Agent starts with no permissions; OBO preview |
| **AWS AgentCore** | Create agent, runtime, memory, identity, gateway | AWS console | Agent identity directory, token vault (KMS), OBO (RFC 8693/7523) | CloudTrail/OTel | AWS billing | Trust centre | Agents authenticate as themselves, not as users |

### 2.2 Detailed notes

**Zapier.** The Admin Center is the model for a compact admin spine: a health dashboard, then
Insights (Analytics, Audit log, Log streams), Governance (App permissions, App connections, Data
policy, Approval settings, AI model providers), User management (Members, Teams), Security (Domain
management, SSO, SCIM), and Organisation (Billing and usage, Organisation profile, Beta features).
The audit log shows event type, object (including MCP servers), actor, date, and IP, with a detail
side panel. Log streams are Enterprise-only. Onboarding is template-led: blank Zap, templates,
guided templates, Copilot, and drafts that survive leaving the editor.

**Microsoft 365 / Entra.** The setup wizard is a linear, resumable flow: install apps, add and verify
the domain, add users and assign licences, connect the domain. The Setup page is the permanent home
for the same tasks later. Entra adds tenant creation, roles, Conditional Access, PIM, access reviews,
and — for agents — an agent identity blueprint, agent identities, agent users, sponsors, and access
packages. The lesson: **the setup wizard and the settings page are the same tasks, not two products.**

**AWS.** The new sign-up is deliberately short: GitHub/Google/Amazon/Apple, verify email, no credit
card for most, a free plan with credits, and a first project provisioned for you. Advanced sign-up is
for regulated/full-control customers. IAM Identity Center + Control Tower is the enterprise path:
landing zone, preconfigured directory, permission sets, account factory, group assignment, delegated
administration. For agents, AgentCore Identity is an agent identity directory plus a KMS-backed token
vault, with an on-behalf-of exchange that carries both agent and user identity.

**Google Workspace / Gemini Enterprise.** Admin console covers domain verification, SSO, and inbound
SCIM (cloud-only, service account + API key, locked groups). Gemini Enterprise has an Agents page
that lists Google-made, employee-made, ADK, A2A, and Dialogflow agents, shows an agent identity
(SPIFFE ID or Agent Registry resource ID), and lets an admin enable/disable capabilities. The lesson:
**agent inventory belongs in the admin console, next to people and apps.**

**Slack.** SCIM creates/deactivates members and guests, syncs profiles, and manages groups; billing
starts on first sign-in and is prorated. Audit logs are retained for two years (since 30 April 2026),
exportable as CSV/JSON, with filters for date, acting user, affected object, and event, plus an
`acting_agent` field when Slackbot acts for a user. 2FA can be required on every paid plan.

**GitHub.** The IAM checklist is the cleanest public joiner/mover/leaver template: choose the auth
method, provision accounts, configure SSO, configure SCIM, enforce 2FA, define external collaborator
rules, map IdP groups to teams, test onboarding/offboarding, document ownership. Audit logs stream to
Event Hubs, Datadog, Splunk, S3, Azure Blob, or GCS. Enterprise Managed Users is the "IdP owns the
account" model.

**Linear.** SCIM is Enterprise-only and requires SAML first. Provisioning roles come from IdP groups
(`linear-owners`, `linear-admins`, `linear-guests`). Once SCIM is on, admins cannot manage users in
Linear; a manual override exists for pre-SCIM members and guests. Audit log is about three months and
owner-only. The lesson: **decide explicitly whether the IdP or the product is the source of truth.**

**Vercel.** Eight team roles (Owner, Member, Developer, Security, Billing, Pro Viewer, Enterprise
Viewer, Contributor); only owners assign roles. Invites show a pending list with a 7-day expiry
(30 days for SAML-enforced teams), role editing, and removal. SCIM is Enterprise. The lesson: **a
pending-invite table with expiry and resend is table stakes.**

**Cursor.** The closest comparable to kell. Admin setup guide: review the trust centre, SSO, SCIM,
org/teams/groups, connect Git, MDM policies, allowed team IDs, dashboard alerts, security hardening.
Plan split: Teams gets SSO and team limits; Enterprise gets SCIM, audit logs, SIEM, OTel, service
accounts, billing groups, pooled usage. The lesson: **publish a setup guide, not just settings pages.**

**Notion.** Enterprise quickstart: verify domains via DNS TXT, enable SAML, choose the default login
method (`Any method` vs `Only SAML SSO`), link workspaces. JIT provisioning is supported, but Notion
warns against enabling JIT and SCIM together. Allowed email domains are an alternative to IdP
provisioning. Guests are not supported through SAML/SCIM. The lesson: **domain verification is the
gate for every identity feature, and the fallback login path must be explicit.**

**Atlassian.** Organisation administration is separate from product/site administration. Audit logs
show 7 days by default and up to 1 year with Guard Standard/Premium; filters, a JSON detail panel,
export, and an Events API. Third-party app activity is logged separately. The lesson: **separate
org-level admin from product-level admin, and say the retention period on the page.**

**WorkOS.** The Admin Portal is an out-of-the-box UI for a customer's IT contact to verify domains and
configure SSO/Directory Sync. You can send a setup link from the dashboard (30 days, revocable) or
generate one via API (5 minutes) with an intent (`sso`, `dsync`, `audit_logs`, `log_streams`,
`domain_verification`, `certificate_renewal`). The lesson: **SSO/SCIM onboarding is a product surface;
buy it before building it.**

**OpenAI.** Organisation owner/reader and project owner/member roles; project visibility is scoped;
service accounts and keys are project-scoped; spend limits are set at org and project level; the
default project cannot be deleted. The lesson: **projects are the unit of access and cost, not just
folders.**

**Anthropic.** SSO is available on Team, Enterprise, and Console organisations; WorkOS powers domain
verification and SSO. Verify domains with DNS TXT, one IdP per org, then enable SSO and JIT/SCIM.
The lesson: **one IdP per org is a real constraint; state it before the customer starts.**

**Microsoft Foundry / AWS AgentCore.** Both treat an agent as an identity. Foundry gives each agent
an Entra identity from a blueprint, a federated credential (no stored secret), least privilege by
default, Conditional Access, and per-agent audit. AgentCore gives an agent identity directory,
workload identities with ARNs, a KMS-encrypted token vault, and an on-behalf-of exchange. The lesson:
**"agent onboarding" is not a name field; it is identity, owner, scope, credential, budget, and audit.**

### 2.3 Table stakes vs enterprise-only

**Table stakes for a team**

- Email/OAuth sign-in, MFA for admins, recovery codes.
- Roles: owner/admin/billing/member/viewer.
- Invite, pending invite list, resend, revoke, remove, deactivate.
- Org profile, members, usage, spend limits, kill switch.
- Provider/API keys with show-once, last-used, revoke.
- Audit log viewer + CSV/JSON export.
- Plan/seat page, invoices, payment method, tax IDs.
- Empty states, checklist, docs, support contact.
- Trust page with honest compliance status.

**Enterprise-only (defer until a named buyer)**

- SSO (SAML/OIDC), SCIM, domain verification/capture, IP allowlist.
- Custom roles, project-level roles, access reviews.
- SIEM streaming, log retention beyond 12 months.
- Data residency, ZDR, signed DPA, subprocessor contracts.
- SOC 2 / ISO 27001 / 42001, pen test, BAA.
- SLA, 24×7 support, RTO/RPO, dedicated/VPC.
- MDM, device posture, Conditional Access.

---

## 3. Patterns worth copying

### 3.1 The checklist

- **3–6 binary tasks.** Each is something the person can do in a minute or two.
- **Auto-complete anywhere.** If they connect a key from the Keys page, the checklist ticks.
- **Count + bar.** "2 of 5 done", with the next step expanded and its button inline.
- **Finished steps stay visible**, ticked or struck through, so progress is believable.
- **Hideable** once done, with a way to bring it back.
- **Role-filtered.** Admin sees admin steps; member sees member steps; billing sees billing steps.
- **Handoff.** When the current role cannot do a step, say who can and offer a request/notify action.

### 3.2 Empty states

- Say **what will appear here**, **why it is empty**, and **one action** that fills it.
- Offer three ways in: start from scratch, start from a template, import.
- Never leave a zero metric blank; explain it and link to the action.
- Sample data must be labelled, and must never be mistakable for real data.
- Do not stack a floating checklist on a blank dashboard; tie each step to the widget it fills.

### 3.3 Invites

- **State machine:** pending / accepted / expired / revoked, with the same meaning on the sender's
  table and the recipient's landing page.
- **Role explanation at the selector**, not in a tooltip: "Can view and comment; cannot edit or invite."
- **Pending list** with recipient, role, expiry, resend, revoke.
- **"Invitation sent" ≠ "member added".** Show pending until accepted.
- **Domain mismatch warning** for external recipients; do not claim to verify identity.
- **Expiry:** 7 days default, 30 days for SSO-enforced orgs.
- **Email delivery** is part of the flow, not an afterthought.

### 3.4 SSO onboarding

The sequence most vendors follow:

1. **Verify the domain** with a DNS TXT record. Subdomains are distinct unless captured.
2. **Enable SSO** with the IdP metadata; one IdP per org.
3. **Choose the default login method** (`Any method` vs `Only SSO`).
4. **Enable JIT** (optional) or **SCIM** (optional, not both without care).
5. **Test joiner / mover / leaver** with a real user.
6. **Enforce** after a grace period, with an owner fallback and a break-glass path.
7. **Document** who owns SSO, who supports it, and what happens on an IdP outage.

Use **WorkOS Admin Portal** rather than building the IdP-specific flows. It gives you domain
verification, SSO, Directory Sync, Audit Logs, and Log Streams with a hosted UI and per-IdP guides.

### 3.5 Agent onboarding

An agent setup flow should collect, in this order:

1. **Name** and **owner** (a person), plus a **sponsor** (accountable human) for org agents.
2. **Kind**: hosted kell agent, local relay agent, MCP connector, or A2A agent.
3. **Tools** it may call (deny by default; absent tools cannot be called by mistake).
4. **Data ceiling**: public only, org-private, or user-private.
5. **Risk ceiling**: R0–R4; anything above needs approval.
6. **Budget**: per task, per day, per month.
7. **Expiry** and **revocation**.
8. **Approval mode**: per call, per task, standing grant.
9. **A test call** that proves the credential and the tool work.
10. **An Agent Card** (A2A) with skills, scopes, and evidence endpoint.

Then show it in an **agent inventory**: name, owner, sponsor, kind, status, last seen, tools, data
ceiling, budget used, evidence count, revoke.

### 3.6 Roles and permissions UI

- **A live permission table**: one row per action, one column per role, cells in words
  (`Allowed`, `Not allowed`, `Own items only`, `Assigned projects only`), not colour alone.
- **A per-person view** that answers "why can she do this?" and names the role/grant that supplies
  each permission.
- **Protect the last admin.** Warn at one, block at zero, and check who holds the permission to
  change roles.
- **Preview a role change**: before/after diff and the names of affected people.
- **Second approver** for high-stakes role changes.
- **Refuse unsafe deletes**: a role still assigned cannot be deleted; offer reassignment.

### 3.7 Audit

- **Viewer** with filters (date, actor, action, object, IP), a detail side panel, and a copyable JSON.
- **Export** CSV/JSON with a cursor and a chain/integrity result.
- **Retention** stated on the page, with a setting where the plan allows.
- **SIEM stream** with retry and a dead-letter table; never sample approval, denial, identity, or
  configuration events.
- **Denials are the interesting events.** An audit that only shows allowed actions does not prove the
  controls work.

### 3.8 Billing and seats

- **Four audiences:** workspace admin, billing contact, finance/AP, ordinary user. Each needs a
  different view and a different notification path.
- **Upgrade** applies immediately with proration; **downgrade** applies at renewal; say which.
- **Seats:** additions prorate and grant access at once; removals take effect next cycle unless access
  is revoked manually; annual invoiced customers get a quarterly true-up.
- **Downgrade consequences** in four buckets: turns off, becomes read-only, stays but over the limit,
  is deleted. Give each a date and a count, and put an export link beside the last two.
- **Invoices** must carry legal entity, tax ID, PO number, and the correct GST fields in India.

### 3.9 Trust centre

The strongest pages state **what was certified, for which systems, and since when**:

- SOC 2 Type 2 with the audit period; ISO 27001 with the Statement of Applicability scope.
- Subprocessor list, current and dated.
- Data residency and the DPA.
- Pen-test summary with the date of the last test.
- SLA and uptime history, separating promised from observed.
- Incident disclosures with dates and identifiers.
- An explicit public-vs-NDA boundary, and a visible "last updated".

Do not show a badge without an audit report. "Not certified" is a valid, trust-building answer.

### 3.10 Developer onboarding

- **API key on the first page after signup**, not three clicks deep.
- **One copy-pasteable request**, with the real test key injected if possible.
- **The exact expected response**, so the developer can compare.
- **One obvious next step**, not a menu of nine.
- **Test mode** that works instantly, uses the same endpoints and errors, and differs only in that
  nothing real happens. A full sandbox is a last resort for irreversible actions.
- **Test the quickstart quarterly from a clean machine with a stopwatch.**

---

## 4. kell today

### 4.1 Surface inventory

| Surface | Route | What it does today | Onboarding role |
|---|---|---|---|
| Marketing | `/` | Hero, install command, demo, about, use | Evaluate |
| Pricing | `/pricing` | Plans, credits, referral code, buy | Evaluate / buy |
| Docs | `/docs` | Legal docs + sidebar; no product/agent docs | Evaluate (weak) |
| Connect | `/connect`, `/connect/claude-code`, `/connect/claude`, `/connect/chatgpt`, `/connect/help` | Step-by-step setup for Claude Code, Claude web, ChatGPT | First value |
| Console | `/console` | Local helper: Connect, Keys & models, Playground, Router activity | First value (developer) |
| Chat | `/chat` | Rooms, `@kell`, relay agents, local agents, Way cards, canvases, docs, memory, files, votes, settings | First value + collaboration |
| Route | `/route` | Goal → Ways → Tools, approval tiers, read-only | Evaluate / discover |
| Sign-in | `/sign-in` | Supabase email/OAuth | Account |
| OAuth consent | `/oauth/consent` | Client name, scopes, local-redirect warning, allow/deny | Connect an app |
| Account | `/account`, `/account/profile`, `/account/connections`, `/account/billing`, `/account/settings` | Profile, connected apps, plan, settings | Account / billing |
| Org | `/org`, `/org/members`, `/org/policy`, `/org/activity`, `/org/keys` | Overview, members, policy, calls/denials, provider keys | Team setup (flag off) |
| Join | `/join` | Redeem an invite code | Member onboarding |
| Admin | `admin/` | Platform operator console: executive, people, team, policy, usage, performance, calls, denials, audit, billing, compliance, security, ingestion, moderation, operations, review, credits | Internal ops |
| Legal | `/docs/legal/*`, `/legal/*` | ToS, privacy, cookies, AUP, security, subprocessors, copyright | Evaluate |

### 4.2 What is good

- **Honest states.** "Not connected", "couldn't load", and "not certified" are used instead of
  fabricated data.
- **Native dialogs.** Settings, Add agent, Manage rooms use `<dialog>` with focus trapping, Escape,
  and reduced-motion handling.
- **Local-first chat.** Clear labels, offline outbox, sync status, explicit "temporary chat".
- **Org pages are role-gated** and handle 409 version conflicts with a clear message.
- **Route page** shows approval tiers (`approved`, `ai_approved`, `proposed`, `submitted`).
- **OAuth consent** names the client, lists scopes, and warns about local redirects.
- **Connect guides** are step-by-step and have an "undo/pause/remove" section.
- **Console** is a real local helper flow, not a fake dashboard.

### 4.3 Gaps

| # | Gap | Severity | Evidence |
|---|---|---|---|
| 1 | No org first-run checklist | P0 | `/org` opens on the dashboard; setup is scattered |
| 2 | No invite roles/email/pending states | P0 | `org_onboarding.py` says no email, no per-invite role, no removal |
| 3 | No member remove/deactivate/role change | P0 | `org_admin.py` has only `GET /members`; `/org/members` says so |
| 4 | No MFA setup for org admins | P0 | Only `admin/` billing gate (`ADMIN_REQUIRE_MFA`) |
| 5 | No approvals inbox | P0 | `ac227bef` deleted the cards; backend `/v1/tools/approvals` remains |
| 6 | No agent registry/onboarding page | P0 | Agents are created in chat; `delegate_agents` has no UI |
| 7 | No evidence/receipt view | P1 | `tool_call_audit`, procedures, checks not surfaced |
| 8 | No trust/status/security contact page | P1 | `frontend_remaining.md` lists the missing claims |
| 9 | No org billing/seat/invoice page | P1 | Plans live on `/pricing` and `/account/billing` |
| 10 | No audit retention/SIEM settings | P1 | Export exists; streaming/retention do not |
| 11 | No role matrix / permission diff | P1 | Roles are hidden in code |
| 12 | No offboarding checklist | P1 | No reassign/export/revoke flow |
| 13 | No empty-state/checklist components | P1 | Each page improvises |
| 14 | No onboarding emails | P1 | Invite email is manual |
| 15 | No product/agent docs | P1 | `/docs` is legal + sidebar |
| 16 | No WCAG audit | P2 | Admin panels are in scope |
| 17 | No i18n/currency/locale beyond INR | P2 | India-first is fine for now |
| 18 | No mobile admin | P2 | `/org` is responsive but untested |

---

## 5. Recommended UI + onboarding spec

### 5.1 Org first-run: `/org` checklist

Replace the current dashboard-first `/org` with a **setup spine** above the dashboard, visible until
dismissed or complete. Each step is a card with status, why, action, and handoff.

| Step | Who | Action | Done when |
|---|---|---|---|
| 1. Name the organisation | Owner | Edit name, logo, region | Org row exists |
| 2. Connect a provider key | Owner/Admin | `/org/keys` form | Key stored, test call passes |
| 3. Set the policy | Owner/Admin | Recommended policy + budgets | `org_policies` row exists |
| 4. Invite your team | Owner/Admin | `/org/members` invite | At least one invite sent |
| 5. Connect an agent | Owner/Admin | `/org/agents` create/connect | Agent has a card + test call |
| 6. Run a first verified task | Owner/Admin | `/chat` or `/route` | One call settles and passes its check |
| 7. Review the audit log | Owner/Admin | `/org/activity` | They open it (not a gate) |

Design rules: count + bar ("3 of 7 done"), next step expanded, finished steps ticked, skip with a
reason, hide after completion, and a "show setup" link in the org menu. The same tasks must be
completable from their dedicated pages; the checklist is a view, not a second product.

### 5.2 Member first-run: `/chat` or `/account`

A member who accepts an invite should see a **three-step** card, not the admin checklist:

1. **Install kell** — copy the command, link to `/connect`.
2. **Connect your tool** — Claude Code / Cursor / Claude / ChatGPT.
3. **Run your first task** — a prompt they can paste, with the expected result.

Then a quiet "You're set up" state, and a link to the room. Do not show provider keys, policy, or
billing to a member.

### 5.3 Invite flow

**Fields:** email (or a shareable link), role (Owner/Admin/Billing/Member/Viewer), expiry (7/30 days),
optional label, optional personal message.

**Rules:**

- Only Owner/Admin may invite; only Owner may invite Owner/Admin.
- Explain the role beside the selector in one sentence.
- Show a pending-invite table: email, role, sent, expires, status, resend, revoke.
- Distinguish "invitation sent" from "member added".
- Warn when the email domain is outside the org's verified domains.
- If email delivery is not configured, fall back to a copyable link and say so.
- On accept: set the role from the invite, write an audit event, and show the member in the table.

**States:** `pending`, `accepted`, `expired`, `revoked`, `bounced` (if email).

### 5.4 Provider key setup

A guided form, not a raw JSON blob:

1. **Provider** (preset list) → base URL auto-filled, editable.
2. **Models and prices** (input/output per Mtok), with a "fetch from provider" option where possible.
3. **Data classes** this connection may see (public/derived/procedure/private).
4. **Declarations**: ZDR, no-training, region, DPA signed. Unknown stays unknown; never invent.
5. **Test** — one tiny call, show latency and cost.
6. **Save** — show a key hint (last four), never the key.

### 5.5 Policy setup

- **Recommended policy** built from connected keys + two budgets.
- **Preview before apply**: "This allows 3 models, 2 providers, 2 tools, public + private data,
  $500/month, $20/person/day."
- **Public-only shortcut** with a plain explanation.
- **Impact**: what will be refused, and why (link to `/org/activity` denials).
- **Version conflict**: 409 handling already exists; keep the "someone else changed this" message.

### 5.6 Agent onboarding: `/org/agents`

**List view:** name, handle, owner, sponsor, kind, status (online/offline/revoked), tools count,
data ceiling, budget used, last seen, evidence count, actions (edit, pause, revoke).

**Create flow (wizard):**

1. **Name + owner + sponsor.**
2. **Kind**: hosted kell, local relay, MCP connector, A2A agent.
3. **Tools**: multi-select from the catalog, deny by default, with a "no tools" option.
4. **Data ceiling**: public only / org-private / user-private.
5. **Risk ceiling**: R0–R4, with the approval rule shown.
6. **Budget**: per task, per day, per month.
7. **Expiry + approval mode.**
8. **Test**: one call through the gate, show the decision and the receipt.
9. **Card**: show the A2A Agent Card preview (skills, scopes, evidence endpoint).

**Agent detail:** identity, owner/sponsor, card, scopes, data ceiling, budget, recent calls,
approvals, evidence, revoke. This is the page that makes a Hinoki-class agent governable.

### 5.7 Approvals inbox: `/org/approvals`

Rebuild the removed card as a first-class page plus an in-chat banner:

- **List**: pending, approved, denied, expired, used; filters by agent, tool, risk, person, room.
- **Card**: tool, server, risk (R0–R4), data class, argument hash, cost (or "not priced"), expiry,
  requesting agent, task/room, "Allow once" / "Deny".
- **R4**: two-person approval, with the second approver named.
- **Detail**: the policy reason (`policy:approve:<id>`), the certificate, the receipt.
- **Notifications**: bell + email/OS for pending approvals; approvals outrank room notifications.
- **Audit**: every decision writes an audit event, including denials.

### 5.8 Evidence: `/org/evidence`

A drawer or page per task:

- **Procedure** used (name, version, tier).
- **Check** that ran and its result.
- **Outcome** (accepted/rejected) and who accepted it.
- **Tool calls** (hashed args, cost, latency).
- **Receipt** (signed, hash-chained) with a verify button and export.
- **Chain status** for the org.

This is the visible form of the differentiator. Without it, "verified" is a claim in marketing.

### 5.9 Billing and seats: `/org/billing`

- **Plan**: name, seats, renewal date, included usage, current spend.
- **Seats**: add/remove with proration preview; pending changes.
- **Invoices**: list, download PDF, GSTIN/legal entity, PO number.
- **Payment method**: card/UPI, billing email, tax IDs.
- **Usage**: per person, per agent, per model, per day; budget vs actual.
- **Change plan**: upgrade now with proration; downgrade at renewal with the four consequence buckets.
- **Kill switch** stays on `/org` (already there).

### 5.10 Trust and status

- **`/trust`**: what we are, data flow, subprocessors (dated), DPA (download or request), pen-test
  status, compliance status ("not certified" if true), security contact, incident history, last
  updated.
- **`/status`**: external probe, 30/90-day uptime, incident history, subscribe.
- **`/.well-known/security.txt`** already exists in `prod_frontend`; add it to the live site.

### 5.11 Offboarding

A member detail page with:

- Remove / deactivate.
- Revoke sessions and keys.
- Reassign their agents and rooms.
- Export their data (or confirm what is retained).
- Audit event.
- A checklist that the action is complete.

### 5.12 Admin settings

- **Security**: MFA for owner/admin, recovery codes, session cap, idle timeout, step-up for sensitive
  actions, break-glass/second owner.
- **Identity**: SSO, SCIM, domains, JIT (later, via WorkOS).
- **Data**: retention, legal hold, export, erasure.
- **Audit**: retention, SIEM stream, export.
- **Agents**: default containment profile, tool catalog, approval defaults.
- **Billing**: plan, seats, invoices, tax.

---

## 6. Component and design-system needs

Build these once, reuse everywhere:

| Component | Used by | Notes |
|---|---|---|
| `SetupChecklist` | `/org`, `/chat`, `/account` | count + bar, next step expanded, hide, role-filtered |
| `StepCard` | checklist | title, why, status, action, skip, handoff |
| `EmptyState` | all lists | what/why/action, template/import, labelled sample |
| `InviteDialog` | `/org/members` | email, role, expiry, message, domain warning |
| `InviteTable` | `/org/members` | pending/accepted/expired/revoked, resend, revoke |
| `MemberTable` | `/org/members` | role, status, last active, actions |
| `RoleMatrix` | `/org/settings` | actions × roles, words not colour |
| `PermissionDiff` | role change | before/after, affected people |
| `AgentCard` | `/org/agents`, chat | name, owner, scopes, data ceiling, status |
| `AgentWizard` | `/org/agents` | 9 steps, test call, card preview |
| `ApprovalCard` | `/org/approvals`, chat | risk, data, args hash, cost, expiry, allow/deny |
| `EvidenceDrawer` | chat, `/org/evidence` | procedure, check, outcome, receipt |
| `AuditTable` | `/org/activity` | filters, detail panel, export |
| `BillingSummary` | `/org/billing` | plan, seats, usage, invoices |
| `SeatStepper` | `/org/billing` | add/remove, proration preview |
| `DangerZone` | settings | kill switch, delete, transfer ownership |
| `TrustBadge` | `/trust` | status + date + scope |
| `StatusPill` | everywhere | online/offline/pending/expired |
| `ConfirmDialog` | everywhere | destructive actions, reason field |
| `Toast` / `Banner` | everywhere | success, error, offline, paused |

**Design rules:** one primary CTA per screen; one next action per empty state; no colour-only status;
no invented numbers; every destructive action has a confirmation and a reason; every async action has
a pending/disabled state; every error names the next step.

---

## 7. Instrumentation

Track the funnel in §1.4. Add these UI events:

| Event | When | Properties |
|---|---|---|
| `onboarding_checklist_viewed` | checklist shown | org, role, step |
| `onboarding_step_completed` | step ticked | org, role, step, source |
| `onboarding_checklist_dismissed` | hidden | org, role, completed_count |
| `invite_sent` | invite created | org, role, domain_match |
| `invite_accepted` | join | org, role, days_to_accept |
| `member_removed` | remove | org, role, reason |
| `agent_created` | agent wizard done | org, kind, tools_count, data_ceiling |
| `agent_test_call` | test | org, agent, decision |
| `approval_requested` | gate returns approve | org, agent, tool, risk |
| `approval_decided` | allow/deny | org, agent, tool, decision, latency |
| `first_verified_task` | check passes | org, user, agent, procedure |
| `trust_page_viewed` | `/trust` | source |
| `billing_plan_changed` | upgrade/downgrade | org, from, to, seats |

Do not send prompts, replies, file contents, or argument values.

---

## 13. What landed on 2026-11-17

The first three P0 items are in the working tree:

| Item | Where | What it does |
|---|---|---|
| **Org setup checklist** | `components/org/SetupChecklist.tsx`, `app/org/page.tsx`, `app/org/org.css` | Six steps, role-filtered, count + bar, next step expanded, hide/show, persisted per browser. Reads `GET /v1/orgs/{id}/onboarding`, members/invites, and the relay agents. |
| **Invite roles + member lifecycle** | `db/211_org_member_lifecycle.sql`, `services/org_onboarding.py`, `api/org_admin.py`, `app/org/members/page.tsx`, `lib/org-admin.ts` | Invites carry owner/admin/member/viewer; `PATCH /members/{user_id}` changes a role; `DELETE /members/{user_id}` expires the memberships and revokes the agents that person owned. The last owner cannot be demoted or removed. |
| **Agents registry** | `services/org_agents.py`, `api/org_admin.py`, `app/org/agents/page.tsx`, `lib/org-admin.ts` | Lists the relay agents owned by org members (owner, kind, online/offline, data ceiling, scopes, budget, expiry); creates one with a containment record; revokes one. |
| **Approvals inbox** | `services/org_agents.py`, `api/org_admin.py`, `app/org/approvals/page.tsx`, `lib/org-admin.ts` | Lists the tool approvals belonging to org members; an owner/admin (or the owner of the approval) can allow once or deny. Every decision is audited. |
| **Desktop hand-off fix** | `lib/chat/desktop-intent.ts`, `components/chat/RoomView.tsx`, `services/desktop_remote.py`, `components/chat/DesktopPanel.tsx` | Ask mode no longer holds a desktop command behind the Way picker; Auto still routes; the server falls back to the person's most recent online session when the cheap router fails. |

Still open from the P0 list: MFA/session policy, the provider-key and policy wizards (the existing forms work but are not checklisted), the trust/status pages, and the empty states on the new pages (they exist but can be sharper). The P1 list (billing seats, audit retention/SIEM, offboarding checklist, product docs) is unchanged.

**Deploy caveat (updated 2026-11-18).** Migration 214 adds tenant-scoped RLS policies for `delegate_agents`, `delegate_tasks`, `tool_approvals`, `agent_links` and `desktop_grants`, keyed on the transaction-local `app.tenant_id`. The organization pages and their writes now have a policy path after the `stealth_app` switch. The relay and tool-gate paths that run outside `tenant_transaction()` still need the tenant bound before the role switch; this migration does not change those paths. Do not switch the DB role until those paths are audited too.

**Truthfulness note.** The scope, data ceiling, budget and expiry on `/org/agents` are recorded for review; the gate does not read them yet. The page says so.

---

## 14. Completion pass, 2026-11-18

The organization surface is now complete across the backend, the frontend and the team-administrator layer.

| Layer | What changed |
|---|---|
| **Role model** | `GET /v1/orgs/{id}/roles` returns the four roles, every capability, and how many people hold each. `org_governance.CAPABILITIES` is the one list the API, the Members page and the tests read. |
| **Membership rules** | An owner may change anyone. An admin may change members and viewers, and may step down themselves; an owner's or another admin's role is an owner's decision. The last owner cannot be demoted or removed. A person may remove themselves unless they are the last owner. |
| **Removal order** | Agents are revoked and desktop/agent grants deleted *before* the membership expires, because the tenant policy can no longer see those rows after expiry. |
| **Invite email** | `POST /invites` accepts `email`; `services/org_mail.py` sends a plain-text Resend message when configured. The response carries `email_sent` and `email_sent_at`; the code is still shown once either way. |
| **Agent lifecycle** | `PATCH /v1/orgs/{id}/agents/{agent_id}` edits scopes, data ceiling, budget and expiry without revoking. Agents report `online` / `offline` / `expired` / `revoked`; `/org/agents` has an edit dialog and status chips. |
| **Approvals** | `GET /approvals` validates the status (`pending`, `approved`, `denied`, `expired`, `used`, `all`). `/org/approvals` has tabs for each and still allows or denies a pending call once. |
| **Onboarding** | `GET /onboarding` now includes `pending_approvals`; the setup checklist has a seventh step, "Clear the approvals queue". |
| **Team admin page** | `/org/members` now shows the role/capability matrix, the invite email and whether it was sent, and the last 30 days of membership, invite, agent, approval and policy changes from the audit chain. The operator console (`admin/app/team/page.tsx`) has the same surface: role changes, removal, role matrix, invite role/email, and recent changes. |
| **RLS** | Migration 214 adds the tenant-scoped policies described above. |

Deploy order: apply `211_org_member_lifecycle.sql` and `214_org_team_admin.sql`, then `NEXT_PUBLIC_ORG_ADMIN=on` for the pages. The operator guide is `docs/org_admin.md`.

---

## 8. Accessibility, i18n, mobile

- **WCAG 2.1 AA is the baseline; 2.2 AA is the target** for public-sector and enterprise procurement.
  Admin panels are in scope, not just the marketing site.
- **Keyboard:** every flow (sign-up, invite, SSO, MFA, onboarding, settings, roles, billing) must be
  operable without a mouse; focus visible; no focus traps outside dialogs.
- **Screen readers:** test with NVDA, JAWS, VoiceOver, TalkBack; label every input; announce async
  status; use `aria-live` for toasts and streamed text.
- **Zoom/reflow:** 400% zoom, 320 px width, no horizontal scroll; the chat and org tables must
  reflow.
- **Colour:** never the only signal; pair status colour with text and an icon.
- **Motion:** honour `prefers-reduced-motion`; the current chat/desktop already does.
- **i18n:** dates, currency, and numbers through `Intl`; INR first, but do not hard-code `₹` in
  components; keep copy in one place.
- **Mobile:** the org admin pages should work on a phone (invite, approve, kill switch), even if the
  full audit table is desktop-first.

---

## 9. Prioritized roadmap

### P0 — unblock a design-partner team (2–4 weeks)

1. **Org setup checklist** on `/org` (7 steps, role-filtered, hideable).
2. **Invite flow** with roles, expiry, pending table, resend/revoke, email-or-link.
3. **Member lifecycle**: remove/deactivate, role change, revoke sessions/keys, two-owner rule.
4. **MFA setup** for owner/admin + recovery codes + session policy.
5. **Approvals inbox** `/org/approvals` + in-chat banner (restore the backend UI).
6. **Provider key + policy wizards** with preview and test.
7. **Trust page + status page + security contact** (honest "not certified").
8. **Empty states** for `/org`, `/chat`, `/org/agents`, `/org/activity`.

**Acceptance:** a new owner can go from sign-in to a verified task and an invited teammate without
reading a doc; a member can accept and run a task without seeing admin steps.

### P1 — make it defensible (4–8 weeks)

9. **`/org/agents`** list + wizard + detail (owner, sponsor, scopes, data ceiling, budget, revoke).
10. **`/org/evidence`** drawer/page (procedure, check, outcome, receipt, chain).
11. **`/org/billing`** plan, seats, invoices, tax IDs, change plan with consequence preview.
12. **Audit retention + SIEM** settings and a denial/auth-failure event stream.
13. **Role matrix + permission diff**; Billing role.
14. **Offboarding checklist** with reassignment and export.
15. **Product/agent docs** under `/docs` (what an agent is, how to connect, what a receipt proves).
16. **Onboarding emails** (invite, welcome, approval needed, first task).

**Acceptance:** an org admin can answer "who can do what, what did this agent do, what did it cost,
and can I revoke it?" from the UI.

### P2 — enterprise, only with a named buyer (8+ weeks)

17. **SSO/SCIM/domain verification** via WorkOS Admin Portal, with JIT and staged enforcement.
18. **Custom roles / project roles / access reviews**.
19. **IP allowlist, session policy, Conditional Access**.
20. **Trust centre** with SOC 2/ISO scope and dates, DPA, pen-test summary, subprocessors.
21. **SLA, status history, incident disclosures, support tiers**.
22. **WCAG 2.2 AA audit** and fixes.
23. **i18n** beyond INR/English.

**Acceptance:** a security reviewer can complete a CAIQ-lite questionnaire from the trust page and
the admin UI without a call.

---

## 10. What not to build

- **A product tour.** Tours are skipped; use a checklist.
- **A separate onboarding app.** The checklist is a view over the real settings pages.
- **A 60-row permission grid** with no search, filters, or role comparison.
- **SSO/SCIM before a named buyer.** Use WorkOS when the buyer appears.
- **A custom identity provider.** Supabase/WorkOS/Entra are the identity layer.
- **A fake dashboard with sample data** that is not labelled.
- **A trust badge without a report.** Say "not certified" until it is true.
- **A billing page that hides downgrade consequences.** Show counts, dates, and export links.
- **A member list without pending invites.** The pending state is the flow.

---

## 11. Open questions

1. **Self-serve org creation or operator-provisioned?** Today `provision_org.py` creates the org;
   Pilot 2+ seats creates one. Pick one for the first 20 teams.
2. **SSO on which tier?** The market puts SSO on Teams, SCIM/audit on Enterprise. The "SSO tax"
   debate is real; a paid add-on is a defensible middle.
3. **Email provider?** Resend is already used for contact/submission email; use it for invites and
   approval notifications, or add a transactional provider.
4. **Agent onboarding: invite or connect?** Hosted kell agents are created; local agents are
   connected; A2A agents are registered. The wizard should branch at step 2.
5. **Evidence: per task or per procedure?** Per task is the receipt; per procedure is the trust
   score. Show both, linked.
6. **Billing: seats, usage, or both?** The Pilot is per seat; credits are usage. Keep both and say
   which applies.
7. **Trust page owner?** Founder/security; it must be dated and reviewed quarterly.
8. **Design system home?** `routing_kell_frontend` has the canonical buttons/dialogs; `admin/` reuses
   its stylesheet. Put the new components in one place and import from both.
9. **Mobile admin scope?** At minimum: invite, approve, kill switch, member status. Full audit table
   can stay desktop.
10. **Data residency.** The India-region plan is a plan; do not promise "India-only" until the
    database, storage, embeddings, and judge are all in-region.

---

## 12. Sources

Vendor and pattern sources read during this sweep (2026-11-17). Where a page is a help-centre summary
rather than a primary specification, it is marked "(summary)".

- Zapier Admin Center: https://help.zapier.com/hc/en-us/articles/38925392216973-Review-your-account-in-the-admin-center
- Zapier audit log: https://help.zapier.com/hc/en-us/articles/13295074298125-Use-the-audit-log-to-review-your-account-activity
- Zapier quick start / templates: https://help.zapier.com/hc/en-us/articles/22234847450893-Zap-workflows-quick-start-guide
- Microsoft Entra tenant creation: https://learn.microsoft.com/en-us/entra/fundamentals/create-new-tenant
- Microsoft 365 setup planning: https://learn.microsoft.com/en-us/microsoft-365/admin/setup/plan-your-setup
- Microsoft Entra agent ID governance: https://learn.microsoft.com/en-us/entra/id-governance/agent-id-governance-overview
- AWS account creation: https://aws.amazon.com/resources/create-account/
- AWS Control Tower + IAM Identity Center: https://docs.aws.amazon.com/controltower/latest/userguide/sso.html
- AWS Agent Registry: https://aws.amazon.com/blogs/machine-learning/manage-agents-tools-and-skills-at-scale-with-aws-agent-registry/
- AWS AgentCore identity (summary): https://dev.to/thasha/part-23-agent-identity-in-one-picture-entra-vs-iam-2elb
- Google Workspace inbound SCIM: https://knowledge.workspace.google.com/admin/users/get-started-with-inbound-scim
- Gemini Enterprise agents: https://docs.cloud.google.com/gemini/enterprise/docs/agents-overview
- Google Agent Registry skills: https://docs.cloud.google.com/agent-registry/skills-architecture
- Slack SCIM: https://slack.com/help/articles/212572638-Manage-members-with-SCIM-provisioning
- Slack audit logs: https://slack.com/help/articles/360000394286-Audit-logs-in-Slack
- GitHub IAM setup: https://learn.github.com/product-guides/github-enterprise/get-started/set-up-identity-and-access-management
- GitHub audit log streaming (summary): https://resources.github.com/learn/pathways/administration-governance/essentials/access-capture-consume-audit-logs
- Linear SCIM: https://linear.app/docs/scim
- Linear security: https://linear.app/security
- Vercel team members: https://vercel.com/docs/rbac/managing-team-members
- Vercel directory sync: https://vercel.com/docs/directory-sync
- Cursor admin setup: https://cursor.com/docs/enterprise/admin-setup-guide
- Cursor enterprise: https://cursor.com/docs/enterprise
- Notion enterprise quickstart: https://www.notion.com/help/guides/the-ultimate-quickstart-guide-to-notion-for-enterprise
- Atlassian audit log: https://support.atlassian.com/security-and-access-policies/docs/view-audit-log-activities/
- WorkOS Admin Portal: https://workos.com/docs/admin-portal
- OpenAI projects: https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform
- Anthropic SSO: https://support.claude.com/en/articles/13132885-set-up-single-sign-on-sso
- OpenAI Agent Builder: https://developers.openai.com/api/docs/guides/agent-builder
- Vanta trust centre guide: https://www.vanta.com/resources/building-a-comprehensive-trust-center
- Trust centre examples: https://www.vezadigital.com/post/trust-center-examples
- B2B SaaS roles/permissions UX: https://humbleteam.com/blog/b2b-saas-roles-permissions-ux
- B2B SaaS billing UX: https://humbleteam.com/blog/b2b-saas-billing-plan-changes-ux
- Onboarding checklist patterns: https://www.skene.ai/resources/blog/saas-onboarding-checklist-template
- Onboarding benchmark 2026: https://getperspective.ai/blog/2026-customer-onboarding-benchmark-activation-rates-by-industry
- Role-based onboarding: https://adoptionsignals.com/posts/role-based-onboarding-for-multi-persona-b2b-products
- Invite UX: https://mockitt.com/en/guide/team-invitation-ux.html
- Developer onboarding: https://www.skene.ai/resources/blog/developer-onboarding-guide
- Developer quickstart: https://kompassify.com/blog/developer-onboarding-guide
- Progressive disclosure: https://userpilot.com/blog/progressive-disclosure-examples/
- Empty states: https://usertourkit.com/blog/empty-states-that-convert-onboarding-design-patterns
- Onboarding checklist UI examples: https://madegooddesigns.com/inspiration/ui/onboarding-checklist-ui
- WCAG 2.1 AA for SaaS: https://accessible.org/wcag-21-aa-saas-platforms-required/
- WCAG 2.2 enterprise UX: https://builtux.com/wcag-2-2-compliance-enterprise-ux-accessibility-2026/
- Accessible onboarding: https://kompassify.com/blog/accessible-onboarding-guide
- Domain verification / domain capture (Overleaf): https://docs.overleaf.com/groups/overleaf-group-single-sign-on/domain-management
