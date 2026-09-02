# StealthLab MCP Server — v1

Exposes StealthLab's bi-temporal knowledge/task graph, debate-based conflict
resolution, procedure lifecycle, Implementation Registry, and a
retrieval-grounded coding agent as 20 MCP tools.

## Setup

1. `pip install -r requirements.txt --break-system-packages` (or `uv run
   --with-editable .` for the Inspector, which picks up `pyproject.toml`
   automatically). Note: `tree-sitter-language-pack` is a real dependency
   introduced by `find_best_way` -- make sure it's actually installed, not
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
   `backend/` -- `find_best_way` imports `Agent`/`RepoSandbox` from there.

## The 20 tools

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
| `find_best_way` | Retrieval-grounded coding agent against a real repo on disk | Yes -- to the filesystem, not the graph |
| `search_procedures` | Find procedures applicable to a task/state -- lookup only, nothing executes | No -- read-only |
| `get_procedure` | Fetch one procedure's full current detail by its stable handle | No -- read-only |
| `check_applicability` | Is this NAMED procedure applicable right now, given this state? | No -- read-only |
| `report_execution` | Report a real execution outcome for a NAMED procedure | Yes -- appends evidence/verification stats |
| `submit_procedure` | Submit a new candidate procedure | Yes -- lands as `system_pending_review` by default |
| `decide_procedure` | The real human sign-off action for a candidate procedure: approve/reject | **Yes, gated** |
| `reproduce_procedure` | Re-run an EXISTING procedure's own steps against a real repo (optionally a different, transfer-tier repo) to test whether it still reproduces its claimed result | Yes -- appends reproduction evidence |
| `resolve_implementation` | Which concrete, durable implementation should satisfy this task node? | No -- read/resolve only (as a standalone tool call; see the wiring note below the table) |
| `inspect_implementation` | Fetch one durable implementation row by id | No -- read-only |
| `list_task_implementations` | Every implementation linked to a task_node | No -- read-only |
| `get_implementation_capability` | Capability estimate for one durable implementation | No -- read-only |

### Implementation Registry is now wired into the real hot path (2026-09-02)

`resolve_implementation` used to be reachable only as its own standalone MCP
call. As of this pass, `_bind_plan_to_registry()` in `server.py` calls
resolve→bind internally at all four real production call sites that compile
a plan -- `find_best_way`'s tier-1 lookup, plan-only mode, and tier-2, plus
`reproduce_procedure` -- after `compile_plan()` and before
`persist_compiled_plan()`, via the existing `procedures.migrated_from_task_
node_id` link (no new column). A plan-pinning guard checks `find_plan_for_
task(procedure_row_id, task_description)` before any resolve, so replaying
an already-bound plan reuses it verbatim instead of re-resolving to a newer
implementation registered since.

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
`propose_synthesis`/`submit_approval`/`decompose_task`/`find_best_way`, since
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
`find_best_way`'s `repo_path` is caller-controlled and `apply_change_set` is an
**ungated write** (see "Known v1 limitations" below); a bearer token gates
*who* can call these tools, it does not make either tool safe against
*anyone* holding a valid token. Treat this as a way to reach the server from
another process/machine you already trust, not as a public deployment.

### Identity: what gets attributed on `approved_by`/`created_by`/`author`

**Default posture (no extra config): every caller looks the same.** The
bearer token below gates *whether* a caller may reach the server at all; it
does not by itself distinguish *which* caller is calling. Every write-path
tool (`decide_procedure`, `submit_approval`, `apply_change_set`, etc.)
attributes to a caller-supplied, self-asserted parameter (`approver_id` and
similar) unless a real identity resolves -- fine for local/single-user use,
but in a real shared deployment any caller holding the one shared token can
claim to be anyone via that parameter.

**Real per-caller identity, when you want it: configure OIDC.** Set
`OIDC_ISSUER` + `OIDC_AUDIENCE` (and optionally `OIDC_JWKS_URL`) in
`backend/.env` -- the exact same settings fields
`app.services.authn.install_actor_middleware` already reads for the REST
app (`app/main.py`, port 8000). This server's token verifier
(`OidcAwareTokenVerifier`, `app/mcp_server/server.py`) then tries OIDC
validation FIRST on every bearer it's handed: a caller presenting a real,
signed token from that IdP gets attributed under that token's real `sub`
claim, and a caller-supplied `approver_id`/`actor_id`-shaped parameter is
ignored for trust purposes whenever a real identity resolved this way --
proven against a real Postgres, with two distinct signed identities, by
`test_mcp_server_identity_e2e.py::test_two_distinct_oidc_identities_
attribute_to_distinct_rows_and_ignore_spoofed_approver_id`. A bearer that
does not validate as an OIDC token for that issuer/audience still falls
back to the shared `STEALTHLAB_MCP_TOKEN` check below, unchanged -- OIDC
support is additive, never a replacement for the loopback gate.

No second auth model was invented for this: it is the same OIDC
validation code (`app.services.authn.validate_token_async`/`OidcConfig`/
`FetchingJwks`) the REST app already uses, reused here rather than
reimplemented.

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
never saw the task `propose_synthesis`/`find_best_way` created, and that call
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

**The repo already ships a committed `.mcp.json`** registering this server
at `http://127.0.0.1:8765/mcp`, so a fresh clone needs no `claude mcp add`
at all. It carries no secret -- the header is written as

```json
"Authorization": "Bearer ${STEALTHLAB_MCP_TOKEN}"
```

and Claude Code expands `${VAR}` from the environment at load time. The
only requirement is that **`STEALTHLAB_MCP_TOKEN` is exported in the shell
you launch `claude` from** -- putting it in `backend/.env` is enough for
the server, but NOT for the client, which never reads that file.

```bash
export STEALTHLAB_MCP_TOKEN=...   # same value the server was started with
claude   # `/mcp` should now list stealthlab as connected
```

If you would rather register it yourself instead of using the committed
file, use **`--scope local`, not `project`**:

```bash
claude mcp add --transport http stealthlab http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $STEALTHLAB_MCP_TOKEN" \
  --scope local
```

`--scope project` would have the CLI write your **literal, expanded** token
into `.mcp.json` and overwrite the env-var placeholder above -- committing
a live credential. Local scope keeps the entry in `~/.claude.json` instead.
That is also where an existing local registration lives, and a local entry
takes precedence over the committed one, so if `/mcp` shows a stale URL or
token, check `~/.claude.json` first.

`propose_synthesis` and `find_best_way` are genuinely long-running; raise the
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
`find_best_way(task_description, repo_path)` -- internally does its own
retrieval grounding, no multi-step governance loop needed.

## Getting collector traces into Postgres

None of the 20 tools above load `.claude/traces/*.jsonl` collector files into
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
  workflow specs), `POST /v1/traces` (OTel-shaped agentic workflow trace
  ingestion), and `POST /v1/admin/failure-routes/process` (real production
  consumer for `fetch_route_queue()`, landed 2026-09-02) are all real,
  working REST endpoints, just not MCP-wrapped -- going around the MCP
  server directly is required to reach any of them.
- **`find_best_way`'s Tasks-extension backing store is in-memory.** Task state
  doesn't survive a server restart and doesn't work across multiple server
  replicas. Fine for single-process use, not for production multi-replica.
- **`repo_path` in `find_best_way` is caller-controlled.** `RepoSandbox` prevents
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
- `test_find_best_way_live.py`
- `test_orphan_cleanup_live.py`
- `test_detect_conflict_trigger_live.py`
- `test_submit_approval_live.py`
- `cleanup_orphaned_debate.py` -- utility, not a test
- `diagnose_panel_connectivity.py` -- utility, not a test
