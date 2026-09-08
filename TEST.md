You are working on the StealthLab repository:

`https://github.com/3Founders/stealth-lab`

Goal: add **production-ready Supabase authentication** to the existing project without disturbing the ingestion, MCP, retrieval, or procedural-memory architecture.

StealthLab already uses Supabase/Postgres for backend data. Authentication should be added on top of the existing system, not by creating a parallel user/database architecture.

## Product goal

We need users to be able to:

```text
sign up / sign in
→ receive a Supabase-authenticated session
→ use the StealthLab UI
→ call authenticated backend/API/MCP-adjacent endpoints where appropriate
→ have server-side code know the authenticated user identity
→ enforce ownership/privacy for user-scoped data
```

For V1, support:

```text
Email + password
Google OAuth
```

If the repo already has another auth mechanism or Supabase auth scaffolding, extend/reuse it rather than duplicating it.

---

# FIRST: inspect the repo

Before changing code, inspect:

```text
frontend structure/framework
backend framework
existing Supabase clients
environment-variable handling
API request helpers
middleware
current user/account models
database schema
RLS policies
MCP/API authentication behavior
deployment configuration
tests
```

Search specifically for:

```text
supabase
auth
user_id
owner_id
created_by
scope
visibility
service_role
anon key
JWT
Authorization
Bearer
```

Then briefly report:

1. existing auth-related substrate
2. frontend framework
3. backend authentication seams
4. tables already carrying user ownership/scope
5. files you intend to modify

Do not redesign unrelated parts of the system.

---

# Architecture requirements

Use **Supabase Auth as the canonical identity provider**.

Desired architecture:

```text
Browser
   ↓
Supabase Auth
   ↓
access token / session
   ↓
Stealth frontend
   ↓ Authorization: Bearer <JWT>
Stealth backend
   ↓
verify Supabase JWT
   ↓
authenticated user ID
   ↓
existing services/database
```

Important distinction:

```text
PUBLIC CLIENT
→ Supabase URL
→ anon/publishable key

BACKEND / trusted workers only
→ service-role key
```

Never expose the service-role key to browser/client code.

Do not put service-role credentials into:

```text
NEXT_PUBLIC_*
VITE_*
browser bundles
frontend source
logs
error responses
```

---

# Frontend requirements

Adapt these to the actual frontend framework.

Add a centralized Supabase browser client.

Support:

```text
sign up
sign in
sign out
Google OAuth
session restoration
current-user retrieval
auth-state changes
```

Create a minimal auth experience consistent with the existing UI:

```text
/sign-in
/sign-up
```

or use the repo's existing routing convention.

The authenticated UI should show at minimum:

```text
user email/avatar where available
sign-out control
```

Protect authenticated product pages appropriately.

Do not create a huge account-management product.

---

# Session behavior

Authentication must survive page refreshes.

Handle:

```text
loading session
authenticated session
unauthenticated state
expired token
sign-out
OAuth callback
```

Avoid UI flashes where protected pages briefly render before auth has resolved.

If using Next.js or another SSR-capable framework, use the framework-appropriate Supabase SSR/session pattern rather than relying solely on localStorage.

---

# Backend requirements

Add a single reusable authentication dependency/helper.

Conceptually:

```python
user = require_authenticated_user(request)
```

It should:

1. read:

```text
Authorization: Bearer <access_token>
```

2. validate the Supabase token using the appropriate supported mechanism

3. return a typed authenticated-user object containing at minimum:

```text
user_id
email if available
claims/session metadata where useful
```

4. reject invalid/expired/missing authentication with appropriate HTTP status

Do not duplicate JWT validation logic across endpoints.

---

# Frontend → backend calls

Update the existing frontend API helper/client so authenticated requests automatically include:

```http
Authorization: Bearer <Supabase access token>
```

Do this centrally.

Do not manually add auth headers in dozens of components.

Desired flow:

```text
get session
→ obtain access token
→ API client attaches token
→ backend resolves user
```

---

# Public vs protected endpoints

Do NOT blindly put authentication on every endpoint.

Classify existing endpoints into:

### Public

Examples may include:

```text
health
landing/product metadata
explicitly public Procedure discovery
public MCP discovery if currently intended
```

### Authenticated

Examples may include:

```text
personal/repository/project data
private Problems
user-owned executions
private procedures
account-specific activity
writes that should have an owner
```

### Internal/service

Examples:

```text
ingestion workers
background jobs
service-role operations
administrative processes
```

These should NOT pretend to be end-user Supabase sessions.

Preserve the existing worker/service authentication model.

---

# Critical StealthLab privacy semantics

The existing system has concepts such as:

```text
GLOBAL
PROJECT
REPOSITORY
PERSONAL
```

Authentication should make those scopes enforceable.

The intended direction is:

```text
GLOBAL
→ accessible according to public/global policy

PERSONAL
→ authenticated owner only

PROJECT / REPOSITORY
→ only authorized members/users once membership exists
```

For this task, do not invent a giant organization/RBAC system unless the repo already has one.

But ensure that obvious owner-scoped reads/writes cannot simply trust a client-supplied `user_id`.

Bad:

```json
{
  "user_id": "someone-else"
}
```

Good:

```text
user_id comes from authenticated Supabase JWT
```

The backend should derive identity from the session.

---

# Database/RLS

Inspect whether Supabase Row Level Security is already used.

If appropriate and compatible with the current backend architecture, add or improve RLS for clearly user-owned tables.

However:

**do not break trusted backend/service-role operations.**

Remember:

```text
service role
→ trusted server/worker context

authenticated user
→ user-scoped permissions
```

Do not run destructive migrations.

Any schema/RLS migration must:

```text
be checked into the repo
be reversible/conservative
preserve existing data
not change global procedural knowledge ownership accidentally
```

Do not suddenly assign existing global Procedures to the first authenticated user.

---

# MCP considerations

StealthLab currently exposes MCP functionality.

Do not casually make the entire MCP server inaccessible during this change.

Separate:

```text
public/global procedural lookup
```

from:

```text
private/personal/repository-scoped knowledge
```

If MCP authentication is not already cleanly supported by the current host/client setup, do the minimum necessary for the web/API auth layer and clearly document MCP auth as a follow-up rather than inventing an unsafe token scheme.

But ensure private data cannot leak through MCP merely because the normal HTTP UI is authenticated.

Inspect this carefully.

---

# Environment variables

Use the repo's existing naming convention if equivalent variables already exist.

Likely frontend/public variables:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
```

or framework-prefixed equivalents.

Backend trusted variables may include:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

Do not rename working existing variables unnecessarily.

Update:

```text
.env.example
deployment docs
GitHub/hosting secret documentation
```

without committing real secrets.

---

# Google OAuth

Configure application code for Supabase Google OAuth.

Expected user flow:

```text
Sign in with Google
→ Supabase OAuth
→ callback
→ session established
→ redirect to app
```

Add any required callback route.

Document the exact redirect URLs we need to configure in the Supabase dashboard for:

```text
localhost development
production domain
```

Do not hardcode one deployment URL throughout the application.

---

# Error handling

Users should get sensible states for:

```text
invalid credentials
account already exists
OAuth failure
expired session
network error
backend 401
backend 403
```

Do not expose raw Supabase/backend internals unnecessarily.

---

# Tests

Add targeted tests for the important boundaries.

At minimum verify:

```text
unauthenticated request to protected endpoint → rejected
valid authenticated user → accepted
malformed token → rejected
expired/invalid token → rejected
client-supplied fake user_id cannot impersonate another user
public endpoint remains public
service/worker path is not broken
```

Frontend tests should cover auth-state routing if the project already has frontend test infrastructure.

Do not build an enormous testing framework if none exists.

---

# Important non-goals

Do NOT:

```text
rewrite the ingestion system
rewrite Procedure retrieval
change find_best_way semantics
change the TaskGraph executor
replace Supabase/Postgres
create another User database unless genuinely required
introduce a second auth provider
build billing
build teams/orgs from scratch
build elaborate RBAC
make all Procedures private
make ingestion workers authenticate as end users
expose service-role credentials
```

---

# Acceptance criteria

The change is complete when:

```text
1. New user can sign up.
2. Existing user can sign in.
3. Google OAuth works structurally and required dashboard config is documented.
4. Refresh preserves the authenticated session.
5. Sign out clears the session.
6. Protected frontend routes reject/redirect unauthenticated users.
7. Frontend API calls automatically attach the Supabase access token.
8. Backend validates that token centrally.
9. Backend derives user identity from authentication, not request payload.
10. Public endpoints remain usable.
11. Existing ingestion workers still work.
12. Existing MCP/retrieval behavior is not accidentally broken.
13. No service-role secret enters frontend/browser code.
14. Tests pass.
```

---

# At the end report

Return:

```text
A. architecture found before changes
B. auth architecture implemented
C. files changed
D. migrations/RLS policies added
E. frontend routes/components added
F. backend endpoints now protected
G. endpoints intentionally left public
H. environment variables required
I. exact Supabase dashboard settings I must configure manually
J. local run/test instructions
K. production deployment checklist
L. tests and results
M. known follow-ups, especially MCP private-scope authentication
```

Favor the smallest production-quality change that cleanly integrates Supabase Auth into the existing StealthLab architecture.
