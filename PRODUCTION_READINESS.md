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

**Install path — now real, and boot-tested clean.** `docker-compose.yml`,
`backend/Dockerfile`, `.dockerignore`, and a healthcheck script were added
earlier this arc (closing what was previously a documented-but-nonexistent
install path). The build context correctly points at the repo root, not
`backend/`, because `app/mcp_server/server.py` imports from the sibling
`experiments/swebench_pro/` at module import time — a naive
`context: ./backend` would have crash-looped, and it didn't.
**Correction to an earlier version of this document**, which stated "Docker
is not installed anywhere in this build" — that was wrong in a specific way:
Docker CLI and Compose were already present on the infra lane's machine, only
Desktop's Linux engine was stopped. Starting it took ten seconds. Once
running, `docker compose up -d` came up healthy with zero fixes needed to any
of the four files above, and the auth-enforcement contract was spot-checked
from the host (not just the container's own healthcheck): `POST /mcp` →
HTTP 401, the documented "serving and auth enforced" signal. **Caveat: this
result is reported by the infra lane (2026-08-27) and sits on an unpushed
local branch pending the founder's call on pushing to the public remote —
treat as strong, detailed evidence, not yet independently re-verified by the
integrator against `main`.**

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

**The one gap that actually blocks calling this "shippable"** is no longer
`check_procedure` wiring — that landed and is confirmed on `main`. It's now
`bootstrap_demo.py`: `demo.md`'s entire "what ships" narrative rests on this
script proving the two-phase story (traces → procedures, then a real
`WOULD_REFUSE`) in one command. Directly reading the script on `main` (82
lines) confirms it: zero references to `procedure`, `refus`, `check_procedure`,
or `WOULD_REFUSE` anywhere in it. It's the older debate/bottleneck-era seeder
— it inserts 10 traces shaped to trigger a *debate*, not the earned-memory
substrate. Run end-to-end (infra lane, 2026-08-27): exits 0, looks successful,
and leaves `episodes`, `observations`, `procedures`, and `evidence` all at
zero rows. This is not "blocked" or "not yet run" — **the script this
checklist item depends on does not implement what the checklist item
describes.** It needs to be written, not just executed. Also unexercised as a
result: Band 2's founding-loop exit criterion, on a fresh database.

---

## What's required (their own bar, from `demo.md` §3 — not an external standard)

| Requirement | Status |
|---|---|
| Full offline suite green | **Met** — 1368 passed / 115 skipped / 0 failed |
| Migration chain applied clean on a throwaway `pgvector/pg15` container | **Reported met** — 30/30 applied, idempotency confirmed (infra lane, unpushed, pending verification) |
| `docker-compose` setup for `docker compose up -d` | **Reported met** — boot-tested clean, zero fixes (infra lane, unpushed, pending verification) |
| `bootstrap_demo.py` scripted two-phase story proven on the release commit | **Unmet — script doesn't implement the story** (see above; needs to be written) |
| `check_procedure` refusal payload shape pinned by tests | **Met** — real tool on `main`, tested against the actual server |
| `SECURITY.md` + plain-language data statement published | **Met** |
| README quickstart works from a clean clone | **Met** — verified by SHIP against current commit |

Since the last version of this document: `check_procedure` wiring closed for
real, and the Docker-dependent items came back positive (pending push +
independent verification). But this pass also demoted `bootstrap_demo.py`
from "untried" to "known incomplete" — a more serious finding than what it
replaced, since it's the thing the whole v0.1 story is supposed to prove in
one command.

---

## What's planned (documented elsewhere in the repo, not speculation)

- Real OIDC auth flip-on, once an identity provider is actually configured
- Redis-backed rate limiting, with the trigger condition (>1 worker) already
  written into the code as a tripwire
- Crypto-shredded deletion (Band 5, needs a founder ruling — D4)
- Capability-based auto-routing thresholds (Band 1.9b, needs founder ruling D1)
- Bands 4/5 generally — correctly not started; ROADMAP's own rule is that no
  Band 4 item starts before P3's acceptance test has passed on a real user's
  workflow, and P3 is still blocked on `bootstrap_demo.py` above

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
- **The real `bootstrap_demo.py` two-phase story.** See above — this needs
  to be written, not run.

**Flagged this wave, not yet fixed** (found by the infra lane, outside its
scoped file grant, so correctly left for someone with the right ownership):
`CLAUDE.md` asserts `*.mov` is gitignored — it isn't. A 56MB
`FounderVideo.mov` currently sits untracked at repo root, in what is a
**public** GitHub repo. It hasn't been committed yet, but a broad `git add`
would sweep it in. Worth fixing the `.gitignore` (or moving the file
somewhere outside the tracked tree entirely) before anyone runs a wide `git
add` in that checkout.

---

## Overall plan, in the order I'd actually do it

1. **Write the real `bootstrap_demo.py` two-phase story.** This is now the
   single item that most blocks calling v0.1 shippable — everything else on
   this list is either done or infra. Ingest traces → run the real
   extraction pipeline → produce procedures → retrieve precedent →
   deliberately break a precondition claim → call `check_procedure` and get
   a real `WOULD_REFUSE`, all in one command, on a fresh database.
2. **Fix the `.gitignore` `*.mov` gap** before any wide `git add` happens in
   a checkout that has `FounderVideo.mov` sitting in it.
3. **Push and independently re-verify the infra lane's Docker results**
   (compose boot, migration chain) once the founder decides how the infra
   lane's work should reach the public remote.
4. **Add baseline error tracking** (Sentry's free tier is enough) on the
   hosted instance, so failures are visible instead of silent.
5. **Confirm the Render Postgres retention story explicitly**, in the
   dashboard, before treating anything stored there as durable.
6. **Only after 1–5:** revisit turning on real auth/multi-tenant mode. It's
   correctly gated off right now — that's a decision to make deliberately
   once the above is solid, not a gap to rush.

None of this blocks the measurement/proof work in flight on the other lanes —
it's a parallel track. Note: all live-model work anywhere in this plan is
currently constrained to genuinely `:free`-tagged OpenRouter models — see
`proj_status.md`'s OpenRouter budget section before assigning anything that
calls a real model.
