# TESTING_GUIDE_V0.1.md — full run, 2026-08-29

**Lane:** infra · **Host:** Windows 11, Docker Desktop 29.7.2, Python 3.13
**DB:** dev `localhost:5432/postgres` (Tier 1) + fresh compose volume (Tier 2)
Counts read fresh per the guide's own instruction; nothing hardcoded.

## Scorecard

| # | Module | Verdict | Evidence |
|---|---|---|---|
| 1 | Backend offline suite | **PASS** | `1505 passed, 115 skipped, 0 failed` in 232s |
| 2 | Database & migrations | **PASS** | 31 pending → applied; `--status` clean. Also auto-applied 01–31 on a fresh compose volume |
| 3 | Live integration checks | **PARTIAL — 2 real defects** | see below |
| 4 | MCP server, 9 tools | **PASS** | live `tools/list` returned exactly 9; unauth 401, auth 200 |
| 5 | Trace ingestion | **PASS** | 4,341 records, `trace_events` → 5,731, `observations` → 3,106; re-run idempotent |
| 6 | Episode assembly | **PASS** | 1,187 episodes; replay contract `0 inserted / 106 skipped`; privacy + double-encode both verified |
| 7a | observation → claim | **PASS (with a wiring gap)** | chain proven; see founding-loop doc |
| 7b | claim → procedure | **PASS** | procedure `01a04c6a-…` extracted from a real episode |
| 8 | Governance & rate limiting | **PASS** | all checks incl. concurrency, ledger independence, cost caps |
| 9 | Redaction & secrets | **PASS** | 26 offline tests + full-history credential scan, below |
| 10 | Observability (Sentry) | **PASS (off-by-default half)** | `init('test')` → `False`. Live half not run — no Sentry project |
| 11 | Sandbox execution | **PARTIAL — new finding** | offline 30 passed; isolation unavailable in BOTH environments, below |
| 12 | Core loop, real model | **NOT RUN — needs founder call on spend** | see below |
| 13 | Docker Compose boot | **PASS** | fresh volume + `--build`, all of the guide's checks |
| 14 | Frontend | **PASS** | fresh `npm install`, `next` present, `Compiled successfully in 75s`, real type-check, 10/10 static pages, all 8 routes |
| 15 | Statistical validation | **NOT RUN — needs founder call on spend** | guide says do not gate a release on it |

Also run (not guide modules): harness suite **254 passed**, packaging suite **95 passed**.

**Module 14 detail** (run genuinely fresh — `node_modules` and `package-lock.json`
deleted first, real network, no sandboxed partial build):
```
✓ Compiled successfully in 75s
  Linting and checking validity of types ...
✓ Generating static pages (10/10)
Route (app):  / · /_not-found · /approvals · /approvals/[id] · /archive ·
              /tasks · /tasks/medical-report-extraction · /visualize · /workbench
First Load JS shared by all: 103 kB
```
All 8 routes the guide's updated list names are present and built.

---

## Module 9 — secrets audit, in full

The guide asks for a systematic sweep, so this went past the three commands listed.

**Scanned:** all 3,983 objects reachable from **every** ref including local-only
`refs/cline/checkpoints/*` (103 checkpoint commits), against 10 credential patterns
(AWS, Anthropic, OpenRouter, Voyage, OpenAI, GitHub, Google, Slack, PG DSN passwords,
PEM private keys), with placeholder filtering.

**Result: no real credential in any reachable object.** Every hit is a documented
test fixture, confirmed by reading the surrounding source:

- `AKIAABCDEFGHIJKLMNOP` → `test_known_token_in_a_nested_event_is_redacted()`
- `ghp_aaaa…` / `ghp_bbbb…` → a README smoke-test command for `stealthlab-trace-hook`
- the only DSN password found is `stealthlab`, the local docker-compose dev default

Also confirmed:
- `git stash list` — **empty**. The credential leak found earlier this session is gone.
- A real `.env` was **never** added in any commit on any ref (`--diff-filter=A` over
  `.env`, `*/.env`, `backend/.env` → no results).
- `backend/.env` is ignored right now (`.gitignore:35`).
- `FundingGrants/` has **never** been tracked on any ref.
- The 103 `cline checkpoint` commits live only on `refs/cline/checkpoints/*` and are
  **not** ancestors of `origin/main` (verified with `merge-base --is-ancestor`). They
  are local-only and contain no real secret, so no rotation is indicated.
- `origin/fundraising-strategy` **is** a pushed branch (1,169 files) but contains no
  `FundingGrants/` path and no `.env` — only `.env.example` files. Clean, but worth a
  founder glance since the branch name implies otherwise.

**Verdict: PASS.** Nothing to rotate from this sweep.

---

## Module 11 — NEW FINDING: sandbox isolation is unavailable in both environments

`tests/test_sandbox_executor.py` + `test_container_sandbox_offline.py`: **30 passed.**

`integration_check_v2_sandbox.py`: **6 FAILED**, in *both* environments, same cause.

- **On the Windows host:** `sandbox isolation mechanism unavailable: [WinError 2]` —
  `unshare` is a Linux kernel primitive and does not exist. Expected.
- **Inside the shipped backend container:** `unshare` **is** installed
  (`/usr/bin/unshare`) but running it fails:
  ```
  unshare: unshare failed: Operation not permitted
  ```
  The container has no `CAP_SYS_ADMIN`, so it cannot create the namespaces
  `app/services/sandbox.py` depends on.

Failing checks: real code executes · network blocked · parent secret does not leak ·
wall-clock timeout · `/etc/passwd` unreachable · `decide_agent` runnable=True.
(CPU and memory limits pass — those use `resource.RLIMIT_*`, not namespaces.)

**The good news, and it is the important half:** it **fails closed**. The log says
`sandbox isolation mechanism unavailable for agent … -- failing closed, not falling
back to unsandboxed execution`. No untrusted code runs unisolated. That is the
correct behaviour and it is working.

**Why this is worth boarding:** the guide's "Known, accepted gaps" documents that
`ContainerSandboxExecutor` cannot reach `solve_task` server-side. It does **not**
document that the *other* sandbox path — the `unshare`-based `run_sandboxed()` behind
`decide_agent` — is also non-functional in the shipped compose config. So in the
configuration a user actually runs, **agent code execution is disabled entirely**,
not degraded. Recommend the guide's gap list say so explicitly, and that a founder
decide whether the compose service should get `cap_add: [SYS_ADMIN]` (which weakens
the container boundary — same class of trade-off as the docker-socket question, so
explicitly not a lane decision).

---

## Module 3 — PARTIAL: one real bug, two dead scripts

`integration_check_v2.py` (access control) — **PASS**, "V2 ACCESS CONTROL VERIFIED
against real Postgres."

**REAL BUG — `app/onboarding/seed.py:203`.** `integration_check.py`,
`integration_check_2.py` and `integration_check_3.py` all crash identically:

```
TypeError: 'NoneType' object is not subscriptable
  app/onboarding/seed.py:203  ->  v_scope[0], v_scope[1],
```

Cause, at `seed.py:164-171`:
```python
v_scope = None
if scope_type or spec.knowledge:      # <-- guard mentions knowledge only
    v_scope = validate_scope(...)
```
`v_scope` is populated only when the spec has **knowledge** nodes (or an explicit
`scope_type`), but the **`task_nodes`** insert at line 203 dereferences `v_scope[0]`
**unconditionally**. Any spec with tasks but no knowledge and no explicit scope
crashes. The knowledge insert at line 187 is guarded correctly by construction (it
only runs when `spec.knowledge` is non-empty — which is exactly what sets `v_scope`);
the task loop has no such protection.

Proposed one-line fix (**not applied** — `app/onboarding/**` is outside this lane's
granted paths, hard rule 4):
```python
if scope_type or spec.knowledge or spec.tasks:
```

**Two dead scripts in the guide's Module 3 catalog** — both listed as runnable, both
fail at import:
- `integration_check_graph_overview.py` → `ImportError: cannot import name
  'get_whole_graph' from 'app.api.graph'`
- `integration_check_v2_repo_execution.py` → `ModuleNotFoundError: No module named
  'app.services.repo_execution'`

Recommend the guide mark both as stale (or they get deleted) rather than leaving a
future runner to rediscover this.

---

## Module 13 — Docker Compose boot, in detail

`docker compose down -v` (volume genuinely removed) → `docker compose up --build -d`.

- Image rebuilt (`--build` honoured; new sha `b89a2649…`).
- Postgres healthy, backend started after the healthcheck gate.
- **All 31 migrations applied automatically** on the fresh volume, 01 → 31, in order.
- `POST /mcp` unauthenticated → **401**.
- `initialize` with bearer token → **200**, real `mcp-session-id` returned.
- `tools/list` → **200**, exactly **9** tools:
  `apply_change_set, check_procedure, decide_decomposition, decompose_task,
  detect_conflict_trigger, propose_synthesis, retrieve_precedent, solve_task,
  submit_approval`.
- `git --version` inside the container → **git version 2.47.3** (present).
- `migrate.py --status` inside the container → nothing pending.

**Note on the tool count:** a naive `grep -c '@server.tool()'` over `server.py`
returns **10**, not 9. The tenth match is prose inside the module docstring
(line 14) discussing the count. The live server answers 9. Anyone re-checking this
by grep alone will get a false drift alarm — worth a line in the guide, since Module
4 explicitly tells you to grep for `@server.tool()`.

---

## Modules 12 & 15 — not run, deliberately

Both require real model spend. The board carries an **OpenRouter budget wall** note
and the standing instruction this session has been `:free`-suffixed models only
because the account has no paid balance. Tonight's brief did not authorise spend, and:

- **Module 12** is a *re-run* of a proof already recorded (`f7262a5`) — its value is
  confirmatory, not blocking.
- **Module 15** the guide itself says explicitly: *"do not gate a release on this
  module."*

Rather than spend without a mandate or fake a result, both are left open with the
reason stated. Say the word and either can run.

Note that Module 7b — the expensive half of the founding loop — **did** get proven
tonight without paid inference, because the deterministic extractor
(`deterministic_v1@1`) needs no model call. See
`.scratch/research/founding-loop-real-data-proof.md`.
