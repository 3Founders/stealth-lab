# Organization administration

This is the operator and developer guide for the team-administrator surface: roles, invitations, membership
lifecycle, the relay-agent registry, the tool-approval inbox and the audit trail. It describes what the code does
today, including the parts that are recorded but not enforced.

The pages live under `/org` behind `NEXT_PUBLIC_ORG_ADMIN=on`. The API lives under `/v1/orgs/{org_id}` in
`backend/app/api/org_admin.py`; the services are `org_governance.py`, `org_onboarding.py` and `org_agents.py`.

---

## 1. Roles and capabilities

Four roles exist. A person's role is held by a row in `org_memberships`; the role catalog is seeded by
`db/28_identity.sql`.

| Capability | Owner | Admin | Member | Viewer |
|---|:---:|:---:|:---:|:---:|
| See the organization and its connection details | ✓ | ✓ | ✓ | ✓ |
| Make model calls through the organization's gateway | ✓ | ✓ | ✓ | |
| See spend, calls and refusals | ✓ | ✓ | | |
| Set the policy and the kill switch | ✓ | ✓ | | |
| Connect and remove provider keys | ✓ | ✓ | | |
| Invite people, change a member's role, remove a member | ✓ | ✓ | | |
| Change an owner's or another admin's role | ✓ | | | |
| See, create, edit and revoke relay agents | ✓ | ✓ | | |
| Allow or deny a member's pending tool call | ✓ | ✓ | | |
| Billing, plans and prepaid credits | ✓ | | | |
| Place and release a legal hold | ✓ | | | |
| Request and execute an erasure | ✓ | | | |

`GET /v1/orgs/{org_id}/roles` returns this matrix with live member counts. It is descriptive: every route still
checks the caller's roles itself.

### Membership rules

- An **owner** may change anyone's role, remove anyone, and invite another owner.
- An **admin** may change a member or viewer, and may step down from admin themselves. An owner's or another
  admin's role is an owner's decision.
- The **last owner** cannot be demoted or removed. An organization always has at least one owner.
- A person may remove themselves, unless they are the last owner.
- Only an owner can invite another owner.

Removing a person **expires** their memberships; nothing is deleted from the identity tables. Before the expiry,
the service revokes the relay agents they owned and deletes the `desktop_grants` and `agent_links` rows where they
were either side. That order matters: the tenant RLS policy can no longer see those person-owned rows after the
membership expires. Their account and personal work are untouched.

---

## 2. Invitations

`POST /v1/orgs/{org_id}/invites` creates an invite:

```json
{
  "label": "Platform team",
  "days": 7,
  "max_uses": 50,
  "role": "member",
  "email": "teammate@company.com"
}
```

- The code is `sl1_<org id as 32 hex>_<24 hex>`. Only its SHA-256 is stored.
- The response is the only time the clear code is returned. It also carries `role`, `email`, `email_sent` and
  `email_sent_at`.
- `role` is one of `owner`, `admin`, `member`, `viewer`. Only an owner may invite an owner.
- `email` is optional. When Resend is configured (`RESEND_API_KEY` and `CONTACT_NOTIFY_FROM`, or `ORG_INVITE_FROM`),
  `services/org_mail.py` sends a plain-text invitation with the link and the code. Delivery is best effort: a
  failure does not invalidate the invite, and the admin still sees the code and a copy link.
- `GET /invites` lists invites with their role, address, use count, expiry and whether the email left.
- `DELETE /invites/{invite_id}` revokes an active invite. A revoked, expired or used-up invite gets the same
  generic "not valid" answer when redeemed, so a code cannot be probed.

A person redeems a code at `/join` (or `POST /v1/join` with `{"code": "..."}`). They must be signed in. Redeeming
adds the membership with the invite's role and consumes one use. Redeeming again as the same person does not
consume another use.

---

## 3. Relay agents

An agent is owned by a person (`delegate_agents.owner_sub`), not by the organization. `/org/agents` shows the
agents owned by current members, with:

- **owner** — display name, email or subject;
- **kind** — normally `agent` (a relay agent that reads its inbox with `delegate_inbox`);
- **status** — `online` (reported within 60 seconds), `offline`, `expired` or `revoked`;
- **data ceiling** — `PUBLIC_SOURCE`, `PUBLIC_DERIVED`, `GLOBAL_PROCEDURE` or `USER_PRIVATE`;
- **scopes** — free-form capability labels recorded for review;
- **monthly budget** — a USD amount recorded for review;
- **expiry**.

`POST /agents` creates one. `PATCH /agents/{agent_id}` changes the containment without revoking; omitted fields are
left alone, `scopes: []` clears scopes, `budget_usd_month: ""` clears the budget, and `clear_expiry: true` removes
the expiry. `DELETE /agents/{agent_id}` revokes the agent and cancels its `needs_approval`, `queued` and `claimed`
tasks.

**What is not here.** A desktop session is reported by the desktop app and appears in the chat's Desktop sessions
panel. An MCP connector is added in the chat. A2A agents are not stored in this table. The page says so.

**Enforcement.** The scope, data ceiling, budget and expiry are a review record today. The gate does not read
them; every tool call still passes the tool gate. Do not describe them to a customer as enforced limits.

---

## 4. Tool approvals

A tool call the policy marks `approve` creates a row in `tool_approvals`. An approval is bound to one exact call
shape: owner, task, server, tool, argument hash and certificate. It is short-lived and single-use. A changed
argument or a different certificate is a different approval. Arguments are never stored, only their hash.

`GET /v1/orgs/{org_id}/approvals?status=pending` lists approvals belonging to current members. `status` may be
`pending`, `approved`, `denied`, `expired`, `used` or `all`. An owner or admin may decide for a member; a person
may decide their own. `POST /approvals/{approval_id}/approve` and `/deny` make the decision and write an audit row.
`Allow once` extends the approval window; the next identical call consumes it. A pending row past its window is not
usable, and the list's lazy sweep marks it `expired`.

---

## 5. Audit

Every write in these services uses `org_governance._audit` on the same transaction as the change, so a change and
its evidence commit together or not at all. The actions are:

- `org_invite.create`, `org_invite.revoke`, `org_invite.redeem`
- `org_member.role`, `org_member.remove`
- `org_agent.create`, `org_agent.update`, `org_agent.revoke`
- `tool_approval.approve`, `tool_approval.deny`
- `org_policy.put`, `org_policy.kill_switch_on`, `org_policy.kill_switch_off`
- `org_legal_hold.placed`, `org_legal_hold.released`
- `org_erasure.requested`, `org_erasure.approved`, `org_erasure.executed`

`GET /v1/orgs/{org_id}/audit?since=...&until=...` exports the organization's events with the hash-chain result.
`/org/members` shows the last 30 days of membership, invite, agent, approval and policy changes from the same
chain.

---

## 6. API reference

All routes require a signed-in human who is a member of the organization. A non-member gets 404; a member without
the needed role gets 403.

| Method | Path | Role |
|---|---|---|
| `GET` | `/v1/orgs/mine` | any signed-in person |
| `GET` | `/v1/orgs/{org}/roles` | owner, admin |
| `GET` | `/v1/orgs/{org}/members` | owner, admin |
| `PATCH` | `/v1/orgs/{org}/members/{user_id}` | owner, admin (rules above) |
| `DELETE` | `/v1/orgs/{org}/members/{user_id}` | owner, admin (rules above) |
| `GET` | `/v1/orgs/{org}/invites` | owner, admin |
| `POST` | `/v1/orgs/{org}/invites` | owner, admin |
| `DELETE` | `/v1/orgs/{org}/invites/{invite_id}` | owner, admin |
| `GET` | `/v1/orgs/{org}/agents` | owner, admin |
| `POST` | `/v1/orgs/{org}/agents` | owner, admin |
| `PATCH` | `/v1/orgs/{org}/agents/{agent_id}` | owner, admin |
| `DELETE` | `/v1/orgs/{org}/agents/{agent_id}` | owner, admin |
| `GET` | `/v1/orgs/{org}/approvals?status=` | owner, admin |
| `POST` | `/v1/orgs/{org}/approvals/{id}/approve` | owner, admin |
| `POST` | `/v1/orgs/{org}/approvals/{id}/deny` | owner, admin |
| `GET` | `/v1/orgs/{org}/onboarding` | owner, admin |
| `POST` | `/v1/join` | any signed-in person with a code |

The policy, usage, calls, denials, provider-key, legal-hold and erasure routes are documented in the route
docstrings in `backend/app/api/org_admin.py`.

---

## 7. Database and RLS

Migrations:

- `211_org_member_lifecycle.sql` — `org_invites.role` and `.email`; `delegate_agents` containment columns.
- `214_org_team_admin.sql` — `delegate_agents.updated_at` / `.updated_by`; `org_invites.email_sent_at`; the
  tenant-scoped RLS policies.

`214` creates `sl_org_subject(text)`, a `SECURITY DEFINER` function that answers whether a subject is an active
member of the transaction's `app.tenant_id`. Policies on `delegate_agents`, `delegate_tasks`, `tool_approvals`,
`agent_links` and `desktop_grants` use it. The tables remain closed to `anon` and `authenticated`.

**Caveat.** The relay and tool-gate paths that run outside `tenant_transaction()` do not bind `app.tenant_id`.
They work today because the backend connects as the table owner (RLS is enabled, not forced). Before the
`stealth_app` role switch, those paths need the same tenant binding; the org pages alone are covered by `214`.

---

## 8. Pages

| Page | What it does |
|---|---|
| `/org` | Overview, spend, per-person usage, kill switch, setup checklist. |
| `/org/members` | Members, role change, remove, invites (code/link/email), role matrix, recent admin changes. |
| `/org/agents` | Relay agents, status, containment, create, edit, revoke. |
| `/org/approvals` | Pending and decided tool approvals; allow once or deny. |
| `/org/policy` | Policy editor and the public-data preset. |
| `/org/activity` | Calls and refusals. |
| `/org/keys` | Provider keys. |
| `/join` | Redeem an invite. |

The separate operator console (`admin/`, port 3200) has the same team surface at `/team`: members with role changes
and removal, the role matrix, invitations with role/email, and the last 30 days of membership changes. It calls the
same `/v1/orgs/{org_id}` routes, so the two consoles cannot disagree.

The pages are behind `NEXT_PUBLIC_ORG_ADMIN=on`. A member who opens an admin page sees a sentence, not a table,
and the page makes no admin request.

---

## 9. Tests and commands

```powershell
# backend, from backend\
python -m pytest tests/test_org_onboarding_offline.py tests/test_org_agents_offline.py tests/test_org_governance_offline.py -q

# frontend, from routing_kell_frontend\
npx vitest run tests/org-pages.test.tsx tests/org-admin.test.ts

# types
npx tsc --noEmit
```

The org-focused backend tests cover role checks, invite rules, membership lifecycle, removal order, agent
containment and status, approval validation and the route layer. The frontend tests cover the members page, the
role matrix, the agents page (create, edit, revoke), the approvals inbox, role gating, and the typed client's
routes.

---

## 10. Limits

- No self-serve organization sign-up; an operator provisions the first owner (`backend/scripts/provision_org.py`).
- No SCIM or directory sync, no MFA/session policy yet.
- Email delivery is best effort; the code is always shown once to the admin.
- Agent containment is recorded, not enforced by the gate.
- No billing seats or plan changes on these pages.
- `tool_approvals`, `delegate_agents`, `agent_links` and `desktop_grants` are person-owned; the organization sees
  them through membership, not through an organization id on the row.
