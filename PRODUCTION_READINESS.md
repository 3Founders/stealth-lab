# StealthLab — Production Readiness Assessment

_Compiled 2026-08-26, refreshed 2026-08-27 from the live repo (`main`), the build
board, and all five lane sessions. Companion doc: `proj_status.md` (phase/roadmap
position). If the two ever disagree, trust whichever was updated more recently._

## The one thing to understand first

There are two different "production" targets in this repo, and they're at very
different stages. Conflating them is the easiest way to misjudge readiness.

**Target 1 — the actual product (v0.1, per `demo.md`).** A *local-first* MCP
server a developer runs on their own machine via `docker compose up`, binding to
`127.0.0.1` only. Single-user. Refusals are audit-mode only (informs, doesn't
block). Hosted/multi-tenant use is an explicit **non-goal** for this version —
not missing, deliberately excluded.

**Target 2 — the hosted public demo.** A live-ish deployment plan (`render.yaml`
+ a Vercel frontend) running the backend in an intentionally open "public
commons" posture: `REAL_AUTH_ENABLED=false`, `PRIVATE_VISIBILITY_ENABLED=false`,
no private data, daily LLM spend capped at $10 total / $1 per viewer. This is a
showcase surface, not a multi-tenant SaaS a customer could privately sign up for
yet — the code has a boot-time refusal (`assert_boot_posture`) specifically to
stop anyone from accidentally flipping it into a half-configured private mode.

Neither target is "done." Below is where each actually stands, as of the latest
push to `main`.

---

## Where we are

**Engine and data layer — mature.** 30 migrations (`db/01` through `db/30`),
tenancy hardened with real row-level-security policies plus an app-layer
builder, atomic evidence/audit writes, append-only tables with tombstone-only
updates, a repo-wide test that bans hand-written tenant filters outside the
sanctioned code path. 1353 tests passing, 0 known regressions across every
recent wave.

**Identity/auth module — built but switched off.** `authn.py` is a real OIDC
(RS256 JWT) gate with a documented threat model, pure-ASGI middleware,
fail-loud rules (bad token → always 401, missing token → anonymous only in
public posture). It exists and is tested. It is simply not turned on, by
design, until OIDC is actually configured somewhere.

**Rate limiting / cost control — built, with a known ceiling.** In-process
token bucket, buffered audit ledger, fail-closed on infra errors, retention
sweep. The code's own docstring names the limitation: this only works
correctly with exactly one worker process (`--workers 1`, matching
`render.yaml`'s free-tier single instance). More than one process would
silently let everyone's rate limit multiply. Redis is the named fix, not yet
needed at current scale.

**Debate/governance loop — smoke-tested, not dogfooded.** The multi-model
panel talks to real APIs successfully after three bugs were found and fixed
earlier this arc (a retired model slug, a reasoning-token trap, models
exceeding JSON output budgets mid-reply). It has run exactly once end-to-end
against real traffic, for about 14 seconds. It has never processed a real
backlog of trace-triggered proposals. Unchanged this wave — no new live runs
attempted, correctly, given the OpenRouter budget wall below.

**Install path — now real, but never boot-tested.** `docker-compose.yml`,
`backend/Dockerfile`, `.dockerignore`, and a healthcheck script were added this
wave (closing what was previously a documented-but-nonexistent install path).
The build context correctly points at the repo root, not `backend/`, because
`app/mcp_server/server.py` imports from the sibling `experiments/swebench_pro/`
at module import time — a naive `context: ./backend` would have crash-looped.
**Caveat, stated plainly: Docker is not installed anywhere in this build.**
The compose file has been validated as syntactically correct YAML against the
compose-spec schema and its import paths traced by hand — it has never
actually been booted. Treat it as "should work," not "confirmed works," until
someone runs `docker compose up -d` for real.

**CI — now exists.** A minimal GitHub Actions workflow runs the offline test
suite on every push/PR. This was the single biggest gap in the previous
version of this document ("nothing stops a broken commit from landing on
`main` except lane discipline") — that's now backstopped by enforcement, not
just discipline.

**Deployment config — exists, partially untested.** `render.yaml` is a
complete, sane Render blueprint: free-tier Postgres 16, free-tier web service,
`uvicorn app.main:app`, secrets correctly marked `sync: false` (set by hand in
the dashboard, never committed), cost governance env vars pre-wired. A Vercel
frontend URL is referenced as the CORS origin, implying a frontend is or was
live there. Whether this Render service is *currently* running, and for how
long the free-tier Postgres actually retains data, can't be verified from the
repo alone — worth confirming directly in Render's dashboard.

**The one gap that actually blocks calling this "shippable"**, separate from
infra: `check_procedure` / `WOULD_REFUSE` — the refusal-with-receipts
capability `demo.md` advertises as C5 — is proven in the evaluation harness
against a live model, but is **not wired into `backend/app/mcp_server/server.py`**
as a callable tool. An agent using the real server today cannot reach it. This
is a product-completeness gap, not an infra gap, but it's the one thing that
would make a demo dishonest if shown as-is. Currently assigned as this wave's
headline task (see `proj_status.md`).

---

## What's required (their own bar, from `demo.md` §3 — not an external standard)

| Requirement | Status |
|---|---|
| Full offline suite green | **Met** — 1353 passed / 115 skipped / 0 failed |
| Migration chain applied clean on a throwaway `pgvector/pg15` container | **Not done** — blocked on Docker access |
| `docker-compose` setup for `docker compose up -d` | **Exists now, not boot-tested** (see above) |
| `bootstrap_demo.py` scripted two-phase story proven on the release commit | Script exists; not run this wave |
| `check_procedure` refusal payload shape pinned by tests | Met *in the harness*; **not reachable through the real server** (see above) |
| `SECURITY.md` + plain-language data statement published | **Met** |
| README quickstart works from a clean clone | **Met** — verified by SHIP against current commit |

Since the last version of this document: 3 of 7 previously-unmet items closed
(compose files, SECURITY.md/data statement, README verification). Two remain
genuinely open (Docker-dependent DB verification, the `check_procedure` wiring);
`bootstrap_demo.py` is untried, not known-broken.

---

## What's planned (documented elsewhere in the repo, not speculation)

- Real OIDC auth flip-on, once an identity provider is actually configured
- Migration chain verification on a disposable database, the moment Docker is available
- Redis-backed rate limiting, with the trigger condition (>1 worker) already
  written into the code as a tripwire
- Crypto-shredded deletion (Band 5, needs a founder ruling — D4)
- Capability-based auto-routing thresholds (Band 1.9b, needs founder ruling D1)
- Bands 4/5 generally — correctly not started; ROADMAP's own rule is that no
  Band 4 item starts before P3's acceptance test has passed on a real user's
  workflow, and P3 is still blocked on the `check_procedure` wiring above

---

## What does not exist at all

- **Observability.** No Sentry, Prometheus, OpenTelemetry, structlog, or any
  logging/monitoring library in `requirements.txt`. If the hosted instance
  errors or goes down, nothing tells anyone.
- **Load testing.** No evidence anywhere of the system being tested under
  concurrent load.
- **Backup / disaster-recovery plan.** Nothing documented for the Postgres
  instance. Worth explicitly confirming Render's free-tier Postgres retention
  policy if any real data is meant to persist there.
- **A live-traffic run of the debate/governance loop.** One 14-second smoke
  test is still the entire track record.
- **A booted, verified Docker install.** The files exist; nobody has run them.

---

## Overall plan, in the order I'd actually do it

1. **Wire `check_procedure` into the real MCP server.** This is the one item
   that turns "we have evidence" into "a user can actually see it." In flight now.
2. **Get Docker running somewhere** (Docker Desktop on any machine in the
   fleet is enough) and use it to boot-test `docker-compose.yml`, verify the
   migration chain on a throwaway container, and run `bootstrap_demo.py`'s
   full scripted story. All three of the remaining checklist gaps share this
   one blocker.
3. **Add baseline error tracking** (Sentry's free tier is enough) on the
   hosted instance, so failures are visible instead of silent.
4. **Confirm the Render Postgres retention story explicitly**, in the
   dashboard, before treating anything stored there as durable.
5. **Only after 1–4:** revisit turning on real auth/multi-tenant mode. It's
   correctly gated off right now — that's a decision to make deliberately
   once the above is solid, not a gap to rush.

None of this blocks the measurement/proof work in flight on the other lanes —
it's a parallel track. Note: all live-model work anywhere in this plan is
currently constrained to genuinely `:free`-tagged OpenRouter models — see
`proj_status.md`'s OpenRouter budget section before assigning anything that
calls a real model.
