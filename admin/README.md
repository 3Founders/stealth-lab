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

### See it without a backend (sample data)

```powershell
npm run dev:demo                  # http://localhost:3201, beside the real app on 3200
```

This answers every `/v1/orgs/...` read from a synthetic organisation ("Demo Organisation (sample data)", providers
`demo-provider-*`, people `Sample Person N`), so every page can be seen and tested with no database. It is development
only: it needs `NEXT_PUBLIC_DEMO_DATA=1` (the script sets it), it is ignored in a production build, writes are refused,
and a yellow **SAMPLE DATA** banner is on every page, including in print. The data is generated from one set of
person-by-model-by-day groups, so organisation totals, per-person totals and cost components agree to the micro-dollar.
Platform-operator pages (`/operations` and friends) are not part of the sample and show an honest "couldn't load".

## What needs what

| Page | Backend | Who |
|---|---|---|
| **Executive** (`/`, the home page) | the org endpoints below (usage, users, performance summary, budget, denials, audit, members) | org owner or admin |
| Operations (`/operations`), Ingestion, Review, Moderation, Credits | `/v1/admin/*`, `/v1/economy/*` | platform staff with the `admin:ops` scope; linked quietly as "Operator tools" |
| Policy, Usage, Performance, People, Calls, Denials, Audit | `/v1/orgs/{org_id}/...` | org owner or admin |
| Compliance (legal holds, erasure) | `/v1/orgs/{org_id}/...` | org owner |

The `/v1/orgs/...` governance API lives in the backend governance work and needs migrations 136-139. Until it is
deployed, those pages show an explicit "couldn't load" state rather than empty or invented numbers.

## The Executive page, in short

Home page (`/`), for an organisation's owners and admins. Organisation-level only: no names, ids or per-person figures
(a test fails if one appears). In order: a one-word **status verdict** with every reason; five **headline numbers** with
change versus the previous equal-length period; the **North Star** (people active in the last 7 days; interim, it
becomes verified successful tasks per week once those results are recorded) with a **metric tree** showing how spend
breaks into completed requests and cost per completed request; **What changed** (spend change split exactly into volume
versus unit cost, with the biggest model movers); **Spending** (budget bar with 75% / 90% markers, daily spend against
an even pace, by model and provider); **Adoption** (head-counts by how often people use it, people active per day);
**Reliability and speed**; **Risk and control** ("Prompts are never stored", policy, limits, refusals, audit integrity,
recent admin changes); and **Return on investment**, which stays "Not measured yet" with the savings formula and no
figure. Alert thresholds can be changed for your browser at the bottom; they change what is highlighted, not what the
organisation enforces.

## Checks

```powershell
npm run typecheck
npm test            # vitest, 115 tests: money, aggregation, ranges, CSV, alerts, thresholds, sample-data consistency,
                    # and every page mounted against the sample organisation (the closest thing to opening it)
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
- Cost per request divides by requests that completed, because only completed requests carry a cost. It is not "cost per
  successful task", which needs verified results the backend does not yet record.
- The Executive page never identifies an individual; per-person pages are limited to owner, admin and team lead.
- Exports neutralise spreadsheet formulas.
