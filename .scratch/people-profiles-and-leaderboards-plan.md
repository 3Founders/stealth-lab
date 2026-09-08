# People profiles, people search, leaderboards

Status (2026-09-08): Workstreams B, C, D **BUILT** on gate-2b (commits 5c6b2f1
backend, f43f6de frontend, 4abf2f0 auth-nav). Workstream A (cross-problem
*solution* leaderboard) NOT built — needs its own `/v1/leaderboard` aggregation
endpoint; the shipped `/leaderboard` page is the *contributor* leaderboard only.
Reality differed from the original plan: migration 28 already had `users`
(with `display_name`), and launch-compliance (`STEALTHLAB-*-SPEC-V1.md`,
`publication_records`, `audit_events`, `data_rights.py`) was already in place —
so the build EXTENDED those (INV-01 private-by-default opt-in, one audited
`profile_visibility_changed` event, export/deletion coverage) instead of adding
a parallel `actor_profiles` table. What actually shipped:
  - `db/48_contributor_profiles.sql` (user_id, visibility, disclosed_at, tagline)
  - `app/services/contributors.py`, `app/api/{profile,contributors}.py`
  - `/v1/me/profile` GET/PUT · `/v1/contributors/{search,leaderboard,{id}}`
  - frontend `/people`, `/leaderboard`, `/contributors/[id]`, `/me/privacy`
    toggle, `/me` + `/auth` disclosure copy, nav links
Original plan below kept for the Workstream A design and the founder decisions.

---

Original plan (Workstream A still open): Needs sequencing with the
`gate-2b` session that owns backend retrieval + `frontendv1` search/procedure/solution
presentation.

## What the user asked for

1. A leaderboard page.
2. Ability to search other people.
3. Privacy: a person's **display name + contribution counts are public**, and the person
   **must be told at login** that this is public (informed, not opt-in).

## What exists today (verified by inspection, not assumed)

- `GET /v1/problems/{id}/leaderboard` — ranks **solutions** by Wilson-lower verified
  success *within one problem's benchmark*. The only leaderboard. Surfaced on
  `/problems/[id]`.
- `GET /v1/me` (`backend/app/api/me.py`) — the **only** people-facing endpoint. Returns
  the caller's own contributions; 401 for anonymous. No `id` param, no other-user read.
- `personal_contributions.py` reads real provenance columns only:
  - `procedures.created_by` (actor subject string)
  - `knowledge_nodes.created_by` where `node_type='claim'`
  - `executions.actor_id` (who ran, not who wrote the row)
  All filtered through `scope_predicates()`.
- **No `users` / `actors` table.** No display-name storage. No per-actor aggregate score
  anywhere — `personal_contributions.py`'s docstring explicitly refuses to invent one.
- Actor subject is opaque: Supabase `sub` (UUID) for signed-in users, `X-Viewer-Id`
  string in dev. Nothing maps a subject to a human name.
- Next free migration number: **48**.

## Gap

A people leaderboard and people search both need (a) a subject→profile table, (b) a
defensible public aggregate metric, (c) the login disclosure. A cross-problem *solution*
leaderboard needs only an aggregation endpoint.

---

## Workstream A — cross-problem solution leaderboard (smallest, no privacy dependency)

**Backend**
- `GET /v1/leaderboard` (new, in `backend/app/api/` — likely `problems.py` or a new
  `leaderboard.py`). Aggregates the same per-solution Wilson-lower verified-success
  aggregation already in `problems/{id}/leaderboard`, but across **all** benchmarks a
  solution has comparable completed evaluations for. Query params: `limit`, `metric`
  (`verified_success` default | `first_pass` | `cost` | `latency`), `benchmark_family`
  filter optional.
- Reuse the existing leaderboard aggregation service; do **not** re-derive ranking in a
  new place. If the current aggregation is problem-scoped in SQL, extract the scoring
  into a shared function and call it with a wider row set.
- Honesty rules carried over: only comparable evaluations; `INSUFFICIENT_EVIDENCE`
  state stays; empty `current_best` never fabricated.

**Frontend (`frontendv1`)**
- `src/app/leaderboard/page.tsx` — table: solution, target problem(s), verified success
  (Wilson-lower), runs, state. Metric switcher (reliability / first-pass / cost /
  latency) only for metrics the backend returns. Reuse `components/leaderboard.tsx`
  primitives; do not fork them.
- `src/lib/api/client.ts` + `types.ts`: add `getGlobalLeaderboard(...)` + `LeaderboardRow`
  reuse. **These two files are owned by the `gate-2b` session — coordinate before
  editing.**
- Nav: add "Leaderboard" between "Search" and "Problems" in `layout.tsx` (auth lane —
  ours).

**Tests**: contract test for `/v1/leaderboard` shape; component test for metric switch +
empty state; route smoke test. (Appendix C discipline: proving tests in the same change.)

---

## Workstream B — actor profiles + login disclosure (privacy foundation)

**Migration 48 — `48_actor_profiles.sql`**
```
actor_profiles (
  subject         text primary key,          -- Supabase sub, or X-Viewer-Id in dev
  display_name    text not null,             -- captured from Supabase user_metadata
  auth_provider   text not null,             -- 'supabase' | 'viewer'
  visibility      text not null default 'public'
                    check (visibility in ('public','hidden')),
  disclosed_at    timestamptz,               -- when the person acknowledged the notice
  t_created       timestamptz not null default now(),
  t_updated       timestamptz not null default now()
)
```
- Additive, idempotent, header states "next free number is 49" after landing.
- No backfill (fresh-start rule). Rows created lazily on first authenticated request.

**Backend**
- `POST /v1/me/profile` — upsert `display_name`, set `disclosed_at = now()`, set
  `visibility`. Called by the frontend once the person acknowledges the disclosure.
  Identity via the same `get_scope` path as `/v1/me` — no second identity path.
- Extend `GET /v1/me` response with `profile: { display_name, visibility, disclosed_at }`
  (or `null` if not yet created) so the frontend knows whether to show the interstitial.
- Profile creation is **not** automatic on token validation — it happens on the
  disclosure acknowledgement, so `disclosed_at` is always truthful.

**Frontend**
- First-login interstitial (client): after sign-in, if `me.profile == null`, show a
  one-screen notice — *"Your name (`<from Google>`) and your contribution counts
  (procedures authored, verified successes) will be publicly visible on your Stealth Lab
  profile and on the contributor leaderboard. You can hide your profile later in
  Settings."* — with **Continue** (writes `POST /v1/me/profile`, `visibility='public'`)
  and **Hide my profile** (`visibility='hidden'`). Either choice records `disclosed_at`.
- `/me` page: add a visibility toggle (public / hidden) that calls the same endpoint.
- `auth/page.tsx` copy: add one line under the sign-in buttons naming the public-profile
  consequence *before* the user signs in, not only after.

**Tests**: migration idempotency + upgrade-path e2e; `POST /v1/me/profile` contract +
401-anon; interstitial render/skip logic; `disclosed_at` always set when a profile row
exists.

---

## Workstream C — people search + public profile pages (depends on B)

**Backend**
- `GET /v1/users?q=&limit=` — search `actor_profiles` where `visibility='public'` by
  `display_name` (trigram / `ILIKE`, not vector). Returns `[{subject, display_name,
  procedures_authored, verified_successes, claims_authored}]`. Counts computed by the
  **explicitly defined** aggregation below.
- `GET /v1/users/{subject}` — public profile: the profile row + the same contribution
  *lists* `personal_contributions.py` already builds (procedures, claims, executions),
  but filtered to `scope_predicates()` for the **requesting** viewer and only if the
  target is `visibility='public'` (else 404 — a hidden profile does not reveal it
  exists). Reuse `get_personal_contributions()` with a `subject` argument instead of
  duplicating its queries.
- New reviewed metric, documented in `personal_contributions.py` style (state the
  definition, refuse to over-claim):
  - `procedures_authored` = count of `procedures` rows, `created_by = subject`,
    `t_invalid IS NULL`, visible to the requester.
  - `verified_successes` = count of those with `verification_state = 'verified'`.
  - `claims_authored` = count of `knowledge_nodes` claim rows, `created_by = subject`,
    `t_invalid IS NULL`.
  - No composite "score", no ranking of people beyond a single sortable count.

**Frontend**
- `src/app/people/page.tsx` — search box + result rows (name, the three counts).
- `src/app/users/[subject]/page.tsx` — profile: name, counts, authored procedures
  (link to `/procedures/[id]`), authored claims, recent verified executions. Honest
  empty states.
- Global search (`search/page.tsx`, `gate-2b`-owned): add a `person` result type —
  coordinate with that session; it is already adding new hit-shape fields.
- WebMCP: optional `inspect_person(subject)` tool mirroring `/v1/users/{subject}`.

**Tests**: `/v1/users` excludes hidden profiles; `/v1/users/{hidden}` → 404;
cross-viewer scoping on the profile lists; route + component tests.

---

## Workstream D — contributor (people) leaderboard (depends on B + C metric)

**Backend**
- `GET /v1/leaderboard/contributors?metric=&limit=` — rank `actor_profiles` with
  `visibility='public'` by `verified_successes` (default) or `procedures_authored`.
  Same metric definitions as C. Ties broken by earliest `t_created`.

**Frontend**
- Add a "People" tab to `/leaderboard` (from Workstream A) rather than a separate route:
  toggle between "Solutions" and "Contributors".

**Tests**: hidden profiles absent from the ranking; metric switch; empty state.

---

## Sequencing

1. **A** can start now — only collision is `client.ts`/`types.ts` (ping `gate-2b`) and
   `layout.tsx` nav (ours).
2. **B** is the gate for C and D. Migration 48 + disclosure must land and be reviewed
   before any public people data is served.
3. **C** then **D**.
4. All backend endpoints are backend-lane; the `gate-2b` session is active there. Land
   these as their own commits with proving tests (hard rule 7), pytest counts in each
   message.

## Open decisions for the founder

- **Hidden-by-default vs public-by-default with disclosure.** User said public + informed.
  Recorded here as `visibility='public'` default with a mandatory `disclosed_at` gate and
  an easy switch to `hidden`. Confirm this is the intended posture (it is a Band-0-class
  visibility ruling).
- **Dev `X-Viewer-Id` actors**: include in profiles/search/leaderboard, or restrict all
  people surfaces to `auth_provider='supabase'`? Recommend Supabase-only to avoid
  unauthenticated names appearing publicly.
- **Subject in URLs**: `/users/{supabase-sub-uuid}` is ugly and correlates to the IdP
  subject. Consider a separate opaque `profile_id` (uuid7) as the public handle, with
  `subject` kept internal.
