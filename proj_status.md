# PROJECT STATUS — Verified Procedural Experience System

_Last updated: 2026-08-27 · by integrator (Anuj, coordinating Claude Code lanes) ·
pull latest `main` before trusting this file._

---

## The one-paragraph version

We are building a **verified procedural memory substrate for AI agents**: raw agentic
traces become episodes → observations → claims → evidence-backed reusable procedures,
with applicability gating, Wilson-bound capability scores on founder-ratified routing
tiers, universal ChangeSet auditing, and bi-temporal invalidate-and-append semantics.
Three phases are formally CLOSED and reviewed (contracts / trust floor / trust
completion), hardening is landed, and this wave closed most of the gap between
"we have evidence this works" and "we have a shippable v0.1." **Backend suite: 1353
passed / 115 skipped / 0 failed. Harness suite: 241/241 green.**

## Phase position

| Phase | State | Proof |
|---|---|---|
| Band 0 — contracts/spec reconciliation | ✅ CLOSED | D1/D4 ratified by founder; spec v4 + schema.md reconciled (`BAND0_DECISIONS.md`) |
| Band 1 — trust floor | ✅ CLOSED | `BAND1_CLOSURE_REVIEW.md` — migrations 21–25 engine-verified |
| Band 2 — trust completion | ✅ CLOSED | `BAND2_CLOSURE_REVIEW.md` — 9/9 items incl. OIDC identity, replay E2E |
| HARDENING | ✅ landed | H1 identity tables + tenancy predicate builder · H2 RLS backstop · H3 rate-limiter buffering (adoption sweeps remain, see board) |
| Band 3 — measurement | 🟡 advancing | §40 harness + real-model arms BUILT; Run #1 complete; semantic-label extraction-quality track opened this wave (see below); repeats + error-floor run + utility-in-routing remain |
| P — product (v0.1 shippability) | 🟡 close | P1 `stealthlab-connect` ✅ · P2 status surface ✅ · **v0.1 ship checklist: 6/8 items done this wave (see below)** · P3 founder acceptance test ⬜ (blocked on the one real gap below) |
| Bands 4/5 — scale/distribution | ⛔ gated, correctly | Do not start before P3 passes on a real user workflow (ROADMAP rule) |

## v0.1 ship checklist snapshot (per `demo.md` §3 — the project's own bar)

| Item | Status |
|---|---|
| Offline suite green | ✅ 1353 passed / 115 skipped / 0 failed |
| `SECURITY.md` + `DATA_STATEMENT.md` published | ✅ |
| README quickstart works from a clean clone | ✅ |
| Doc accuracy (migration count, stale paths, stale product framing) | ✅ closed this wave (Band 6 hygiene sweep) |
| `docker-compose.yml` / `Dockerfile` exist | ✅ built this wave — **never boot-tested, no Docker installed anywhere in the fleet yet** |
| Migration chain verified on a disposable DB | ❌ blocked on Docker access |
| `bootstrap_demo.py` scripted two-phase story | ❌ not attempted this wave |
| **`check_procedure` / `WOULD_REFUSE` reachable through the MCP server** | ❌ **the one real gap — see below** |

## The one real gap right now

`demo.md`'s C5 capability ("refusal with receipts") is genuinely proven — a real
model, live, correctly refuses a stale procedure and cites the superseded claim
(`experiments/harness/model_decides.py`'s `refused_procedure_ids` mechanism,
independently verified). But repo-wide grep confirms `check_procedure` as a
literal callable tool does not exist in `backend/app/mcp_server/server.py` (8
tools live today, none named this). An agent using StealthLab today cannot
actually reach the refusal capability — only the harness can. This is the
current headline task (assigned to CORE-B, which owns the closest logic —
`precondition_gate.py` / `applicability.py` / `state.py`).

## OpenRouter budget — explicit limitation, read this before assigning live-model work

The founder's OpenRouter account has **no paid balance** — only a small
promotional grant, already spent. `ox-alpha`, the model this project's original
methodology was built around, is confirmed **absent from OpenRouter's current
catalog entirely** (not an access issue — the model itself appears gone).
Until a paid key or a working replacement exists, all live-model work must use
genuinely **`:free`-suffixed** OpenRouter models only, and stays inside the
free tier's rate limits (20/min, 50/day). MEASURE's semantic-label prompt-variant
results this wave (`combined` variant winning, F1 0.444 vs baseline 0.167) are
against `liquid/lfm-2.5-2.6b:free` as an ox-alpha stand-in — **directional
signal only, not validated on a production-representative model**, and not yet
mirrored into the live extraction prompt for exactly that reason.

## First real-model result (Run #1, 2026-08-26 — still the only live-arms run)

ox-alpha via OpenRouter (while it was still reachable) · 95 billed calls ·
$0.27 total · 10 valid tasks:

| Arm | Resolved | False reuse | Stale refused |
|---|---|---|---|
| A solo frontier | 6/10 | 0 | — |
| B +ordinary memory | 7/10 | 1 | **0/6** |
| C +verified substrate | 7/10 | **0** | ✅ **6/6** |

Stale-refusal C vs B: McNemar p≈0.031 (at the k≥6 floor — repeats required
before public phrasing). Full log: `.scratch/build-board.md` RUN #1 entry.

## Onboarding — 10 minutes

1. Read order: this file → `PRODUCTION_READINESS.md` (deploy/infra posture) →
   `.scratch/build-board.md` (your lane + rules) → `ROADMAP.md` "Start here" →
   `demo.md` (what actually ships).
2. Environment: `cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
   (z3/pandas/pyarrow declared). Copy `backend\.env` from an existing worktree if absent —
   it holds DATABASE_URL + OPENROUTER_API_KEY and is gitignored. NEVER commit keys.
3. Claim before you work; commit prefix = your lane tag; paste pytest output in every
   commit message; rebase onto origin/main before pushing; never force-push.
4. Proving tests ship in the same change as the code (Appendix C rows in ROADMAP.md).
5. Ambiguity → numbered blocking question in the board Log with proposed default;
   continue with another queue item meanwhile.

## Hard rules (violating any invalidates the work)

1. Fresh-start: NO backfills/migrations-for-legacy-data; legacy rows stay quarantined.
2. Nothing enters storage without scope + provenance (+ extractor version if derived).
3. Spec v4 / schema.md frozen — discrepancies go to board notes.
4. No edits outside your lane's granted paths. Cross-lane needs → board request.
5. Exit criteria are numbers. "Tests written" ≠ "tests run".

## Command quick reference

```cmd
:: backend suite (from backend\)
.venv\Scripts\python.exe -m pytest tests -q
:: harness suite (from experiments\harness\)
..\..\backend\.venv\Scripts\python.exe -m pytest tests -q
:: migrations (from backend\) — additive + idempotent
.venv\Scripts\python.exe scripts\migrate.py --status
:: status UI (from packaging\)
..\backend\.venv\Scripts\python.exe -m stealthlab_connect.status_entry   → http://127.0.0.1:8766/
:: real-model arms (from experiments\harness\) — FREE MODELS ONLY until budget resolves
..\..\backend\.venv\Scripts\python.exe run_real_arms.py --auto-resume --out <fresh-name>.jsonl --spend-log <fresh-name>.jsonl
```

## Who's who

- Founders: Anuj — owns D-rulings, acceptance tests, spend decisions. Chaitanya — co-founder.
- Lane agents: Claude Code instances in worktrees (`sl-core-a/b`, `sl-measure/research/ship`),
  coordinated by the integrator via `.scratch/build-board.md`.
- Integrator: reviews every landing, verifies git state independently (never trusts
  terminal output alone), runs closure reviews, owns ROADMAP checkboxes.
