# stealthlab-connect

Installable entry points that let an external agent connect to StealthLab in
minutes. This package adds **zero business logic** — every real capability is
imported from the backend checkout (`backend/app/services/trace_collector.py`,
`backend/app/mcp_server/server.py`) at runtime; this wrapper only resolves
paths, loads config, and provides console commands.

Two surfaces:

| Command | What it launches |
|---|---|
| `stealthlab-mcp-server` | The StealthLab MCP server (29 tools over the bi-temporal knowledge/task graph) — Streamable HTTP on loopback by default, or `--stdio` |
| `stealthlab-trace-hook` | Claude Code hook command: reads one hook JSON payload on stdin, redacts it (`trace_redaction`), appends it to the local collector file (`trace_collector.append_event`) |
| `stealthlab-status-page` | The minimal status surface: one read-only page listing episodes -> claims -> procedures with capability scores and evidence trails (board item P2) |
| `stealthlab-public-board` | The public scoreboard generator: static markdown + HTML page from a real-arms sweep's results + spend JSONL, power-analysis footer with discordant pairs beside every p-value (board item P5) |

## Public scoreboard generator (`stealthlab-public-board`)

```bash
# after a real-arms sweep wrote experiments/harness/real_arms_results.jsonl:
stealthlab-public-board                  # writes ./public_scoreboard.md + .html
stealthlab-public-board --out-dir site/  # anywhere you like
```

Reads the sweep's two artifacts — `real_arms_results.jsonl` and its spend
ledger (resolved automatically: `<results stem>_spend.jsonl`, then
`real_arms_spend.jsonl`, then `real_spend.jsonl`; override with `--spend`) —
and emits a **static** page in both formats. No server, no database, no
backend imports: statistics come from `experiments/harness/` as shipped
(scoring, McNemar exact test, power analysis, spend-log aggregation), so the
public page cannot drift from the terminal scoreboard.

Structural guarantees:

- every `exact-p=` on either page travels with its discordant-pair counts —
  comparison lines are rendered by harness `mcnemar_power.format_pair()`,
  whose signature makes a bare p-value unrepresentable;
- a POWER-ANALYSIS FOOTER section on every page;
- a SPEND line (attempts / billed / failed / tokens / cost, 429 count,
  per-arm billed cost); a missing ledger renders an honest-absence note,
  never a fabricated zero-cost claim;
- `Generated:` UTC timestamp plus source-file provenance;
- tasks excluded from paired stats (missing/invalid arm episode or runner
  error) are counted and disclosed; comparisons under the RUN #1 floor of 6
  discordant pairs carry a small-n caveat.

Refuses to run (exit 2) when the results file is missing or empty — a public
page is never generated from absent data. Offline tests:
`tests/test_public_board_offline.py`.


## Status page (`stealthlab-status-page`)

```bash
stealthlab-status-page                # serves http://127.0.0.1:8766/
```

Requires `DATABASE_URL` in `backend/.env` (same as the MCP server's HTTP
mode). Read-only in both senses: every SQL statement is a SELECT scoped
through `app/services/access.py`'s predicate builders, and the only
backend code used is imported as shipped — no backend edits, no new auth
surface (it reuses `authn.py`'s middleware + boot guards exactly like
`app/main.py`; pass-through while OIDC is unconfigured).

What you get:

- `/` — one HTML/JS page (`status_page.html`): episodes -> claims ->
  procedures, expandable cards, capability badges
  (`L<level> <label> · P̂=<Wilson lower bound>`) plus routing verdicts,
  claim provenance chains (`claim_sources` -> observations -> extractor
  stamps), episode link targets, and per-target evidence-trail tables
  fetched lazily from `/api/evidence/{claim|procedure}/{id}`.
- Deep links point at the main API's `GET /v1/graph/{id}`; override the
  target with `STEALTHLAB_API_BASE` if the API does not live at
  `http://127.0.0.1:8000`.

Honest notes:

- Capability scores are computed by the REAL engine
  (`app/services/procedure_extraction/capability.py::compute_capability`)
  over each procedure's recorded outcome evidence (supports-direction
  `execution_result`/`reproduction` rows — the same population as
  `procedure_evidence_stats`). Verification-plan / completed-review gates
  are reported as unclaimed until anything stores them, so trust tiers
  above "reproduced" cannot appear from statistics alone.
- Against a pre-migration-24 database (no `evidence` table yet — the
  known shared-instance drift) the page degrades with a named banner and
  level-0 scores instead of faking numbers or dying.
- Loopback-only posture; there are no write endpoints to gate.

## Prerequisites

- Python >= 3.12
- A checkout of this repository (the backend is imported from disk, not
  vendored — see "How backend resolution works" below)
- A reachable Postgres for `DATABASE_URL` (the MCP server opens its pool on
  startup; the trace hook never touches the database)
- Provider keys per `backend/README_MCP_SERVER.md` (at minimum
  `VOYAGE_API_KEY`; debate tools need panel keys)

## Install

From the repository root:

```bash
pip install -e ./backend     # stealthlab-backend itself (app/ package + all deps incl. mcp[cli]>=2.0.0)
pip install -e ./packaging   # this package's thin entry points
```

Verify both commands exist:

```bash
stealthlab-mcp-server --help
stealthlab-trace-hook --help
```

## Configure

Create/edit `backend/.env` (gitignored):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# then in backend/.env:
# DATABASE_URL=postgresql://...
# VOYAGE_API_KEY=...
# STEALTHLAB_MCP_TOKEN=<generated value>
```

`STEALTHLAB_MCP_TOKEN` is required for HTTP mode (the server fails fast
without it); stdio mode bypasses auth by protocol design and does not need it.

## Smoke test A — MCP server over HTTP (Claude Code / any Streamable HTTP client)

```bash
stealthlab-mcp-server                      # serves http://127.0.0.1:8765/mcp
```

Expected on startup (stderr): the connection hint block, then uvicorn logs.
Auth must actually be enforced before connecting anything:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp
# expect: 401

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer $STEALTHLAB_MCP_TOKEN"
# expect: NOT 401
```

Connect Claude Code:

```bash
claude mcp add --transport http stealthlab http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $STEALTHLAB_MCP_TOKEN" --scope local
claude mcp list        # expect: stealthlab ✔ Connected
```

Cheapest-first tool order: `retrieve_precedent` → `get_procedure` /
`check_applicability` (fast reads) → everything else costs real API spend.
`propose_synthesis` / `find_best_way` are long-running; raise client
timeouts (see `backend/README_MCP_SERVER.md`).

## Smoke test B — MCP server over stdio (no token)

```bash
stealthlab-mcp-server --stdio
```

Register directly, e.g.:

```bash
claude mcp add stealthlab -- stealthlab-mcp-server --stdio
```

Stdio still requires `DATABASE_URL` at startup (pool creation) but skips all
bearer-token handling.

## Smoke test C — trace collector hook (fully offline, no database)

Pipe one realistic PostToolUse payload through the CLI:

```bash
echo '{"session_id":"smoke-1","hook_event_name":"PostToolUse","cwd":"/tmp","tool_name":"Bash","tool_input":{"command":"export TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"tool_output":"ok"}' \
  | STEALTHLAB_TRACE_DIR=/tmp/sl-smoke stealthlab-trace-hook --verbose
echo $?    # expect: 0
```

Then inspect `/tmp/sl-smoke/smoke-1.jsonl`: exactly one line; the token reads
`[REDACTED:github_token]`; the record carries `dedup_key`, `sequence`, and a
`_redaction.patterns_matched` audit list.

Real registration goes in `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      { "hooks": [ { "type": "command", "command": "stealthlab-trace-hook" } ] }
    ]
  }
}
```

Collector file defaults to `$CLAUDE_PROJECT_DIR/.claude/traces/<session_id>.jsonl`,
overridable via `--file`, `--trace-dir`, or `$STEALTHLAB_TRACE_DIR`. The CLI
**always exits 0** — exit code 2 would block the user's actual tool call, so
every failure path degrades to a stderr note instead.

## Library use

```python
from stealthlab_connect import (
    append_trace_event, build_event, collect_payload,
    get_backend_root, load_mcp_server_module, load_trace_collector_module,
)
```

`load_mcp_server_module()` returns the real `app.mcp_server.server` module
(`.server` = MCPServer instance, `.app` = ASGI app for `uvicorn`).

## Offline tests (no database needed)

```bash
python -m pytest ../packaging/tests -q     # from backend/, or:
python -m pytest packaging/tests -q        # from repo root
```

Coverage: backend discovery/explicit-path errors, collector round-trip +
redaction + dedup-key determinism + drop-count compaction accounting, hook CLI
fail-safe behavior (malformed JSON / missing session_id / empty stdin all exit
0), and the MCP server module imported without any DB connection (tool roster,
token verifier accept/reject).

## How backend resolution works

The package locates the backend at call time, in priority order:

1. explicit path (`--backend-root` / first function argument) — invalid values
   raise immediately, never silently fall back
2. `$STEALTHLAB_BACKEND_ROOT`
3. auto-discovery relative to the installed package and the current directory

It then validates the markers (`app/mcp_server/server.py`,
`app/services/trace_collector.py`) and fronts that directory on `sys.path`.
If some *other* `app` package was already imported first, you get an error
rather than silent wrong-module binding — import `stealthlab_connect` before
touching `app.*`.

## Honest scope, inherited limitations stated plainly

- **Loopback-only deployment posture.** The token gates who can call.
  Knowledge-graph mutation is gated (only `submit_approval` /
  `decide_decomposition`, each requiring a persisted proposal + audit row;
  the ungated `apply_change_set` tool was removed post-freeze,
  `v1-final-2026-09-03.1`). `find_best_way`'s `repo_path` is still
  caller-controlled — same accepted-for-now posture as
  `backend/README_MCP_SERVER.md`.
- **Single process only.** Tasks-extension state is in-memory; run exactly one
  server process (uvicorn default here).
- **Hook payload mapping mirrors `backend/scripts/hook_wrapper.py`** (field
  mappings marked as unconfirmed there are equally unconfirmed here);
  `build_event` is unit-tested against synthetic documented-schema payloads,
  not a live Claude Code process.
- **This package vendors nothing.** It breaks if the backend checkout is moved
  or deleted — by design; there is no second copy of the logic to drift.
