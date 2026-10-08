# keळ admin console

Operations and governance dashboard for keळ. A separate Next.js 15 / React 19 app that reuses `prod_frontend`'s
stylesheet, fonts, Supabase sign-in and API client, so the two look and behave alike.

Authorization is the backend's, never this app's: every request carries the signed-in user's Supabase token, and the
backend answers 401 / 403 / 404 / 409 as appropriate. Pages hide what a role can't use, but that is a courtesy.

## Run it

```powershell
cd admin
npm install
copy .env.example .env.local      # then fill in the three values below
npm run dev                       # http://localhost:3200
```

`.env.local`:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_KEL_API_URL` | the backend, e.g. `http://127.0.0.1:8000` (`uvicorn app.main:app --reload` from `backend/`) |
| `NEXT_PUBLIC_SUPABASE_URL` | same Supabase project as `prod_frontend` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | its anon key (public by design) |

The backend must allow this origin: add `http://localhost:3200` (and the hosted admin origin later) to its
`FRONTEND_ORIGIN` setting.

## What needs what

| Page | Backend | Who |
|---|---|---|
| **Executive** (`/`, the home page) | the org endpoints below (usage, users, performance summary, budget, denials, audit, members) | org owner or admin |
| Operations (`/operations`), Ingestion, Review, Moderation, Credits | `/v1/admin/*`, `/v1/economy/*` | platform staff with the `admin:ops` scope; linked quietly as "Operator tools" |
| Policy, Usage, Performance, People, Calls, Denials, Audit | `/v1/orgs/{org_id}/...` | org owner or admin |
| Compliance (legal holds, erasure) | `/v1/orgs/{org_id}/...` | org owner |

The `/v1/orgs/...` governance API lives in the backend governance work and needs migrations 136-139. Until it is
deployed, those pages show an explicit "couldn't load" state rather than empty or invented numbers.

## Checks

```powershell
npm run typecheck
npm test            # vitest: money math, usage/perf aggregation, ranges, CSV, alerts, people
npm run build
```

## Rules the dashboard keeps

- Dollar amounts are summed as exact integer micro-dollars from the backend's decimal strings, never as floats, and
  never recomputed from tokens x price (prices are snapshotted per call on the server).
- Latency percentiles are never averaged. Day-level rows show the day's own value; whole-range figures come from the
  backend's merged summary.
- A null cache rate is "not reported", not 0%. A null tier is "untiered".
- Alerts (budget watch, spend spikes) are computed in the browser and only appear while a page is open. Real alert
  delivery needs server-side rules.
- Exports neutralise spreadsheet formulas.
