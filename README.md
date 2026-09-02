# StealthLab

**North star:** *agents that earn the right to remember.* Most agent memory
records what happened. StealthLab distills what an agent *believes*, tracks
whether that belief still holds, and refuses to let an agent reuse a
procedure whose preconditions have quietly changed — with a cited reason,
not a shrug.

This file describes two things on purpose, clearly separated, because they
are not the same thing yet:

1. **What you can run today** — a working MCP server and CLI, described below.
2. **What the product is being built into** — the "earned memory" v0.1 slice
   defined in [`demo.md`](demo.md), whose full technical positioning lives in
   [`commLLM.md`](commLLM.md). The refusal mechanism in that document is real
   and independently verified against a live model (see
   `.scratch/research/model-decides-verification.md`), but it isn't reachable
   through the server below yet — `demo.md` names this gap explicitly.

If you only read one doc, read `demo.md` — it's the shorter, current
statement of what ships and what proves it.

---

## StealthLab V1 — what ships (frozen)

V1 is a **verified _personal_ procedural memory** for coding agents, with an
explicit path to a shared commons. The lifecycle a user actually experiences:

```
your existing work ─┐
your agent's new work ─┼─▶ PRIVATE personal library ─▶ (you publish) ─▶ GLOBAL commons ─▶ another user reuses
                     ─┘        (candidates, local)         explicit          (candidates)      (independent evidence)
```

**What do I install?** `pip install -e packaging/` gives the MCP server, the
trace hook, and the status page. A Postgres 15 + `pgvector` instance
(`pgvector/pgvector:pg15`) is needed for the *global* commons and the MCP
server; your *private* library is a local SQLite file and needs neither.

**How do I connect an agent?** Point it at the MCP server (stdio, or HTTP
with `STEALTHLAB_MCP_TOKEN`). Multi-user installs set `OIDC_ISSUER` +
`OIDC_AUDIENCE` and `DEPLOYMENT_MODE=shared`; the server refuses to boot in
`shared` mode without OIDC so per-caller identity is never silently lost.

**How do I bring in my existing work?** One command:

```bash
cd backend
python scripts/bootstrap.py --repo-root /path/to/repo \
  --claude-export ~/claude/conversations.json \
  --chatgpt-export ~/chatgpt/conversations.json \
  --traces-dir ~/.claude/projects/<project>
```

`--repo-root` processes the repo's procedural docs **and its real git
commit history** (conservative fix→test / migration→code→test patterns,
never one candidate per bare commit). Claude/ChatGPT exports and Claude
Code transcripts converge into the **same** private library, deduplicated,
with every source's provenance preserved. Chat evidence stays honest: a
recommendation ("you could run pytest") is never upgraded to *executed* by
an unrelated later "tests passed" — status is attached to the material it
describes, per step.

**Where does my private memory live?** `<workspace>/.stealthlab/local_procedures.db`
(SQLite). Nothing in the bootstrap or the automatic learning path sends it
anywhere.

**How does ongoing learning happen?** The app runs an in-process learning
loop (`INGESTION_AUTO_MODE=local`, the default). On a timer it reads new
local trace transcripts and writes **private candidates** into that same
SQLite library — bounded (a few sessions per tick), idempotent, no LLM call
per event, failures visible in `GET /v1/admin/ingestion/auto-status`. A raw
local trace is **never** uploaded to the global server just because
automatic learning is on. (`INGESTION_AUTO_MODE=global` is the opt-in
shared-substrate path for a company deployment.)

**How does a procedure mature?** Every procedure is born a `candidate`.
Real reuse records real outcomes; a **failure lowers capability, it does
not raise it**; retrying the same context is not independent evidence.
`verified` is reached only on genuine independent supporting evidence
(≥ successes across distinct contexts, invariant #3 gate). Multiple
compatible successful episodes can synthesize a **generalized** procedure
with provenance preserved. When a relevant precondition or environment fact
changes, the procedure goes `stale` and retrieval/applicability stop
selecting it — with a cited reason, not a shrug. `UNKNOWN` in a
local-applicability check fails closed.

**How does global publishing work?** You explicitly publish a trusted
private procedure. It is scrubbed and enters the global commons as a
**fresh `candidate`** — the local verification count is **not** copied.

**How does another user reuse it?** User B's own retrieval surfaces the
global candidate; User B's own local applicability check runs; User B
executes it in their own environment; the outcome becomes **independent**
global evidence. User B **cannot** see User A's private git-derived,
repo-derived, chat-derived, trace-derived, or unpublished material —
tenant scoping (`app.tenant_id`, `scope_predicates()`) plus a
row-level-security backstop (migration 29).

### Not in V1 (deliberately post-V1)

Production SLM / WASM execution runtimes · internet-scale ingestion · a
marketplace or monetization · massive distributed scaling · a reputation
economy beyond "execution evidence is the reputation signal".

---

## Final-V1 update (2026-09-03)

The FINAL-V1 hardening wave landed since the frozen section above.
[`docs/final-v1.md`](docs/final-v1.md) is the authoritative account; the
short version of what changed:

- **Problem / Benchmark / Solution / Evaluation is a shipped product
  concept, not a future one.** Migration 35 adds the tables;
  `app/services/product_model.py` is the one service; `app/api/problems.py`
  exposes 17 `/v1` routes; six read-only MCP tools (`find_problem`,
  `inspect_problem`, `list_problem_solutions`, `compare_solutions`,
  `inspect_evaluation`, `find_best_solution`) share it. It is an
  association + read-model layer over the existing substrate — no target
  object is copied, no second execution engine. A completed **Evaluation**
  aggregates real Executions + Evidence (the DB and the service both
  reject a `completed` status with no execution lineage); a Problem's
  **current-best** Solution is derived on read from a Wilson lower bound,
  never stored, and is `[]` until something is verified.
- **Evaluation is a first-class product concept now**, not just a testing
  artifact — it is the unit that turns real execution evidence into a
  comparable, version-pinned result and a leaderboard.
- **Execution is durable on the production tier-2 path.** MCP
  `find_best_way` tier-2 and `reproduce_procedure` run through
  `app/execution/durable_graph.run_graph_durably` on the
  `execution_runs` / `execution_run_nodes` substrate (migrations 36–37),
  not the in-memory loop. **Retry / resume exists**: a crashed run resumes
  through `find_best_way(resume_run_id=…)`, completed nodes are not re-run,
  a terminal node is fenced against stale workers, and a concurrent resume
  is refused. Exactly one immutable `executions` row is appended on
  terminal, with `implementation_id` pinned.
- **Execution descriptor** — one deterministic, secret-free projection of
  an Implementation Registry row to the execution ABI
  (`GET /v1/implementations/{id}/descriptor`; also on MCP
  `inspect_implementation` / `resolve_implementation`).
- **Ingestion security** — historical ChatGPT-export ingestion now
  reconstructs the conversation branch tree and drops abandoned sibling
  branches (§28); document / skill ingestion treats untrusted document
  text as data behind a fence and cannot use it to escalate capability or
  trust (§29).
- **MCP surface is 28 registered tools** (was 21).
- **The V1 product surface is `frontendv1/`** — a separate Next.js 16 app
  (now tracked) with the benchmark-first pages and a 13-tool WebMCP
  bridge, owned by the frontend session. The older `frontend/` (Next.js 15
  debate/approval UI) is **not** the V1 surface.

---

## What runs today

A local-first MCP server exposing **20 tools** (verified 2026-09-01 against
`app/mcp_server/server.py`'s live tool registry — up from 9; the newer ones
add procedure decisioning and the Implementation Registry surface,
`check_applicability`/`decide_procedure`/`get_implementation_capability`/
`get_procedure`/`inspect_implementation`/`list_task_implementations`/
`report_execution`/`reproduce_procedure`/`resolve_implementation`/
`search_procedures`/`submit_procedure` among them) over a bi-temporal
knowledge/task graph: ingest execution traces, detect a bottleneck, run a
multi-model debate to propose a fix, evaluate it statistically, and apply it
only after a human approves — every state change auditable and reversible
via the graph's supersede-not-delete history. The table below covers the
original debate/approval slice only; it predates the newer tools.

| Tool | What it does |
|---|---|
| `retrieve_precedent` | Find prior solved patterns relevant to a query |
| `detect_conflict_trigger` | Find a real conflict between knowledge nodes, open a debate |
| `propose_synthesis` | Run a real multi-round model debate on a trigger, produce a scorecard |
| `submit_approval` | Approve/reject a scorecard — applies + audits + closes the debate atomically |
| `decompose_task` | Turn an unstructured problem into a structured change proposal |
| `decide_decomposition` | Approve or reject a decomposition proposal |
| `apply_change_set` | Apply a change set directly, no approval gate (ungated — use narrowly) |
| `find_best_way` | Retrieval-grounded coding agent against a real repo on disk |
| `check_procedure` | Applicability check → `ALLOW` or structured `WOULD_REFUSE` with a cited reason (audit mode only) |

Full setup, the stdio vs. hosted-HTTP split, and the known v1 limitations
(no job queue, `apply_change_set` is an ungated write, `repo_path` is
caller-controlled) are documented in
[`backend/README_MCP_SERVER.md`](backend/README_MCP_SERVER.md) — that's the
accurate, current setup doc; if anything below disagrees with it, trust it
instead.

**Quick install**, via the packaging layer
([`packaging/README.md`](packaging/README.md)), which imports the same
backend code above and adds zero business logic of its own:

```bash
pip install -e packaging/
stealthlab-mcp-server            # Streamable HTTP on loopback, or --stdio
stealthlab-trace-hook            # Claude Code hook: redact + collect one trace event
stealthlab-status-page           # read-only: episodes -> claims -> procedures, capability scores
stealthlab-public-board          # static scoreboard page from a real-arms sweep + spend ledger
```

Needs a Postgres 15+ instance with the `pgvector` extension (Supabase
works) — see `backend/README.md` for the exact image/connection-string
gotchas (stock `postgres:15` cannot run the migration chain; use
`pgvector/pgvector:pg15`) — plus `backend/.env` set with at minimum
`DATABASE_URL` and `VOYAGE_API_KEY`, and `STEALTHLAB_MCP_TOKEN` for HTTP
mode (`--stdio` skips auth by protocol design), before `stealthlab-mcp-server`
above will actually start; full variable list in `packaging/README.md`'s
Configure section or `backend/README_MCP_SERVER.md`'s Setup section.

The backend also exposes a read-oriented REST API alongside the MCP server
(verified 2026-09-01, routers wired in `backend/app/main.py`): `/v1/claims`,
`/v1/procedures`, `/v1/solutions`, `/v1/repositories`, `/v1/projects`,
`/v1/tasks`, `/v1/me`, `/v1/search`, and `/v1/implementations` (the
Implementation Registry). No client or quickstart doc for this layer exists
yet outside the code itself.

Tests: `cd backend && python -m pytest tests -q` — offline, no DB or API
keys required for the bulk of the suite. Needs `backend/.env` to exist first
(`cp backend/.env.example backend/.env`, then set `STEALTHLAB_MCP_TOKEN` —
see the generation command inside that file) — without it, two test files
fail to even collect and the whole run aborts before anything executes.

### Ingesting collected traces

> **V1 note:** for normal use you do **not** run an ingestion script. The
> app's in-process learning loop (`INGESTION_AUTO_MODE=local`, the default —
> see the V1 section above) turns local trace transcripts into private
> candidates automatically. The scripts below are the *global*/company
> substrate path and the one-shot historical bootstrap.

`stealthlab-trace-hook` writes collector files (`.claude/traces/*.jsonl`).
For a shared/company deployment (`INGESTION_AUTO_MODE=global`), the loop
calls the same path `backend/scripts/run_ingestion.py` drives — trace event
→ `trace_events` → `observations` → `claims` → shared `procedures` — via the
job queue in `app/services/ingestion_jobs.py` (`enqueue_pending_claim_promotions`
/ `enqueue_pending_procedure_extractions` / `process_pending_jobs`). Model
calls (embeddings, extraction) are bounded and opt-in per the loop's caps.

## What it's becoming

`demo.md` defines the minimal shippable v0.1 slice: ingest an agent's traces
→ distill evidence-backed procedures → surface them on similar future tasks
→ **refuse stale reuse with cited reasons**, in audit mode (the agent is
informed, never blocked, until Band 3). Every capability in that document
ships with the exact command that proves it — nothing is claimed without a
passing test on the release commit. `check_procedure` (refuse stale reuse
with a cited reason, audit mode only) is now part of what runs today, not
what's becoming — see the tool table above. `commLLM.md` carries the full
positioning: the research base, the competitive landscape, and the tool
surface this is still expanding into (`explain_decision`,
`explain_failure`).

The engine behind that story is real and extensively tested — evidence
tracking, capability scoring, precondition/applicability gating, failure
classification and routing all exist and are proven offline and, where
claimed, against a live model. Most of that engine is now wired into the MCP
tool surface (verified 2026-09-01: `check_applicability`, `decide_procedure`,
`get_procedure`, `report_execution`, `reproduce_procedure`,
`search_procedures`, `submit_procedure`, plus a new Implementation Registry
group — `get_implementation_capability`, `inspect_implementation`,
`list_task_implementations`, `resolve_implementation` — are real, registered
tools, not just engine code). What's still open is narrower than "tool
wiring": `demo.md`'s ship checklist tracks one remaining gap, that a fresh
install's own prior work doesn't yet surface via `retrieve_precedent` (the
episode → claim pipeline breaks before procedures are produced from real
agent sessions).

Landed since the 2026-09-01 pass above (verified against source/tests
2026-09-02, not inferred from commit messages): the Implementation Registry
is no longer just standalone read tools next to the hot path —
`_bind_plan_to_registry()` in `app/mcp_server/server.py` now calls
resolve→bind at all four real `find_best_way`/`reproduce_procedure`
production call sites, pinning a durable implementation to a compiled plan
before persistence (with a plan-pinning guard so a replay reuses an
already-bound plan rather than re-resolving). Multi-episode generalization
(`synthesize_procedure`) now has a real production caller too — ingestion
auto-discovers up to 4 compatible same-scope single-episode procedures
after each extraction and attempts synthesis itself, instead of requiring a
caller to hand-pick `episode_ids`. The publish-time privacy scrub
(`publish_local_procedure`) now redacts absolute filesystem paths and
covers `preconditions`/`scope`/`exclusions`, not just `name`/`goal`/`steps`.
A new admin endpoint, `POST /v1/admin/failure-routes/process`, gives
`fetch_route_queue()` a real production consumer. And an unscoped IDOR on
`GET /v1/agent-store/{agent_id}` (a raw `SELECT * FROM agents WHERE id = $1`
with no visibility check) is fixed.

## Security & data

[`SECURITY.md`](SECURITY.md) — threat model, stated plainly: a bearer token
gates *who* reaches the server, not *what* they can do once in.
[`DATA_STATEMENT.md`](DATA_STATEMENT.md) — local-first, redact-before-persist,
no phone-home telemetry in v0.1, never train on user traces without consent.

## Where things are, if you're exploring the repo

- `demo.md` — the current, short definition of what ships (start here).
- `commLLM.md` — full technical positioning, research base, competitive landscape.
- `ROADMAP.md` — the band-by-band work plan behind both of the above.
- `backend/` — the FastAPI app, the graph, the debate/eval/governance
  services, and the MCP server. `backend/README_MCP_SERVER.md` is the
  accurate setup doc for the server as it exists today.
- `packaging/` — the installable CLI wrapper (`stealthlab-connect`) described above.
- `experiments/harness/` — the evaluation harness comparing a solo agent,
  ordinary RAG, and the verified substrate on real model calls; this is
  where the earned-memory refusal claim in `demo.md` is currently proven.
- `.scratch/build-board.md` — the running multi-lane build log, if you want
  the detailed history of how any of the above got here.
