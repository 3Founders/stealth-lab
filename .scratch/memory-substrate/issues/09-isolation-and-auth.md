# Isolation and auth posture

Type: grilling
Status: resolved
Blocked by: 01

## Question

What is the owner-isolation and authentication posture for traces, claims and procedures — and does this milestone adopt a managed auth provider (Supabase, WorkOS) or stay with owner columns and local-first deployment?

The repo owner explicitly asked for this to be discussed rather than defaulted.

spec.md's requirements: owner isolation, project isolation, explicit visibility, encryption where appropriate, no cross-user leakage, and **local-first deployment compatibility**. Agentic traces are described as containing extremely sensitive information.

The relevant existing facts:

- **There is no authentication.** Viewer identity comes from an unverified `X-Viewer-Id` header in `backend/app/api/deps.py`, documented as deliberate and temporary.
- There is a startup fail-closed guard: enabling `private_visibility_enabled` without `real_auth_enabled` raises at boot. Both default false.
- `backend/app/services/access.py` is a genuinely good seam: one module builds every visibility predicate in the system, `AccessScope` is taken at *construction* rather than per call so a call site cannot forget it, and unrestricted returns the literal `TRUE` so permissiveness is visible in the query text.
- **`tenant_id` is the cautionary tale, and it is in this repo.** It is on every table from the first schema and filtered by **zero** queries. Most write paths omit it from their INSERTs entirely and rely on the column default. `access.py`'s own docstring cites this as the lesson that motivated its design.
- `owner_id` is TEXT, not a FK, because there is no users table — explicitly deferred in `db/03_access.sql`.
- There is no users table, no sessions, no password or token storage of any kind.

Decide:

- Managed provider (Supabase Auth, WorkOS) vs. rolling identity locally vs. staying single-owner for milestone 1.
- If local-first single-user is the milestone-1 answer: what must be *designed in* now so multi-tenancy is a real addition later rather than another decorative column? Concretely — do new tables get `owner_id` from day one, and does every new query go through `access.py`?
- Does project isolation (per-repository, per-workspace) need a separate axis from owner isolation, or is it a property of the episode?

Grill these:

- Supabase and WorkOS both assume a hosted deployment with an outbound dependency. spec.md demands local-first deployment compatibility and says not to send raw traces to an external LLM by default. Does adding a hosted auth dependency contradict the privacy posture, or is auth categorically different from trace data?
- What is the actual first user? If it is one developer running this against their own Postgres, real auth in milestone 1 is work that protects nobody. If a second user is weeks away, retrofitting isolation across trace/episode/observation/claim/procedure/execution tables is far worse than building it in.
- The strongest argument for a provider is that hand-rolled auth is where security bugs live. The strongest argument against is that `X-Viewer-Id` plus a fail-closed startup guard is *honest* about having no auth, whereas a half-integrated provider looks like security without being it. Which risk is real here?
- If a provider is chosen: what exactly does it own — identity only, or also row-level authorization? Supabase RLS would mean policy lives in the database, competing with `access.py`'s single-seam design. Which one is the authority?

## Answer

**Provider vs. local vs. single-owner: stay local-first, single/few-trusted-owner for milestone
1. Do not adopt Supabase Auth or WorkOS yet.** This is consistent with a posture already shipped
elsewhere in this exact codebase: `mcp_server/server.py`'s `StaticTokenVerifier` is explicitly
documented as for "a single-tenant, loopback-bound deployment," with hosting deliberately kept
loopback-only rather than tunneled out. Adopting a hosted provider for this effort now would be
inconsistent with a decision already made one layer over.

One asymmetry worth keeping explicit for whenever this is revisited: the two named providers are
not equivalent choices. This repo is already committed to Supabase for the database, so adopting
**Supabase Auth specifically** later would be a smaller marginal step (same vendor, already
trusted with data) than adopting WorkOS, a genuinely separate vendor relationship. Not a reason
to adopt either now — a reason not to treat them as interchangeable later.

**What must be designed in now, so multi-tenancy is a real addition later, not another decorative
column:** both of the ticket's own concrete questions, answered yes, non-negotiably, given the
`tenant_id` precedent already in this repo (`db/03_access.sql`'s own comment names it as the
cautionary case: present on every table since the first schema, filtered by zero queries, most
write paths omitting it from INSERTs entirely). Every new table this effort adds (`trace_events`,
the trace-header table from ticket 06, anything for claims/procedures) gets `owner_id TEXT` from
row one — not deferred "for later" the way `tenant_id` was. Every new query goes through
`app/services/access.py`'s `visibility_predicate()` — no hand-written filters, no exceptions.
`AccessScope`'s real fields are `viewer_id: Optional[str]` and `include_private: bool`
(`access.py:27-35`); its `unrestricted()` classmethod's own docstring is explicit — "never for a
request originating from a user" (`access.py:47-50`) — that boundary should hold for every new
table too, not just the existing ones.

**Project isolation: its own axis, not a property of episode.** A project (repo/workspace) is a
stable, longer-lived thing many episodes belong to over time — querying "everything from this
repo" needs to span months of episodes, and a project can have multiple owners contributing
episodes independently of any single episode's owner. Same column-based pattern `owner_id`/
`visibility` already use, not a new mechanism — a `project_id`-shaped column, with exactly what
"project" resolves to (a git repo? something coarser?) deferred until ticket 06's actual
trace/episode schema is being built, since that's where the column would live.

**Encryption:** blanket at-rest and in-transit encryption is already satisfied automatically by
Supabase's own platform defaults (AES-256 at rest, TLS in transit, always on, no configuration
possible to disable it) — nothing to design here, and nothing this ticket needs to decide. The
real, narrower question — field-level encryption for a specific sensitive value that slips past
redaction (e.g. a credential embedded in captured tool output) — belongs to **ticket 18 (Privacy
and redaction)**, not here; `Supabase Vault` (Transparent Column Encryption, already available on
the platform this repo already runs on) is the concrete mechanism if that ticket decides it's
needed. Flagging the cross-reference rather than re-deciding it in this ticket.

**Grill 1 — does hosted auth contradict the local-first/no-raw-traces-to-external-LLM privacy
posture?** Auth and trace content are categorically different risks: verifying an identity token
with a hosted service doesn't leak agent transcripts the way sending them to an external LLM
would. But it is still a real, unavoidable outbound dependency, genuinely in tension with "local-
first deployment compatibility" taken literally (a fully offline machine can't authenticate
against a hosted service). Not a fatal objection — one more real point toward not adopting a
provider yet, not a claim that one could never be adopted.

**Grill 2 — what is the actual first user, and what risk does deferring accept?** Almost
certainly this repo's own team running against their own Supabase project, not a second/external
user weeks away — precisely the condition the ticket itself names as the case where real auth
"protects nobody." But "single-owner" and "a few trusted teammates" are not the same risk
profile, and deferring should not blur them together. If it is genuinely one person, the
unverified `X-Viewer-Id` header barely matters. If it is a few teammates, there is already a
real, narrow risk *today*, independent of any outside attacker: anyone on the team can set that
header to whatever value they like and act as anyone else on the team. Deferring real auth is a
decision to accept that specific, internal risk for now — not a claim that no risk exists at all.

**Grill 3 — hand-rolled security bugs vs. a false sense of security: which risk is real here?**
The current posture (`X-Viewer-Id` unverified, plus `deps.py:44-56`'s fail-closed startup guard
that refuses to boot if `private_visibility_enabled` is on without `real_auth_enabled`) is already
the honest choice, and it is explicitly documented as deliberate, not an oversight. Keep that
honesty rather than half-integrate a provider now — a partial integration that resembles real
auth without being one is a worse failure mode than a stub that visibly isn't, because it invites
trusting a guarantee that isn't actually there.

**Grill 4 — if a provider is ever chosen, what does it own?** Identity only, never authorization.
`access.py` is a genuinely centralized seam — the single place every visibility predicate gets
built, with `AccessScope` bound at construction so a call site cannot forget it. Supabase RLS
would create a second, competing source of authorization truth, splitting policy between the
database and the app layer — a real, well-known drift risk (policy in RLS and policy in
`access.py` silently diverging over time). If a provider is adopted later, it verifies who
someone is; `access.py` remains the sole authority for what they may see.