# StealthLab — FINAL V1 FREEZE REPORT

| | |
|---|---|
| Historical baseline | `a5dace6ccbe52c7669e13fa0efe8eb17448d05a8` — annotated tag `v1-baseline-2026-09-02` (kept, untouched, historical provenance) |
| Final V1 commit | **this commit** on `main` (the one that adds this report). `git rev-parse HEAD` / `git rev-parse v1-final-2026-09-03^{commit}` agree. |
| Final V1 tag | `v1-final-2026-09-03` (annotated, immutable) |
| Branch integrated | `core-a/ingestion-testing` |
| Merge method | **fast-forward** (`git merge --ff-only`). `main` history not rewritten, not squashed, not force-pushed. |
| Migration level | 01–37 (last: `37_execution_runs_terminal_chk_fix.sql`) |
| Date | 2026-09-03 |

---

## 1 · Merge

- `origin/main` before merge: `a5dace6` (= `v1-baseline-2026-09-02`).
- Local `main` before merge (`MAIN_BEFORE_SHA`): `92a6eee` — the
  claim-graph-viewer commit, which is an **ancestor** of the hardening
  head, not a divergence. `git pull --ff-only` reported "Already up to
  date" against `origin/main`.
- Verified before merging: `a5dace6` and `92a6eee` are both ancestors of
  the hardening head; `git log --merges a5dace6..<head>` is **empty**
  (linear history, no merge commits) → a fast-forward is valid and
  loses no history.
- `git merge --ff-only origin/core-a/ingestion-testing` advanced `main`
  from `92a6eee` to the hardening head. (First attempt hit a transient
  `index.lock` from a concurrent process and made no change; the retry
  succeeded.) Working tree clean afterwards.
- Two freeze-only commits were then made **on `main`** (line-ending fix +
  ledger re-baseline tool, see §7) followed by this report.

## 2 · All commits included (`a5dace6..HEAD`) — 36 commits

Newest first. 35 `core-a:` + 1 `frontend:` (`39e2892`). `main` = Final V1.

```
54b0635 core-a: one-time schema_migrations ledger re-baseline tool + run it
6897c8f core-a: migration checksums are line-ending-agnostic + pin *.sql to LF
5763512 core-a: freeze pass -- acceptance matrix all-CLOSED + A/B/C/D limitations register
a404593 core-a: freeze prep -- dead imports, doc tool-count, untrack build logs
2fae92c core-a: Final-V1 scripted browser E2E + wire the Problems list page (freeze §3)
4208b87 core-a: rewrite acceptance matrix + final V1 production-readiness report (§13/§14)
9721c84 core-a: packaging tool-list snapshot follows the Final-V1 MCP tools (§11)
dfb793e core-a: performance sanity baseline (§5)
885d83a core-a: migration upgrade-path e2e (§4)
a8bd940 core-a: retry/resume REST + MCP surface over the durable-run service (§2)
44c942c core-a: documentation reflects the shipped Final-V1 system (§6)
39e2892 frontend: V1 benchmark surface -- problem hero, backend-ranked leaderboard, evaluations, WebMCP, auth, submit
b966cd7 core-a: execution descriptor + durable-run wired into the real tier-2 path (§1/§3)
892f60c core-a: acceptance matrix -- full offline regression gate CLOSED
320918a core-a: final-V1 hardening acceptance matrix (§61)
0f17055 core-a: pin product-model MCP e2e queries to the run tag (test isolation)
002e6da core-a: durable execution-graph retry / resume (§24-§27, §58 E2E)
9040dfa core-a: product-model MCP tools (§37) -- REST and MCP converge on one service
3229641 core-a: product-model service + REST + comparability + evidence-derived ranking
ed958b2 core-a: corpus wave Phase 10 -- retrieval + claim-graph proof + final report
8aecc04 core-a: migration 35 -- Problem/Benchmark/Solution/Evaluation product model
47f4ffd core-a: ingestion treats untrusted documents as data (§29)
209564a core-a: reconstruct ChatGPT export branch tree (§28)
53c93f9 core-a: final-v1 hardening -- §0 current-state audit
bbf6ff0 core-a: fix claim-graph overview e2e -- KeyError 'relation' on similarity edges
7968efe core-a: corpus wave Phase 9 -- canonical ingestion into Supabase
08499bc core-a: corpus wave Phase 8 -- admission ranking (29 admitted / 21 held)
612dd77 core-a: corpus wave Phase 7 COMPLETE -- candidate procedures S31-S50
2a64c7a core-a: corpus wave Phase 7 -- candidate procedures S21-S30
a1c0c65 core-a: corpus wave -- static claim-graph preview
52301c3 core-a: corpus wave Phase 7 -- candidate procedures S01-S20
f76702c core-a: corpus wave Phase 6 -- 50-source registry resolved (49/50, gate PASS)
08c5931 core-a: corpus wave -- Supabase e2e re-verified post clock-fix
3bfe777 core-a: corpus wave -- Supabase substrate stood up + Phase 3 readiness
5b2fc15 core-a: better-ways corpus Phase 1 -- source resolution + one-procedure gate
92a6eee core-a: claim-graph viewer rebuilt on force-graph + embedding-similarity edges
```

## 3 · Frontend status — CLOSED

`frontendv1/` (Next.js 16 App Router, tracked): benchmark-first surface
over the `/v1` REST API — Problem page (backend-ranked leaderboard +
current-best / tie / open hero + evaluations), Search, Procedure / Task /
Solution / Evidence / Implementation / Repository / Project / Personal /
Claim pages, OIDC + dev-viewer auth, submit. All ranking comes from the
backend. The stale Problems **list** stub was wired to `GET /v1/problems`
in the freeze pass (`2fae92c`).

**Scripted browser E2E — `2fae92c`, `frontendv1/e2e/v1-flow.spec.ts`
(Playwright, 7 tests, re-run GREEN on `main`):** real Next frontend
against the real FastAPI backend + real Supabase, no mock server. Proves:
app loads + `/health`; viewer identity (`X-Viewer-Id`) attached to `/v1`
requests; Problems list from `GET /v1/problems`; Problem detail renders
the benchmark name, "2 candidate solutions", the leaderboard `<table>` +
the backend Wilson-LB copy, and the backend-derived "Current best
verified" hero (93.3% verified / n=30); Evaluation detail shows the
backend-recomputed n and verified rate; full home→problems→problem→
evaluation click-path; **every `/v1` request is asserted to stay on the
configured backend origin and to match no `mock|fixture|stub|fake`.**
Seed: `frontendv1/e2e/seed_v1_flow.py` (real `product_model` write paths;
`problem_leaderboard.current_best == [Solution A]`). `npx tsc --noEmit`
clean; `npx next build` compiles all 19 routes.

`npm run lint` (bare `eslint`) crashes inside ESLint 9 / `@eslint/eslintrc`
on the flat-config + legacy `extends` mix — pre-existing tooling defect,
not a regression; `tsc` is the real type gate.

## 4 · WebMCP status — CLOSED

`frontendv1/src/webmcp/` + `/webmcp` status page. `registerWebMcpTools()`
feature-detects `document.modelContext` and registers **13 semantic
tools** over the domain API; inert where the API is absent. The browser
E2E asserts `/webmcp` feature-detects and renders all 13 tool names.

## 5 · Security status — CLEAR (pre-merge audit, `.scratch/final-v1-premerge-audit.md`)

- **Execution descriptor** secret-free: `_sanitize_auth`
  (`implementation_registry.py:449,453`) reduces an inline secret to
  `{"redacted": true}`; `auth_requirements` carries credential references
  only. `test_implementation_descriptor_offline.py` (7).
- **Cross-user run mutation**: `authorize_run_mutation`
  (`durable_resume.py:121`) raises `NotYourRun` iff both the resolved
  caller identity and `execution_runs.created_by` are known and differ →
  REST 403 (`runs.py:69,90`), MCP `REFUSED` (`server.py`).
  `test_durable_resume_e2e.py`. Reads are open (accepted V1 posture,
  §10-B).
- **Private Problems / Benchmarks / Evaluations**: `product_model` read
  paths thread `scope_predicates()` / gate on `get_problem(scope)`;
  `problems` carries `owner_id` / `visibility` / `scope_type`.
  Re-verified post-upgrade by `test_migration_upgrade_e2e.py`.
- **§28** abandoned ChatGPT branches contribute no verified evidence
  (`chat_history_import.py:168-206`; 10 `test_s28_*`).
- **§29** untrusted documents are data, fail-closed, `capability_statement`
  is metadata-only (grep-confirmed consumed only by
  `semantic_projections.py` + `replay.py`). 4 named tests.
- No `.env` / service-role key / token in the diff (`a5dace6..HEAD`
  scanned; 0 hits). `frontendv1/.env.local.example` is placeholders only.
- **Carried, unchanged from baseline (founder review, not a blocker):**
  `apply_change_set` is an ungated raw write primitive present in the
  public MCP registry — CLAUDE.md's "opt-in flag, not public" posture is
  not enforced by a flag today. Approval + audit still come from
  `submit_approval` / `decide_decomposition`.

## 6 · Retry / resume status — CLOSED

Durable execution runs (`execution_runs` + `execution_run_nodes`, mig
36/37): per-node state, `attempt_count` / `max_attempts`, implementation
pinned at first resolve, `side_effecting` parking, `trg_ern_terminal_fence`.
`run_graph_durably` (`durable_graph.py`) is the one bridge; MCP
`find_best_way` tier-2 and `reproduce_procedure` call it (not the
in-memory executor); `_respond_tier1_hit` stays in-memory by design (the
now-unused imports were removed in `a404593`). REST `/v1/runs/{id}` +
`/nodes` + `/resume` + `/nodes/{order}/retry`; MCP `inspect_run` /
`resume_execution_run` / `retry_run_node`. A coding-agent run returns
`needs_product_context`, never a fake resume. `_finalize` appends exactly
one immutable `executions` row. Proof: `test_durable_run_offline.py` (12),
`test_durable_run_e2e.py` (3), `test_durable_graph_e2e.py` (1),
`test_durable_resume_offline.py` (11), `test_durable_resume_e2e.py` (3).

## 7 · Migration status — CLEAN

- `python backend/scripts/migrate.py --status` against the acceptance
  Supabase: **37 applied, no MISMATCH, no pending.**
- Migrations 35/36/37 are additive + idempotent; 37 is a one-directional
  CHECK relax (no data migration). `git grep 'DROP TABLE|DROP
  COLUMN|TRUNCATE'` over 35/36/37 is empty. 01–37 contiguous, each header
  states the next free number, none rewritten.
- `test_migration_upgrade_e2e.py` (1 passed): throwaway PG17 cluster,
  apply 01–34, write a pre-hardening dataset through the **real** write
  paths, apply 35/36/37 via the **real** `migrate.py`, then assert every
  pre-existing row byte-for-byte unchanged + scope still enforced + the
  new product-model and durable-run tables work against the pre-existing
  rows + no destructive DDL.
- **Freeze fix (`6897c8f`, `54b0635`):** on a Windows checkout with
  `core.autocrlf=true`, `git checkout` rewrites `db/*.sql` with CRLF, and
  `migrate.py` hashed raw bytes → spurious `MISMATCH` against a ledger
  populated from an LF checkout. Fixed at two layers: `.gitattributes`
  pins `*.sql` (and `migrate.py`) to `eol=lf` so the frozen tag always
  checks out LF, and `migrate.py._checksum` now normalises CRLF/lone-CR
  to LF before sha256 (a migration's meaning is its text, not its line
  endings). The acceptance Supabase ledger — a long-lived shared dev DB —
  had 20 rows (mig 11–34) recorded from CRLF checkouts; a one-time,
  idempotent `backend/scripts/rebaseline_migration_ledger.py` rewrote
  those to the LF-normalised checksum (20 rewritten / 17 already
  normalised / 0 orphans). `--status` is clean and stays clean on any OS.

## 8 · Performance sanity result — GO

`.scratch/final-v1-perf-sanity.md` / `.scratch/perf_results.json` (probe
re-run this pass, N=20/path vs live Supabase, 0 failures):

| Path | p50 ms | p95 ms | DB queries |
|---|---:|---:|---:|
| `problem_leaderboard` | 150 | 374 | **8 (fixed — no N+1)** |
| `durable_run.start_run` (3-node) | 133 | 361 | 7 |
| `mcp.inspect_problem` | 283 | 744 | 16 |
| `claim_graph_api.get_claim_graph_overview` (with_status) | 394 | 952 | 98 (bounded, `limit ≤ 600`, semaphore 8) |
| `embeddings.embed_one` (distinct text) | 3431 | 5599 | external API |

Every median ≤ the earlier baseline; query counts identical. Leaderboard
N+1 check: none (data-independent). All retry/drive loops bounded; no
repeated embed. **GO.**

## 9 · Complete regression results

`.scratch/final-v1-regression-results.md`. Freeze run 2026-09-03, then
re-run **on `main`** after the fast-forward:

| Suite | branch `2fae92c` | re-run on `main` |
|---|---|---|
| Backend offline (`DATABASE_URL` unset) | 2118 passed / 287 skipped / 0 failed | **2118 passed / 287 skipped / 0 failed** |
| Backend live E2E vs Supabase (8 scenarios) | 12 passed | **12 passed** |
| Harness | 254 passed | **254 passed** |
| Packaging | 95 passed | **95 passed** |
| Frontend `tsc --noEmit` | clean | **clean** (+ `next build` all 19 routes) |
| Frontend browser E2E (Playwright) | 7 passed | **7 passed** |
| Migration `--status` (acceptance Supabase) | clean | **clean after ledger re-baseline** |

**Total: 2486 passed / 0 failed / 287 skipped**, exit 0 everywhere. Zero
unexplained conditions. The only non-green signal anywhere is the
pre-existing `frontendv1` `npm run lint` ESLint-config crash (not a
regression; `tsc` is clean).

## 10 · Accepted V1 limitations (not blockers)

Full text: `docs/final-v1.md` §"FREEZE PASS — LIMITATIONS REGISTER" · B.

1. `find_best_way` tier-2 resume rebuilds the sandbox per invocation
   (durable at the graph layer; a resumed run's earlier file edits ride
   forward as agent *context*, not mechanically re-applied).
2. A coding-agent run does not resume headless — the durable-run tools
   return `needs_product_context`; resume via
   `find_best_way(resume_run_id=…)`.
3. Execution-run **reads** are unauthenticated (mutations are
   creator-gated; consistent with every other V1 read surface).
4. `test_ingestion_admin_endpoint_e2e` pre-existing red — trace →
   observation → claim drain, not the corpus/product path.
5. `apply_change_set` ungated + present in the public MCP registry
   (unchanged from baseline; founder review).
6. MCP Tasks-extension backing store is in-memory (`--workers 1`
   load-bearing) — the durable execution *run* is Postgres-durable, the
   MCP Tasks *envelope* is not.
7. `get_claim_graph_overview(with_status=True)` ~4.5 queries/node
   (bounded, flat fast-path exists).
8. `frontendv1` `npm run lint` ESLint-config crash (pre-existing tooling;
   `tsc` is the real gate).

## 11 · Post-freeze evaluation / feature work (explicitly deferred)

Full text: `docs/final-v1.md` §"FREEZE PASS" · C and D.

**Unmeasured evaluation questions:** real baseline-vs-Stealth live
results; real ROI / break-even; full embedding-level retrieval quality
(`problems` / `procedures` have no vector column — `find_problem` is
lexical `ts_rank`); large-scale capacity (p95 latency, leaderboard
compute-on-read at 10²–10³ evaluations, durable-run throughput); live LLM
extraction quality; comparability / Wilson-vs-raw / `MIN_RUNS_FOR_RANKING`
ablations.

**Post-V1 features:** richer generalization/transfer semantics; more
execution providers + headless coding-agent resume + a persistent MCP
Tasks store; owner column + authenticated reads on `execution_runs`;
full convergence of `find_best_way` (HTN) and `find_best_solution`
(leaderboard); set-based claim-lifecycle query; `schema.md` refresh
(frozen — needs a formal unfreeze); internet-scale corpus admission.

## 12 · Exact reproduction commands

```bash
git checkout v1-final-2026-09-03

# backend offline (DATABASE_URL unset)
cd backend && python -m pytest tests -q -p no:cacheprovider

# backend live E2E (Supabase DSN in backend/.env)
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs) && \
  python -m pytest tests/test_product_model_e2e.py tests/test_product_model_mcp_e2e.py \
    tests/test_durable_run_e2e.py tests/test_durable_graph_e2e.py tests/test_durable_resume_e2e.py \
    tests/test_claim_graph_overview_e2e.py tests/test_claim_graph_mcp_e2e.py \
    tests/test_migration_upgrade_e2e.py -q -p no:cacheprovider

# harness / packaging
cd experiments/harness && python -m pytest tests -q -p no:cacheprovider
cd packaging && python -m pytest tests -q -p no:cacheprovider

# migrations
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs) && python scripts/migrate.py --status

# frontend
cd frontendv1 && npx tsc --noEmit -p tsconfig.json && npx next build
cd backend && export $(grep -E '^DATABASE_URL=' .env | xargs) && python ../frontendv1/e2e/seed_v1_flow.py
cd frontendv1 && NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npx playwright test
```

## 13 · Repository state confirmation

- `main` HEAD == this commit == `v1-final-2026-09-03^{commit}`.
- `git tag --points-at HEAD` includes `v1-final-2026-09-03`.
- `v1-baseline-2026-09-02` still resolves to `a5dace6` — untouched.
- `git status --short` on `main`: clean of freeze work (only pre-existing
  cross-lane noise — `TEST.md`, egg-info, `backend.zip`, `prompts.md`,
  scratch notes, hand-run `check_*.py` probes — none authored or staged
  by this freeze).
- `git log --merges a5dace6..HEAD` empty — `main` is `a5dace6` plus a
  linear fast-forward. No force-push. No history rewrite. No squash of
  the hardening history.
