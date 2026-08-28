# StealthLab MCP Server — v1

Exposes StealthLab's bi-temporal knowledge/task graph, debate-based conflict
resolution, and a retrieval-grounded coding agent as 9 MCP tools.

## Setup

1. `pip install -r requirements.txt --break-system-packages` (or `uv run
   --with-editable .` for the Inspector, which picks up `pyproject.toml`
   automatically). Note: `tree-sitter-language-pack` is a real dependency
   introduced by `solve_task` -- make sure it's actually installed, not
   just listed.
2. `backend/.env` needs real values for at minimum: `DATABASE_URL`,
   `VOYAGE_API_KEY`. `propose_synthesis`/`decompose_task`/`submit_approval`
   additionally need a working panel -- either all three of
   `ANTHROPIC_API_KEY`/`FIREWORKS_API_KEY`/`OPENAI_API_KEY` (the default
   3-provider panel), or set `USE_GENERAL_COMPUTE=true` and provide
   `GENERAL_COMPUTE_API_KEY` for a single-provider panel (cheaper, easier
   to get fully working). Run `diagnose_panel_connectivity.py` to confirm
   your panel actually responds before relying on any debate tool.
3. `experiments/swebench_pro/` must exist as a real sibling directory of
   `backend/` -- `solve_task` imports `Agent`/`RepoSandbox` from there.

## The 9 tools

| Tool | What it does | Writes to the graph? |
|---|---|---|
| `retrieve_precedent` | Find prior solved patterns relevant to a query | No -- read-only |
| `check_procedure` | Audit-mode ALLOW/WOULD_REFUSE verdict on reusing a named procedure right now, with evidence | No -- read-only, informs the caller, never blocks |
| `decompose_task` | Turn an unstructured problem into a structured proposal (new nodes/edges), persisted but not yet applied | No -- returns a proposal only |
| `decide_decomposition` | Approve/reject a `decompose_task` proposal: re-runs the capability-boundary check at apply time | **Yes, gated** -- the correct path for `decompose_task`'s output |
| `apply_change_set` | Apply a change_set directly, no approval gate | **Yes, ungated** |
| `detect_conflict_trigger` | Find a real conflict between knowledge_nodes, open a debate trigger | Yes -- creates a proxy task node + trigger, doesn't touch existing content |
| `propose_synthesis` | Run a real multi-round debate on a trigger, produce scorecards | No -- drives debate state to `PENDING_APPROVAL`, doesn't write graph content |
| `submit_approval` | Approve/reject a scorecard: applies + audits + finalizes debate state | **Yes, gated** -- the correct path for debate-originated changes |
| `solve_task` | Retrieval-grounded coding agent against a real repo on disk | Yes -- to the filesystem, not the graph |

### Important: which gated tool goes with which proposal

Two different tools produce proposals, and each has its own required
apply step -- do not cross them:

- **`decompose_task` output → `decide_decomposition`, never `apply_change_set`.**
  `apply_change_set` uses `KnowledgeUpdater.apply()`, which never calls
  `validate_generative()` -- the capability-boundary check that is this
  project's stated only real guarantee against a prompt-injected/hijacked
  model (generated content may only *create* new nodes and connect them
  to each other, never modify or invalidate anything that already
  exists). `decide_decomposition` calls the real `app.api.decompose.decide()`,
  which re-runs `validate_generative()` at apply time, so a proposal
  tampered with in storage between propose and decide still can't
  escalate. This was a real bug in an earlier version of this server
  (`decompose_task` used to tell callers to apply via `apply_change_set`)
  -- fixed, but `apply_change_set`'s own docstring in `server.py` still
  describes decomposition proposals as its "intended case" and has not
  been updated to match; flagged here rather than silently rewritten
  in-code.
- **`propose_synthesis` output → `submit_approval`, never `apply_change_set`.**
  `apply_change_set` is a raw write primitive with **no approval gate** --
  it doesn't check debate state, doesn't require `APPROVED`, doesn't write
  an audit row. `submit_approval` is the real, gated path: it applies the
  change_set, writes a row to the `approvals` table, and transitions the
  debate to `APPROVED`/`REJECTED` -- all atomically, so there's never a
  false audit trail (an approval recorded against a change that didn't
  actually apply). Skipping this and calling `apply_change_set` directly on
  a debate scorecard's change_set bypasses human approval entirely.

`apply_change_set` itself is for change_sets that never went through
either proposal flow -- e.g. a manually constructed change_set for
testing.

## Quickstart -- MCP Inspector

```
cd backend
MCP_SERVER_REQUEST_TIMEOUT=300000 mcp dev app/mcp_server/server.py:server --with-editable .
```
(Windows cmd: `set MCP_SERVER_REQUEST_TIMEOUT=300000 && mcp dev ...`, or
just raise the timeout in the Inspector's own Configuration panel after
it opens -- the Inspector's default is a real 10s/60s, far too short for
a genuine multi-round debate.)

Test order, cheapest/safest first: `retrieve_precedent` → `apply_change_set`
with deliberately malformed input → `detect_conflict_trigger` → only then
`propose_synthesis`/`submit_approval`/`decompose_task`/`solve_task`, since
those cost real API spend.

## Hosting -- Streamable HTTP, for real clients (Claude Code included)

The Inspector quickstart above uses **stdio** (a subprocess Claude Code or
`mcp dev` spawns and talks to over stdin/stdout). This section is for
**hosting** the same `server` object over HTTP so any Streamable HTTP client
can connect to it, including a Claude Code instance on a different machine
on your network.

**Requires `mcp[cli]>=2.0.0`** (`pyproject.toml`/`requirements.txt` are
already pinned to it). The 1.x line ships `FastMCP`/`Server`, not the
`MCPServer` class this file uses -- confirmed by a real failing import
against 1.29.0, not assumed from changelogs.

**Why this stays loopback-only.** `DATABASE_URL` is a local Postgres
instance -- a cloud-hosted server could not reach it. More importantly,
`solve_task`'s `repo_path` is caller-controlled and `apply_change_set` is an
**ungated write** (see "Known v1 limitations" below); a bearer token gates
*who* can call these tools, it does not make either tool safe against
*anyone* holding a valid token. Treat this as a way to reach the server from
another process/machine you already trust, not as a public deployment.

### 1. Generate a token and set it

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Add it to `backend/.env` (gitignored -- confirmed via `git check-ignore`,
never commit this) as `STEALTHLAB_MCP_TOKEN=...`. The server fails at
**import time**, not on the first tool call, if this is unset -- same
discipline as `lifespan`'s existing `DATABASE_URL` check.

### 2. Run it

```bash
cd backend
uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765 --workers 1
```

**Windows, multiple Python installs:** if this fails with
`ModuleNotFoundError: No module named 'mcp'` even though step 1's install
succeeded, the bare `uvicorn` on `PATH` is resolving to a *different*
Python install than the one `pip`/`python` point at (verified 2026-08-28:
`uvicorn.exe` on `PATH` came from a separate Python 3.11 install with no
project deps, while `pip install -r requirements.txt` had gone to a 3.14
install). Use `python -m uvicorn ...` instead -- it always runs under
whichever `python` resolves to.

`app` is `server.streamable_http_app()`, exposed at module level; it serves
`/mcp`. **`--workers 1` is load-bearing**, not a default left alone: the
Tasks extension's backing store (`tasks_extension.py`) is in-memory, so a
second worker would sometimes answer a `tasks/get` poll from a process that
never saw the task `propose_synthesis`/`solve_task` created, and that call
would appear to hang. Port 8765 avoids colliding with `app/main.py`'s
FastAPI app, which already uses uvicorn's conventional 8000.

Confirm auth is actually enforced before connecting anything to it:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp
# expect 401

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $STEALTHLAB_MCP_TOKEN"
# expect NOT 401
```

A 200 on the *unauthenticated* call means the token check did not take
effect and the endpoint is open to anything that can reach that port.

### 3. Connect Claude Code

```bash
claude mcp add --transport http stealthlab http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $STEALTHLAB_MCP_TOKEN" \
  --scope local
```

**Use `--scope local`, not `project`.** Project scope writes to `.mcp.json`
in the repo root, which is meant to be checked into version control --
that would commit the bearer token. Local scope keeps the entry in
`~/.claude.json`.

`propose_synthesis` and `solve_task` are genuinely long-running; raise the
per-server tool timeout past Claude Code's default by adding a `timeout`
(milliseconds) field to the server's entry in `~/.claude.json`:

```json
"stealthlab": {
  "type": "http",
  "url": "http://127.0.0.1:8765/mcp",
  "headers": { "Authorization": "Bearer ..." },
  "timeout": 600000
}
```

Then `claude mcp list` should show `stealthlab ✔ Connected`. `! Needs
authentication` means the header did not land; `✘ Failed to connect` means
either uvicorn or the database is not actually up.

## Example workflow -- knowledge-conflict governance loop

1. `decompose_task("we updated our vacation policy to 20 days")` → proposal
2. `decide_decomposition(<decomposition_id>, approver_id, "approved")` →
   commits the new node
3. `detect_conflict_trigger(<new_node_id>)` → finds the old "15 days" node,
   opens a real trigger
4. `propose_synthesis(<trigger_id>)` → real debate, produces a scorecard
   recommending "20 supersedes 15, effective [date]"
5. A human reviews the scorecard
6. `submit_approval(<scorecard_id>, approver_id, "approved")` → applies +
   audits + closes the debate, atomically

For the coding-assistant use case, it's just one call:
`solve_task(task_description, repo_path)` -- internally does its own
retrieval grounding, no multi-step governance loop needed.

## Getting collector traces into Postgres

None of the 9 tools above load `.claude/traces/*.jsonl` collector files into
the database themselves. Run `scripts/run_ingestion.py` for that:

```bash
cd backend
python scripts/run_ingestion.py --once
```

Free (no model calls); drives collector files through
`process_collector_file()`/`process_pending_jobs()` into `trace_events` and
`observations`. It stops there today — episode assembly is bypassed
entirely and the break is at observation → claim, so `retrieve_precedent`
won't see anything new from a run of this script alone (see `demo.md`'s C2
entry-point note and §3 reuse-demonstration checklist item for the
engine-verified measurement).

## Known v1 limitations, stated plainly

- **Layer 2 (empirical replay evaluation) is not wired.** `propose_synthesis`
  only runs Layer 1 (groundedness/fallacy checks). This was a deliberate
  scope cut, not an oversight.
- **Only knowledge-vs-knowledge conflicts are covered.** The original
  metric-threshold trigger detector (cost/error-rate/cycle-time bottlenecks
  from task execution) isn't exposed as an MCP tool -- `detect_conflict_trigger`
  only wraps the knowledge-conflict half.
- **Bulk/bootstrap ingestion isn't exposed.** `Onboarder.seed()` (hand-authored
  workflow specs) and `POST /v1/traces` (OTel-shaped agentic workflow trace
  ingestion -- a real, working endpoint, just not MCP-wrapped) both require
  going around the MCP server directly.
- **`solve_task`'s Tasks-extension backing store is in-memory.** Task state
  doesn't survive a server restart and doesn't work across multiple server
  replicas. Fine for single-process use, not for production multi-replica.
- **`repo_path` in `solve_task` is caller-controlled.** `RepoSandbox` prevents
  edits from escaping `repo_path` itself, but nothing stops a caller from
  pointing `repo_path` at a sensitive real directory in the first place.
  Fine for trusted/internal use (this project's current, explicit posture),
  not for untrusted multi-tenant deployment.
- **Rule extraction / SHADOW→ENFORCE lifecycle status is unconfirmed** --
  no code for this was found in this session's review. Worth checking
  whether it exists at all before treating it as an "MCP gap" specifically.

## Test scripts included

Each one documents exactly what it does and doesn't verify (most are
honestly stubbed around real network walls this dev sandbox couldn't
reach -- Supabase, Voyage, and your LLM panel providers -- re-run them on
real infra to close that gap):

- `test_apply_change_set_live.py`
- `test_tasks_extension_live.py`
- `test_propose_synthesis_live.py`
- `test_solve_task_live.py`
- `test_orphan_cleanup_live.py`
- `test_detect_conflict_trigger_live.py`
- `test_submit_approval_live.py`
- `cleanup_orphaned_debate.py` -- utility, not a test
- `diagnose_panel_connectivity.py` -- utility, not a test
