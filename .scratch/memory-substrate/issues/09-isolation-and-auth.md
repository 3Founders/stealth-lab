# Isolation and auth posture

Type: grilling
Status: claimed
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
