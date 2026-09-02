# StealthLab v0.1 — End-to-End Testing Guide

> **Historical (v0.1).** Tool counts and suite numbers below predate Final
> V1. The current MCP surface is **29 tools** (`apply_change_set` was
> removed as a public tool post-freeze, `v1-final-2026-09-03.1`); see
> `docs/final-v1.md` and `backend/README_MCP_SERVER.md` for the current
> surface. The module structure and per-module "what a pass means" notes
> below still apply.

Companion to `PRODUCTION_READINESS.md` (status snapshot) and `.scratch/build-board.md`
(lane history). This doc is the runbook: how to actually verify every real component
of v0.1, module by module, so anyone (a lane, a fresh contributor, a future Claude
session) can pick exactly the modules relevant to what changed and run them
independently — no module depends on another having been run first, except where
explicitly noted.

**How to use this:** find the module(s) that cover what you touched. Each module
states its real prerequisites (offline / needs a DB / needs a real model / costs
money), the exact commands, and what a pass actually means — not just "no error,"
but what failure mode this module exists to catch. Log any new finding on
`.scratch/build-board.md`, same convention as every lane this session.

**Cost/dependency tiers**, cheapest first — always run a lower tier before a higher
one when debugging, same discipline `backend/TESTING_PLAN.md` established for
real-model testing specifically:

| Tier | Needs | Modules |
|---|---|---|
| 0 | Nothing but Python | 1, 9 (offline half) |
| 1 | A real Postgres (local or disposable) | 2, 3, 5, 6, 7, 8, 10 (off-by-default check) |
| 2 | Docker | 11, 13 |
| 3 | Node/npm | 14 |
| 4 | A real model, real spend | 12, 15 |

---

## Module 1 — Backend offline suite

**Verifies:** every unit of logic in isolation. No DB, no network, no model calls.
**Prerequisites:** Python 3.11+, `pip install -r backend/requirements.txt
--break-system-packages` (make sure `pytest-asyncio` is present — it's a real
dependency several test files need despite historically being easy to miss).

```bash
cd backend
export STEALTHLAB_MCP_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python -m pytest tests -q
```

**Pass:** all tests pass, 0 failed. Skips are expected — they're the DB/API-key-gated
tests Module 2+ actually exercises. **Do not hardcode the expected pass count
anywhere** — it changes with every commit; always read it fresh. (`backend/README.md`
had a stale "1266 pass" count sitting in two places for a while — exactly this
mistake, now fixed.)

**What a failure here means:** a real logic bug, not an environment issue — this
tier has no external dependencies to blame.

---

## Module 2 — Database & migrations

**Verifies:** all 30 migrations apply cleanly, in order, on a disposable database.
**Prerequisites:** a real Postgres instance (local, Docker, or disposable cloud).

```bash
cd backend
python scripts/migrate.py --status   # see what's pending
python scripts/migrate.py            # apply everything
```

**Pass:** every migration applies with no errors, `--status` afterward shows nothing
pending. **Do not run this against a shared, non-disposable database** unless you
mean to — several board notes this session flagged the real Supabase instance is
shared, not disposable; prefer a throwaway local/Docker Postgres for anything
destructive.

---

## Module 3 — Live integration checks (catalog)

**Verifies:** everything that genuinely cannot be tested offline — real Postgres
behavior (RLS, real transactions, real query plans), not just mocked pools.
**Prerequisites:** Module 2's database, a real `DATABASE_URL` in `backend/.env`.

These are **not** part of the pytest suite — they're standalone scripts, run
individually:

| Script | Verifies |
|---|---|
| `integration_check.py` | Core loop logic against real Postgres |
| `integration_check_2.py` | `DebateStateMachine` / `TriggerDetector` (zero unit coverage otherwise) |
| `integration_check_3.py` | Ingestion endpoint, `Layer1Evaluator`, full approval `decide()` |
| `integration_check_4.py` | Silent all-agents-failed debate vs. a real one |
| `integration_check_graph_overview.py` | `GET /v1/graph` against real data |
| `integration_check_v2.py` | Access control properties beyond the predicate builder |
| `integration_check_v2_agent_promotion.py` | Decomposition promotion + human decision |
| `integration_check_v2_agent_review.py` | Agent review orchestrator, 4 real cases |
| `integration_check_v2_code_review.py` | Code-sourced review, real bandit scan (not mocked) |
| `integration_check_v2_decomposition.py` | Approved-decomposition application + escalation |
| `integration_check_v2_governance.py` | Rate limiting / cost governance (see Module 8) |
| `integration_check_v2_hierarchy.py` | Task hierarchy (Part B) |
| `integration_check_v2_human_participation.py` | Human-in-the-loop debate, real trigger → real debate |
| `integration_check_v2_repo_execution.py` | Sandboxed repo execution — **needs a real Docker daemon** |
| `integration_check_v2_sandbox.py` | Sandboxed execution wiring into `decide_agent` |

```bash
cd backend
python integration_check_v2_governance.py   # example — run any subset relevant to your change
```

**Pass:** each script prints its own checklist; read its output, don't assume exit
code 0 alone means everything passed (some print warnings for known, accepted gaps).

---

## Module 4 — MCP server, all 9 tools

**Verifies:** the actual interface external agents call.
**Prerequisites:** Module 2's database, `STEALTHLAB_MCP_TOKEN` set.

```bash
cd backend
python -m app.mcp_server.server   # Streamable HTTP on loopback
```

Then, from a client (or `claude mcp list` if using Claude Code as the caller):
confirm the tools resolve — `retrieve_precedent`, `propose_synthesis`,
`find_best_way`, `detect_conflict_trigger`, `check_procedure`,
`decompose_task`, `decide_decomposition`, `submit_approval`. An unauthenticated
`POST /mcp` should return 401. (`apply_change_set` was in this list at v0.1;
it was removed as a public tool post-freeze — `v1-final-2026-09-03.1`.
The live registry is now 29 tools, not 9 — grep `@server.tool()`.)

**Pass:** 9 tools, not 7 or 8 — this exact drift has happened three separate times
in the docs this session (root README, `README_MCP_SERVER.md`, `packaging/README.md`,
the CLI's own `--help` text all drifted independently). If you add or remove a tool,
grep for `@server.tool()` and update every doc that states a count, not just one.

---

## Module 5 — Trace ingestion pipeline

**Verifies:** `agent_traces → trace_events → observations`, the first half of the
founding loop.
**Prerequisites:** Module 2's database, some real trace data (`.claude/traces/*.jsonl`,
written by `hook_wrapper.py` during normal Claude Code usage in this repo).

```bash
cd backend
python scripts/run_ingestion.py --once
```

**Pass:** real counts printed for `trace_events`/`observations` processed, no
exceptions. This pipeline does **not** produce episodes or claims — that's Modules
6–7. If `files_processed: 0`, check `_default_trace_dir()`'s resolution (it reads
`.claude/traces/`, not `~/.claude/projects/...` — that's Module 6's directory,
different script, don't confuse the two, this exact mix-up cost real time earlier
tonight).

---

## Module 6 — Episode assembly

**Verifies:** raw session transcripts segment into episodes correctly, and the
replay contract holds (rerunning inserts nothing new).
**Prerequisites:** Module 2's database, real Claude Code session transcripts
(`~/.claude/projects/<mangled-repo-path>/*.jsonl` — Claude Code writes these
automatically as you work in this repo).

```bash
cd backend
python scripts/ingest_transcripts.py --dry-run   # assemble only, no DB writes
python scripts/ingest_transcripts.py             # real run, writes episodes
python scripts/ingest_transcripts.py             # run again immediately
```

**Pass:** `--dry-run` reports real `main_lines`/`episodes`/`bad_lines` counts (not
zero, unless you genuinely have no transcripts yet). The second real run must show
`0 inserted / N skipped` — if it duplicates everything, the replay contract broke
(this exact bug existed and was fixed once already — see `write_session_episodes`'s
metadata encoding, must be a dict passed to the `$4::jsonb` param, never
`json.dumps(...)`-ed first, that double-encodes and silently breaks the dedup SELECT).
Also confirm privacy: query `episodes` and check `content IS NULL` for all rows —
only `content_ref` (a locator) and structural `metadata` should ever be written,
never raw transcript text.

---

## Module 7 — The founding loop (trace → observation → claim → procedure)

**Verifies:** the actual value proposition — that a solved task produces reusable
knowledge a later task can find. This is the module that matters most.
**Prerequisites:** Modules 5–6 run first (real observations and episodes must exist).

**7a — observation → claim (Option B, episode-justified):**
```bash
cd backend
python scripts/run_ingestion.py --once   # drains ingestion_jobs, including promote_observation_to_claim
```
Then check the DB directly:
```sql
SELECT count(*) FROM knowledge_nodes WHERE node_type = 'claim';
SELECT count(*) FROM episode_links;
SELECT count(*) FROM claim_sources;
```
**Pass:** nonzero claims for any observation whose event resolved to a real episode.
Coverage is bounded by how many sessions Module 6 has processed — 0 claims here
usually means 0 episodes exist yet, not a code bug. Cross-check against
`resolve_justification_episode()`'s logic if a claim you expect to exist doesn't.

**7b — claim → procedure (needs a real model — this is the expensive, most
important check):**
```bash
cd backend
# via the MCP tool, or directly:
python -c "
import asyncio
from app.mcp_server.server import find_best_way
# call with a real repo_path and task_description, model=<a real, live model>
"
```
Run it **twice** on similar tasks. Check:
```sql
SELECT id, name, verification_state FROM procedures ORDER BY created_at DESC LIMIT 5;
```
**Pass:** the first run writes a `procedures` row. The second run, called with
`allow_unverified_procedures=True`, matches and cites it — check the tool's returned
text for the "UNVERIFIED — opted in..." block. **If a task description names a
specific file, extraction may be structurally refused** (`V4_capability_abstraction`
rejects any evidence token in `capability_statement`, and the deterministic
extractor sets that field to the goal text verbatim) — this is a known, diagnosed,
not-yet-fixed gap (CORE-B's territory, `procedure_extraction/**`), not a new bug if
you hit it. Reword the task without naming a file to isolate whether that's what's
happening.

---

## Module 8 — Governance & rate limiting

**Verifies:** the real token-bucket rate limiter and cost governance, live.
**Prerequisites:** Module 2's database.

```bash
cd backend
python integration_check_v2_governance.py
```

**Pass:** all checks pass, including concurrency and cost-governance cases. If
`RateLimiter(pool)` throws `TypeError: cannot create weak reference to 'Pool'
object` — that exact production crash existed once, is fixed, and should never
recur; if it does, something regressed the `_STATES` dict keying.

---

## Module 9 — Redaction & secrets safety

**Verifies:** no trace content or secret ever reaches an external system unredacted.
This is the highest-stakes module — a memory substrate that leaks secrets isn't
usable at all, regardless of anything else working.

**Offline half (Tier 0):**
```bash
cd backend
python -m pytest tests/test_band1_11_redaction.py tests/test_observability_offline.py -q
```
**Pass:** all pass. These prove `redact_event()` and Sentry's `_scrub()` both
correctly strip known secret patterns and sensitive paths, and both fail closed
(drop the event rather than risk sending it raw).

**Manual audit half (do this before any public release, not just once):**
```bash
git log --all -G'DATABASE_URL=' --oneline
git log --all -G'sk-ant-' --oneline   # or whatever your real key prefixes are
git stash list   # stashes are LOCAL ONLY and never pushed — check them separately per machine
```
**Pass:** nothing reachable from `origin/main` or any pushed branch. If something
appears only in local history/stash (as real credentials did once this session),
that is still real exposure risk on that machine — rotate the credentials, don't
just leave them because "it never reached GitHub."

---

## Module 10 — Observability (Sentry)

**Verifies:** off by default, on and redacted when configured.

**Off-by-default check (Tier 0, always run this one):**
```bash
cd backend
unset SENTRY_DSN
python -c "from app import observability; print(observability.init('test'))"
# must print False — no DSN means no reporting, no import of sentry_sdk even attempted
```

**Live check (Tier 1, optional — only if you actually have a Sentry project):**
set a real `SENTRY_DSN`, trigger a deliberate error in each of the three entry
points (`api`, `mcp`, `worker`), and confirm the event arrives in Sentry with
`tool_input`/`tool_output`-shaped fields showing `[REDACTED:...]`, never raw content.

---

## Module 11 — Sandbox execution

**Verifies:** code execution isolation, and its honest limits.
**Prerequisites:** Docker, for the container path only.

```bash
cd backend
python -m pytest tests/test_sandbox_executor.py -q   # SubprocessSandboxExecutor, offline
python integration_check_v2_sandbox.py                # wiring into decide_agent
python integration_check_v2_repo_execution.py          # needs a real Docker daemon
```

**Pass, and an honest limit to know about:** `ContainerSandboxExecutor` gives real
isolation (`--network none`, read-only rootfs, resource limits, no fallback to
unsafe execution) but **only works host-side** — the shipped backend container has
no Docker socket, so `find_best_way` (which runs server-side inside that container)
cannot actually reach it in the deployed configuration. This is deliberate, not a
bug — mounting the Docker socket into that container would be a bigger security
problem than the one it solves (documented in `sandbox_executor.py`'s own
docstring). Don't silently "fix" this without a founder-level call, same class as
the licensing question.

---

## Module 12 — The core loop, real model, real proof

**Verifies:** Module 7b, but as a deliberate, documented, paid proof run — not
just a spot-check. This is the module to re-run before ever claiming publicly
"solving a task once helps the next one."

**Prerequisites:** a real, live model, real spend (a few cents is enough).

Follow Module 7b's steps exactly, but this time: pick two distinct real coding
tasks in a scratch repo, run each twice as described, and **write down the real
numbers** — procedure id, verification_state, attempts, successes, distinct
contexts — in a board note or this doc's own changelog, the same way `f7262a5`'s
commit message did. Don't round an ambiguous result up to "it works."

---

## Module 13 — Docker Compose boot test

**Verifies:** the actual thing a user runs — `docker compose up`.
**Prerequisites:** Docker.

```bash
docker compose up --build -d   # --build is load-bearing — without it, a stale
                                # image silently reuses old code and yields a false pass
docker compose logs -f backend
```
Then confirm: `POST /mcp` unauthenticated → 401, authenticated → 200; `claude mcp
list` reports Connected; all 9 tools resolve; migrations applied automatically (or
run Module 2 against the compose Postgres). Also confirm `git` is present in the
container (`docker compose exec backend git --version`) — its absence silently
degraded `find_best_way`'s structural retrieval before this was caught.

**Pass:** clean boot, no manual intervention beyond `.env` setup, all of the above
true. Tear down after with `docker compose down -v` if the volumes were disposable.

---

## Module 14 — Frontend

**Verifies:** the approval-review UI actually builds and runs for a new user.
**Prerequisites:** Node/npm, real network access (font loading needs
`fonts.googleapis.com`).

```bash
cd frontend
rm -rf node_modules package-lock.json   # genuinely fresh, not incremental
npm install
ls node_modules/.bin | grep next        # must exist — if not, stop here, this is the real bug
npm run build
npm run dev
```

**Pass:** `node_modules/.bin` populated, `npm run build` completes with real
TypeScript type-checking and static generation, `npm run dev` serves all current
routes — `/`, `/approvals`, `/approvals/[id]`, `/archive`, `/tasks`,
`/tasks/medical-report-extraction`, `/visualize`, `/workbench`. **Do not trust a
build that skipped network-dependent steps** (font loading, external fetches) as
"confirmed clean" — that exact gap between a sandboxed partial build and a real one
caused a real, previously undetected failure this session.

---

## Module 15 — Statistical validation (optional, evidence not a functional requirement)

**Verifies:** whether substrate-grounded task-solving measurably beats solving
alone — Claim 1 in `experiments/harness/`. This is confirmatory evidence for the
pitch, **not** something a user needs working — don't gate a release on this
module.

**Prerequisites:** a model (paid or a confirmed-live free-tier model), real spend
or a free-tier daily cap to work within.

```bash
cd experiments/harness
python run_real_arms.py --fixtures-dir fixtures/model_decides \
  --out model_decides_results.jsonl --auto-resume --models <model-id> \
  --spend-log model_decides_spend.jsonl
python run_model_decides.py --results model_decides_results.jsonl
```

**Pass:** a complete run (all fixture tasks resolved, not partial) with a
statistically meaningful sensitivity/specificity number, and `served_by_model`
verified on every row (catches silent provider substitution). Do not report
numbers from a partial run — that misrepresents statistical power.

---

## Known, accepted gaps (not bugs — check `PRODUCTION_READINESS.md` before re-discovering these)

- Identity/auth (`authn.py`) is built and tested but switched off by design until
  OIDC is actually configured.
- Rate limiting only works correctly with exactly one worker process
  (`--workers 1`).
- `ContainerSandboxExecutor` can't reach `find_best_way` in the shipped compose
  config (Module 11) — deliberate, needs a founder call to change.
- Claim → procedure extraction is structurally refused whenever a task
  description names a specific file (Module 7b) — diagnosed, not yet fixed,
  owned by the `procedure_extraction/**` path.
- Licensing is undecided — `demo.md` says Apache-2.0, `packaging/pyproject.toml`
  says Proprietary, no `LICENSE` file exists. See `BAND0_DECISIONS.md` D6.
  **Do not treat this repo's license as settled until that's resolved.**

## Changelog

- 2026-08-28: initial version, written after the core-loop proof (`f7262a5`),
  Option B (`71e8b29`), episode assembly hardening (`b8b23e4`), Sentry
  (`870ba7c`), and the fresh-clone dry run (CORE-B) all landed the same night.
- 2026-08-29: landed by MEASURE. Module 14's route list updated from 3 to the 8
  routes now live on `main` (`/archive`, `/tasks`, `/tasks/medical-report-extraction`,
  `/visualize`, `/workbench` added since this guide was drafted). Everything else
  (script paths, test filenames, MCP tool count, `pytest-asyncio` dependency, the
  stale-README pass-count fix) re-verified against `origin/main` and confirmed
  accurate as written — the local `lane/measure` worktree was behind on several of
  these (`app/observability.py`, `scripts/ingest_transcripts.py`,
  `tests/test_band1_11_redaction.py`) because they land via sibling lanes not yet
  merged into this branch locally; `origin/main` already has them.
