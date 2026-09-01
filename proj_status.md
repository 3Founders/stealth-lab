# PROJECT STATUS — Verified Procedural Experience System

_Last updated: 2026-09-01 · doc-reconciliation pass (Claude Code), verified
directly against source/tests, not inferred from commit messages · pull
latest `main` before trusting this file._

## 2026-09-01 doc-reconciliation update — what changed since the last entry

Re-verified against the actual repo (not commit messages) on 2026-09-01:

- **Backend offline suite: 1981 passed / 252 skipped / 0 failed** (`cd backend
  && python -m pytest tests -q`, `DATABASE_URL` unset) — up from the
  2026-08-27 entry's 1368/115. The growth tracks the Backend Master Build
  REST API phases, the Implementation Registry, and the Ideal V1 historical-
  bootstrap/claim-reasoning/publish-lifecycle work landed since (see `git log
  --oneline -40`).
- **Harness suite: 254 passed / 0 skipped** (`experiments/harness`, was
  reported as 241/241). **Packaging suite: 95 passed** (not previously
  recorded here).
- **MCP tool surface: 20 registered tools**, not 9 — verified by running
  `packaging/tests/test_server_offline.py::test_all_registered_tools`
  (passes) and independently by listing `server._tool_manager.list_tools()`
  after import. New since the last count: `check_applicability`,
  `decide_procedure`, `get_procedure`, `report_execution`,
  `reproduce_procedure`, `search_procedures`, `submit_procedure`, and a new
  Implementation Registry group — `get_implementation_capability`,
  `inspect_implementation`, `list_task_implementations`,
  `resolve_implementation`. `README.md` and `commLLM.md` (both named "live"
  in `CLAUDE.md`'s doc map) still said 9; corrected there.
- **A read-only REST API layer now exists and is wired**, confirmed by
  reading `backend/app/main.py`'s `include_router` calls and each router
  file: `/v1/claims`, `/v1/procedures`, `/v1/solutions`,
  `/v1/repositories`, `/v1/projects`, `/v1/tasks`, `/v1/me`, `/v1/search`
  (Backend Master Build Wave 1), plus `/v1/implementations` (Implementation
  Registry, migration 33). None of the five live docs mentioned this layer
  before this update; `README.md` and `commLLM.md` now do.
- **Migrations: 33 files** in `backend/db/` (`01_ontology.sql` …
  `33_implementation_registry.sql`), up from the 30 the 2026-08-27 entry's
  "30/30 applied" referred to. This is a file-count verification only — no
  Postgres instance was available in this pass to re-run
  `scripts/migrate.py --status` against a live DB, so "applies cleanly" is
  NOT re-confirmed for migrations 31–33; only their presence and content are.
- **The "one real gap" below (`bootstrap_demo.py`) is stale.** Direct read of
  `backend/scripts/bootstrap_demo.py` on `main` today shows 301 lines (not
  82) that do reference `procedure`, `extract_procedure`,
  `check_procedure_reuse`, and `WOULD_REFUSE`, with real two-phase logic:
  Phase A extracts a real procedure via `extract_procedure()`, Phase B
  invalidates one derived precondition and shows the ALLOW → WOULD_REFUSE
  flip. This was landed by commit `694a2aa` ("core-b: bootstrap_demo.py real
  two-phase story (demo.md §3), extract_procedure V0-gate fix"), which is an
  ancestor of current `main`. Offline coverage exists:
  `backend/tests/test_bootstrap_demo_offline.py`,
  `test_check_procedure_reuse_offline.py`,
  `test_retrieve_precedent_procedures_offline.py`. `demo.md` §3 already
  reflects this (its `[x]` row for this item, dated 2026-08-27); this file
  had not caught up. Not independently re-run against a live DB in this
  pass (no Postgres instance available) — the source-level claim is
  verified, the live-DB run is not.

Everything below this point is the 2026-08-27 entry; the phase table, ship
checklist, and "one real gap" section have been edited in place to match the
verified facts above (each edit says what it replaced and why). Sections not
touched (OpenRouter budget, Run #1 result, onboarding, hard rules, commands,
hygiene flag, who's-who) were not re-verified this pass and may also have
drifted.

---

## The one-paragraph version

We are building a **verified procedural memory substrate for AI agents**: raw agentic
traces become episodes → observations → claims → evidence-backed reusable procedures,
with applicability gating, Wilson-bound capability scores on founder-ratified routing
tiers, universal ChangeSet auditing, and bi-temporal invalidate-and-append semantics.
Three phases are formally CLOSED and reviewed (contracts / trust floor / trust
completion), hardening is landed, `check_procedure` is now a real callable
tool, and the Docker-dependent infra items came back positive (pending
push). One gap remains between "we have evidence this works" and "we have a
shippable v0.1" — see below. **Backend suite: 1368 passed / 115 skipped /
0 failed. Harness suite: 241/241 green.** (Superseded by the 2026-09-01
update above — real current counts are 1981/252/0 and 254/0.)

## Phase position

| Phase | State | Proof |
|---|---|---|
| Band 0 — contracts/spec reconciliation | ✅ CLOSED | D1/D4 ratified by founder; spec v4 + schema.md reconciled (`BAND0_DECISIONS.md`) |
| Band 1 — trust floor | ✅ CLOSED | `BAND1_CLOSURE_REVIEW.md` — migrations 21–25 engine-verified |
| Band 2 — trust completion | ✅ CLOSED | `BAND2_CLOSURE_REVIEW.md` — 9/9 items incl. OIDC identity, replay E2E |
| HARDENING | ✅ landed | H1 identity tables + tenancy predicate builder · H2 RLS backstop · H3 rate-limiter buffering (adoption sweeps remain, see board) |
| Band 3 — measurement | 🟡 advancing | §40 harness + real-model arms BUILT; Run #1 complete; semantic-label extraction-quality track opened this wave (see below); repeats + error-floor run + utility-in-routing remain |
| P — product (v0.1 shippability) | 🟡 close | P1 `stealthlab-connect` ✅ · P2 status surface ✅ · **v0.1 ship checklist: 8/8 items ✅/reported (see 2026-09-01 note below — `bootstrap_demo.py` closed)** · P3 founder acceptance test ⬜ (not itself re-run this pass; see below) |
| Bands 4/5 — scale/distribution | ⛔ gated, correctly | Do not start before P3 passes on a real user workflow (ROADMAP rule) |

## v0.1 ship checklist snapshot (per `demo.md` §3 — the project's own bar)

| Item | Status |
|---|---|
| Offline suite green | ✅ 1981 passed / 252 skipped / 0 failed (re-verified 2026-09-01; see update at top) |
| `SECURITY.md` + `DATA_STATEMENT.md` published | ✅ |
| README quickstart works from a clean clone | ✅ |
| Doc accuracy (migration count, stale paths, stale product framing) | 🟡 this pass fixed the MCP tool-count and REST-layer gaps in `README.md`/`commLLM.md`; not a full re-sweep |
| **`check_procedure` / `WOULD_REFUSE` reachable through the MCP server** | ✅ closed — real tool, tested against the actual server (now one of 20 registered tools, see update at top) |
| `docker-compose.yml` boots clean | ✅ **reported** by the infra lane (zero fixes needed) — status not independently re-checked this pass |
| Migration chain verified on a disposable DB | 🟡 33 migration files present and read (up from 30); **not** re-run against a live DB this pass (no Postgres instance available) — the prior "30/30 applied" report is not re-verified for 31–33 |
| `bootstrap_demo.py` scripted two-phase story | ✅ **closed** — see below; this row was wrong in the 2026-08-27 entry |

## The one real gap right now (superseded — see 2026-09-01 update at top)

Historical note, not current: as of 2026-08-27 this section named
`bootstrap_demo.py` as the one real gap, describing an 82-line script with no
procedure/refusal logic. That is no longer true. `backend/scripts/
bootstrap_demo.py` on `main` today is 301 lines and does implement the
two-phase story (real `extract_procedure()` call, real precondition
invalidation, real ALLOW → WOULD_REFUSE flip via `check_procedure_reuse()`),
landed by commit `694a2aa`, with offline test coverage
(`test_bootstrap_demo_offline.py` and two related files). `demo.md` §3's
checklist already reflects this as closed (dated 2026-08-27); this file had
simply not been updated to match. No new "one real gap" has been identified
in this pass — the closest open item is P3 (founder acceptance test on a
real user workflow), which was not itself re-run here.

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

## Known repo hygiene flag — read before a wide `git add`

The infra lane found (2026-08-27, flagged not fixed — outside its file grant):
`CLAUDE.md` claims `*.mov` is gitignored; it isn't. A 56MB `FounderVideo.mov`
sits untracked at repo root in what is a **public** GitHub repo. Not yet
committed, but `git add -A` would sweep it in. Fix the `.gitignore` (or move
the file out of the tracked tree) before that happens.

## Who's who

- Founders: Anuj — owns D-rulings, acceptance tests, spend decisions. Chaitanya — co-founder.
- Lane agents: Claude Code instances in worktrees (`sl-core-a/b`, `sl-measure/research/ship`),
  coordinated by the integrator via `.scratch/build-board.md`. `lane/infra`
  (Chaitanya, 2026-08-27) is the newest — Docker-equipped, took the
  compose-boot/migration-chain items that had been blocked since Band 1.
- Integrator: reviews every landing, verifies git state independently (never trusts
  terminal output alone), runs closure reviews, owns ROADMAP checkboxes.
