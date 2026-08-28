# Docket — the v0 approval UI

Connects directly to the Phase A backend. Three routes: `/approvals` (the
docket — pending scorecards), `/approvals/[id]` (the case file — full
argument, evidence, objections, transcript, and the approve/reject
ruling), and `/` (redirects to the docket).

## What was actually verified here

**Confirmed clean, verified with real network access on 2026-08-29**
(Node v24.18.1, npm 11.16.0). A prior pass through this repo, done from
inside a sandbox with no outbound network access, reported two problems
on a fresh clone: (1) `npm install` completing but leaving
`node_modules/.bin` unpopulated, and (2) `npm run build` failing
TypeScript type-checking on a clean install. Neither reproduced here.
Root-caused by actually trying it, not guessed at:

- **`.bin` population**: deleted `node_modules` entirely and ran both
  `npm ci` and, separately, a from-scratch `npm install` — each
  populated `node_modules/.bin` fully and identically (51 entries,
  including `next`, `tsc`, `tsserver`). No lockfile drift (`npm ci`
  succeeded against `package-lock.json` unmodified), no AV/Defender
  interference observed. The earlier report was very likely an artifact
  of the sandboxed environment it ran in, not a real bug in this repo.
- **Type-checking**: with the real shipped `app/layout.tsx` (genuine
  `next/font/google` loading — `IBM_Plex_Mono`, `IBM_Plex_Sans`,
  `Source_Serif_4`, unstripped) and real access to
  `fonts.googleapis.com`, `npm run build` completed cleanly through
  compilation, type-checking, and static generation for all ten routes.
  No `tsc` error to chase — there wasn't one. The earlier "sandbox
  couldn't verify this" caveat from the previous pass is resolved: it's
  now been run for real, on Vercel-equivalent network access, and it
  works.

Worth running `npm run build` yourself once too, rather than taking this
on faith — but as of this date, on a genuinely fresh `node_modules`, it
is confirmed working end to end.

One dependency note: `npm install` initially resolved a Next.js version
with a published critical CVE (cache poisoning / RCE-adjacent, per `npm
audit`). Bumped to the patched release before writing any app code
against it — check `npm audit` yourself after `npm install` if you add
or change dependencies later.

`postcss` (transitive, via `next` — this app has no `postcss.config.js`
of its own) later showed 4 high-severity advisories: XSS via unescaped
`</style>` in its stringifier, and three rounds of arbitrary-file-read via
attacker-controlled `sourceMappingURL` in CSS comments. Both classes only
matter when postcss stringifies or resolves source maps for
*attacker-controlled* CSS; this app only ever runs it over its own
checked-in `app/globals.css` at build time, so neither path was actually
reachable — but since `next@15.5.22` pins `postcss` to an exact vulnerable
version (`8.4.31`) rather than a range, `npm audit fix` alone can't move
it without a breaking `next` major bump. Fixed properly instead, via an
`overrides` entry in `package.json` pinning `postcss` to `^8.5.26`
(patched, same 8.x API `next` already targets) — confirmed clean with a
fresh `npm install` + `npm audit`.

The remaining `npm audit` finding is in `sharp`, which only matters for
`next/image`; this app doesn't use it.

## Setup

```bash
npm install
cp .env.local.example .env.local
# edit .env.local if the API isn't on localhost:8000
npm run dev
```

Requires the backend running with `FRONTEND_ORIGIN` in its `.env` set to
match wherever this runs (`http://localhost:3000` for local dev — that's
already the backend's default).

## The API contract this depends on

`lib/api.ts` is the single source of truth for the shapes this app
expects — every type in it mirrors a real backend model or SQL row, not
a guess. If the backend's response shape changes, this is the one file
to update; nothing else in the app touches the API directly.

| Call | Backend route | Used by |
|---|---|---|
| `api.listPending()` | `GET /v1/approvals/pending` | docket page |
| `api.getDetail(id)` | `GET /v1/approvals/{id}` | case file page — this route didn't exist before this session; the list endpoint alone doesn't return enough to review responsibly (no fallacy flags, no change set, no transcript) |
| `api.decide(id, ...)` | `POST /v1/approvals/{id}` | the approve/reject buttons |
| `api.runScan()` | `POST /v1/admin/scan` | the "Run scan" button — see below |

## The gap this surfaces, worth knowing before you rely on the demo

Before this session, nothing in the backend ever called
`TriggerDetector.scan()` or `LoopOrchestrator.run()` — the loop existed
as code but nothing invoked it automatically. For v0 there's still no
scheduler; `POST /v1/admin/scan` is a manual trigger, meant to be called
from a cron job, a dashboard button (which is what "Run scan" on the
docket page is), or `curl`, until real scheduling is worth building.

**On a fresh database, "Run scan" will correctly find nothing** — there's
no trace data yet for anything to cross a threshold on. Run the
backend's `python scripts/bootstrap_demo.py` first (seeds a demo
workflow and trace data specifically shaped to trigger); only then does
"Run scan" have a bottleneck to find.

**It also needs all four LLM provider API keys configured in the
backend's `.env`** (Anthropic, Fireworks, OpenAI, Google — the fourth
exists specifically so the judge has a model family independent of the
panel). Without them, "Run scan" will find the trigger but fail to run
the debate, and will say so in its error message rather than failing
silently.

## Deployment

Vercel is the natural fit — this is what the project's stack was chosen
around (`next/font/google` needing real network access is itself a sign
this wants a real hosting environment, not a sandbox). Since this lives
in a monorepo alongside `backend/`, one non-default setting is needed:
when importing the project, set **Root Directory** to `frontend` in
Vercel's project configuration screen — without it, Vercel tries to
build the repo root, finds no `package.json` there, and fails. Then set
`NEXT_PUBLIC_API_BASE_URL` to the deployed backend's URL in Vercel's
environment variable settings, and set `FRONTEND_ORIGIN` in the backend
to the resulting `*.vercel.app` URL so CORS allows it.

## What's deliberately not here yet

Auth (the approver-id field is free text — matches the backend's
unenforced `approver_role` placeholder, not a real login), the debate
transcript view doesn't yet render citations inline against the graph,
and Layer 2 metrics have no UI since Layer 2 doesn't exist until v1.1.
None of these block a working v0 demo; all three are real next steps.
