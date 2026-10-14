# Frontend summary

Written 2026-10-09 by the Frontend session. It covers the two web apps this session touched, what was built and why,
how to run it, what it depends on in the backend, and what is open. It is a summary of work done, not a spec: where a
claim was not verified, it says so.

## 1. The two apps

| App | Folder | Purpose | Port |
|---|---|---|---|
| Customer app | `prod_frontend/` | Public product site and signed-in app: goals, search, docs, account, onboarding, OAuth consent | 3100 |
| Admin console | `admin/` | Operations and governance dashboard. Home page is an Executive view for a customer company's leadership | 3200 |

Both are Next.js 15 / React 19 with the same stylesheet, Barlow font, header and Supabase sign-in. `admin/` was created
from `prod_frontend/`'s theme on purpose so the two look and behave alike. The older `frontend/` (debate and approval
UI) is not part of this work and has no org, usage, policy or cost calls.

## 2. prod_frontend changes made this session

| Commit | Change |
|---|---|
| `9e9c9c8` | Goals page: the view, resolution and category filters now sit in one horizontal row |
| `a694bb8` | Goal detail page: the "Success" text was rendered one character per row, because `humanize()` ran `Object.entries()` on a plain string. `humanize()` now returns a string as a single row |

Earlier in the session (before the thread was forked) the question was why procedures and goals were not showing in
`prod_frontend`: the UI browsed a disconnected `problems` table that ingestion never populated. The Problems-to-Goals
replacement is now visible in the app (it has `/goals` routes) and a migration named `110_merge_problems_into_goals.sql`
exists in `backend/db`. I did not verify that work end to end, and my own draft `110_retarget_goal_id_to_goals.sql` was
never applied.

## 3. The admin console (`admin/`)

### 3.1 Who it is for
Two audiences, deliberately separated:
- **Customer executives and org admins.** The home page `/` is an Executive view. Org pages sit on a second navigation row.
- **Platform operators (keळ staff).** Operations, Ingestion, Review, Moderation and Credits. They appear only on those
  pages, reached through a quiet "Operator tools" link, so customers never see tools they cannot use.

### 3.2 Pages

| Route | Audience / role | What it shows | Backend |
|---|---|---|---|
| `/` Executive | org owner, admin | Status verdict, 5 headline numbers with change vs the previous period, Spending, Adoption, Reliability and speed, Risk and control, ROI (not measured), printable one-pager | org endpoints below |
| `/policy` | owner, admin | Allow-lists and budgets editor, kill switch, "coming" cards | `/policy`, `/kill-switch` |
| `/usage` | owner, admin | Input / output / processing cost split, month-to-date and projection, burn-down, price per 1M tokens, by model / tool / day, spend spikes | `/usage` |
| `/performance` | owner, admin | Latency, router overhead, cache hit rates; merged percentiles for the whole range | `/performance`, `/performance/summary` |
| `/people` | owner, admin, team lead | Live budget, spend by person, member list (read only) | `/usage/users`, `/budget`, `/members` |
| `/calls` | owner, admin, team lead | Every allowed model call (metadata only), filters, paging | `/calls` |
| `/denials` | owner, admin, team lead | Refusals by reason and events | `/denials` |
| `/audit` | owner, admin | Hash-chained audit log, integrity badge, CSV export | `/audit` |
| `/compliance` | owner | Legal holds, two-owner erasure workflow | `/legal-holds`, `/erasure-requests` |
| `/coming` | any org admin | Planned, unbuilt features, with no numbers | none |
| `/operations`, `/ingestion`, `/review`, `/moderation`, `/credits` | platform operators | Ingestion health, review queue, moderation, Credits ledger and clawback | `/v1/admin/*`, `/v1/economy/*` |
| `/sign-in` | everyone | Email and password, Google, GitHub | Supabase |

All org endpoints are under `/v1/orgs/{org_id}/...`. The org picker comes from `/v1/orgs/mine`.

### 3.3 The Executive view
- **Status verdict:** "On track", "Needs attention", "Action needed" or "No activity yet", with every reason listed. The
  worst reason sets the level. Inputs: kill switch, audit integrity, budget pace, failure rate (watch at 5%, action at
  15%), no policy, spend spikes, people at their daily cap, requests with unknown cost.
- **Aggregates only.** The Executive page has no names, no per-person spend and no ranking. People appear only as counts.
  Reason: people fear usage data feeding performance reviews, and per-person activity counts are easy to game.
- **Prompts are never stored.** The Risk and control section states this. Calls and denials are metadata only.
- **ROI is "Not measured yet".** The page shows the formula `Savings = cost on the default model - actual cost` with no
  figure, because the backend does not record the default-model cost yet.
- **Verified pass rate is "Not available yet".** Check results from `report_result` are not joined to an organisation in
  the backend.

### 3.4 Rules the code enforces
- Dollar amounts are summed as exact integer micro-dollars from the backend's decimal strings. They are never added as
  floats and never recomputed from tokens times price (prices are snapshotted per call on the server).
- Latency percentiles are never averaged. Daily rows show the day's own value; whole-range figures come from the
  backend's merged summary.
- A null cache rate is "not reported", not 0%. A null tier is "untiered".
- Every card loads independently and says "couldn't load", "not permitted" or "not set up yet" instead of going blank.
- Ranges longer than 366 days are blocked in the browser (the backend returns a 422).
- CSV exports neutralise spreadsheet formulas.
- Budget alerts start at 75% and 90% of the monthly budget.
- Alerts (budget watch, spend spikes) are computed in the browser and appear only while a page is open. Real alert
  delivery (email, webhook) needs server-side rules.
- Hiding a page by role is a courtesy. The backend decides access from the user's token and the org's roles.

### 3.5 Roles
Backend roles today: owner, admin, member, viewer. `team_lead`, `executive` and `auditor` are typed in the code but get no
extra rights until the backend supports them. Note that the backend still requires owner or admin for every `/v1/orgs`
read, so a team lead would currently be refused even though the UI would show them the people pages.

### 3.6 Added on 2026-10-14 ("completely build this dashboard")
- **Executive page:** North Star (people active in the last 7 days) and a metric tree; a "What changed" section that splits a
  spend change exactly into volume versus unit cost and lists the biggest model movers; adoption depth as head-counts;
  cost per *completed* request (the earlier version divided by attempts, which understated it); executive-friendly dollar
  rounding.
- **Configurable alert thresholds** (failure watch and action levels, budget warn and urgent levels), kept per organisation
  in the browser; they change what is highlighted, never what is enforced. Invalid or out-of-order values are rejected.
- **Sample-data mode** (`npm run dev:demo`, port 3201): a clearly bannered synthetic organisation so every page can be seen
  and tested without a backend. Development only; ignored in production builds; writes refused.
- **Tests:** 115 passing in 10 files, including a render test that mounts every page against the sample organisation and
  asserts the Executive page never shows an individual.
- **Operations** (`/operations`) lost its duplicate organisation block and now shows API failures as errors instead of an
  endless "...".

## 4. Where the code is

```
admin/
  app/                 pages (one folder per route)
  components/          AdminHeader, OrgContext, OrgGate, Range, Widgets, ExecWidgets, Admin
  lib/org-api.ts       the single typed client for /v1/orgs/...; role helpers
  lib/admin-api.ts     operator endpoints
  lib/money.ts         exact micro-dollar arithmetic
  lib/usage.ts, perf.ts, people.ts, exec.ts, alerts.ts, range.ts, export.ts, denials.ts
  tests/               vitest
  README.md            run instructions and rules
```

## 5. Running it

```powershell
# backend (from backend\): add the admin origin to backend\.env first
#   FRONTEND_ORIGIN=http://localhost:3000,http://localhost:3200
uvicorn app.main:app --reload

# admin (from admin\)
copy .env.example .env.local     # NEXT_PUBLIC_KEL_API_URL, NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY
npm install
npm run dev                      # http://localhost:3200
```

The Supabase key is needed only for sign-in (`anon` is the legacy name for the publishable key). The org pages need a
database that has migrations 136-139 and an organisation in which the signed-in user is an owner or admin. There is no
screen for creating an organisation. The admin app connects to no database itself; it only calls the backend.

Checks, from `admin\`: `npm run typecheck`, `npm test`, `npm run build`.

## 6. State of the work

| Item | State |
|---|---|
| `admin/` initial console | Pushed (`9bb9d6e`) |
| Executive home page | Pushed (`c1e9daa`) |
| Executive spec, role gates, `/coming`, audit newest-first | On `origin/main` as `1cc9b9b` (my local commit `675034b` was rebased by another session, which changed its hash). Checked 2026-10-14: `admin/` matches `HEAD`, 58 files |
| Verification | `tsc` clean, vitest 115 passed (10 files), `next build` clean (17 routes) as of 2026-10-14. The 2026-10-14 additions are uncommitted |
| Live backend | Never run against one. The org endpoints needed migrations 136-139, which were only on local scratch databases when the pages were written |

## 7. Open items

Backend work still needed (requested from the backend sessions, with shapes proposed):
- **Verified pass rate:** per org per day `checks_passed` and `checks_failed`. Blocked on a design decision (the data
  lives on search shards with no org column).
- **Savings vs the default model:** cost on the default model recorded per call.
- **Spend and errors per connection:** `group_by=connection` and a connections list.
- **Teams, nested budgets, request-a-higher-limit, new roles, change proposals:** not built.
- **Provider key management, model catalog and rules, home region, zero-data-retention per provider:** not built.
- **Member invites, role changes, removal:** not built (the member list is read only).
- **Server-side alert delivery:** needs a delivery channel and a scheduler.
- **Newest-first audit (`order=desc`):** wired with a fallback to a 3-page walk; not deployed yet.
- **find_ways metrics per org and memory health counts:** the underlying tables have no org id.

Frontend work not yet done:
- `/models` and `/connections` pages, reserved for the MCP model-serving session; I will build them once I get endpoint
  paths and exact JSON field names.
- A sample-data preview mode so the Executive page can be seen without a backend (offered, not built).
- Hosted deployment of `admin/`, and adding its origin to the backend's `FRONTEND_ORIGIN`.

## 8. Things to know about the shared checkout

- Several Claude sessions share this working tree. An earlier `git pull --rebase --autostash` left conflict markers in
  `backend/Dockerfile.mcp-server` and `docs/deploy/hosted-mcp.md`. They are clean now (I did not resolve them). `git stash
  list` still shows several `autostash` entries and one older WIP stash that may hold other sessions' uncommitted work.
- Do not pull into the shared checkout while other sessions have uncommitted changes. Push from a separate worktree or
  commit only the paths you own.
- Peer sessions the Frontend session exchanged messages with: the backend governance session (`telemetry`), the
  executive-spec session (`MCP tool removal and test fallout`), and the MCP model-serving session (`MCP serving tools
  overview`).

## 9. Research behind the dashboard design

Two rounds of web research (vendor and consultant sources, so feature claims are unverified until seen in a demo):
- Standard executive dashboards: few KPIs, large numbers with trend and comparison, outcomes over activity, role-specific
  views, the "5-second" rule of thumb.
- Current AI-spend and adoption products: GitHub Copilot's impact dashboard (adoption cohorts, a labelled-directional ROI
  section), Ramp AI token spend (by user, project, model, team, weekly briefing), CloudZero (unit economics), Vantage, and
  the FinOps Foundation's finding that 98% of FinOps teams now manage AI spend.
- Ideas not yet built: adoption cohorts (power, regular, light, inactive), a "what changed and why" panel, savings hints,
  and a clearly labelled what-if ROI calculator driven by the executive's own assumptions.
