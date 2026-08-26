# PROJECT STATUS — Verified Procedural Experience System

_Last updated: 2026-08-26 · by integrator ox-alpha · pull latest main before trusting this file._

---

## The one-paragraph version

We are building a **verified procedural memory substrate for AI agents**: raw agentic
traces become episodes → observations → claims → evidence-backed reusable procedures,
with applicability gating, Wilson-bound capability scores on founder-ratified routing
tiers, universal ChangeSet auditing, and bi-temporal invalidate-and-append semantics.
Three phases are formally CLOSED and reviewed (contracts / trust floor / trust
completion), hardening is landed, and the measurement instrument just produced its
first real-model result. Everything below is test-pinned: **backend suite 1168+ /
harness 163 / packaging 57 — all green at last integrated run.**

## Phase position

| Phase | State | Proof |
|---|---|---|
| Band 0 — contracts/spec reconciliation | ✅ CLOSED | D1/D4 ratified by founder; spec v4 + schema.md reconciled (`BAND0_DECISIONS.md`) |
| Band 1 — trust floor | ✅ CLOSED | `BAND1_CLOSURE_REVIEW.md` — migrations 21–25 engine-verified |
| Band 2 — trust completion | ✅ CLOSED | `BAND2_CLOSURE_REVIEW.md` — 9/9 items incl. OIDC identity, replay E2E |
| HARDENING | ✅ landed | H1 identity tables + tenancy predicate builder · H2 RLS backstop · H3 rate-limiter buffering (adoption sweeps remain, see board) |
| Band 3 — measurement | 🟡 ~65% | §40 harness + real-model arms BUILT; **Run #1 complete** (below); repeats + error-floor run + utility-in-routing remain |
| P — product | 🟡 ~45% | P1 `stealthlab-connect` ✅ · P2 status surface ✅ (`stealthlab-status-page`) · P3 founder acceptance ⬜ · P5 publication ⬜ |
| Bands 4/5 — scale/distribution | ⛔ gated | Activate only on public-launch signals |

## First real-model result (Run #1, 2026-08-26)

ox-alpha via OpenRouter · 95 billed calls · **$0.27 total** · 10 valid tasks:

| Arm | Resolved | False reuse | Stale refused |
|---|---|---|---|
| A solo frontier | 6/10 | 0 | — |
| B +ordinary memory | 7/10 | 1 | **0/6** |
| C +verified substrate | 7/10 | **0** | ✅ **6/6** |

Stale-refusal C vs B: McNemar p≈0.031 (at the k≥6 floor — repeats required before
public phrasing). Resolution B-vs-C not significant. Full log: `.scratch/build-board.md`
RUN #1 entry; raw data `experiments/harness/real_arms_results.jsonl`.

## Onboarding — 10 minutes

1. Read order: this file → `.scratch/build-board.md` (your lane + rules) →
   `ROADMAP.md` "Start here" → `BAND2_CLOSURE_REVIEW.md`.
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
:: real-model arms (from experiments\harness\)
..\..\backend\.venv\Scripts\python.exe run_real_arms.py --auto-resume --out <fresh-name>.jsonl --spend-log <fresh-name>.jsonl
```

## Who's who

- Founder: Anuj — owns D-rulings, acceptance tests, spend decisions.
- Lane agents: ox-alpha instances in worktrees (`sl-core-a/b`, `sl-measure/research/ship`).
- Integrator: reviews every landing, runs closure reviews, owns ROADMAP checkboxes.
