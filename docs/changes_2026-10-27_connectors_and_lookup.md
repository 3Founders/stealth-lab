# 2026-10-27: connectors, autostart, task-shaped lookups

What changed in this session, why, how it was checked, and what is still unproven.
Commits on `origin/main`: da673169, 6a92dc2a, 2ef9d8ed, 23551627, e45fe813 (and this one).

## 1. claude.ai connector guide (da673169)
- `docs/claude_web_connector.md`: server env (`STEALTHLAB_MCP_PUBLIC_URL`, `SUPABASE_PROJECT_URL`,
  `SUPABASE_JWT_AUDIENCE`, `DATABASE_URL`), Supabase OAuth 2.1 settings, `curl` checks, the
  Settings -> Connectors -> Add custom connector steps, limits.
- `packaging/npm/README.md`: one line pointing to it.
- No code changed: the server already has OAuth 2.1 / RFC 9728 metadata (`backend/app/mcp_server/oauth_resource.py`).
- NOT verified: the claude.ai handshake. No public deployment existed, and a tunnel could not be opened
  (blocked by the auto-mode classifier, then by the college network blocking QUIC).

## 2. Autostart for a down local server (6a92dc2a)
- New `packaging/npm/lib/ensure_server.mjs` and a `SessionStart` hook `kell hook ensure-server`
  (registered with the capture hooks in `capture_hook.mjs`, routed in `bin/kell.mjs`).
- When Claude Code starts and the configured URL is loopback and nothing answers, it starts
  `backend/scripts/start_mcp_local.ps1` detached and hidden. Never touches a healthy server (the script
  replaces the port holder), never starts a hosted URL, `STEALTHLAB_SERVER_AUTOSTART=off` disables.
- Windows + backend checkout only (or `STEALTHLAB_SERVER_START`). Tests: `test/ensure_server.test.mjs` (4).
- NOT verified: a real start. On a spare port the hook fired and logged the spawn, but the server did not
  answer within ~80 s; the cause was not diagnosed (running the script directly was blocked).

## 3. Lookup gets 4000 characters, not 1500 (2ef9d8ed)
- `FIND_WAYS_QUERY_MAX = 4000` in `hook.mjs`, used by the Claude Code/opencode hook and the Cursor hook.
- Triage keeps 1500: it only judges whether to look up, and costs a model call per prompt.

## 4. Long prompts keep their end; the unit is a task (23551627)
- `backend/app/utils/condense.py` + JS twin `fitQuery`: an over-limit query keeps its head and its last 500
  characters with a marker, exactly `limit` long. Used for triage (1500), goal text search (2000, three
  places in `goal_resolution.py`), the task-code embedding (4000) and the hook. Still a cut: the middle is lost.
- The MCP surface now says the unit is a TASK (goal + the context to do it), not a message: call
  `find_ways` at the start of every new task, again when the task changes, one call per task in a message,
  none for a follow-up on the same task; the query is a self-contained statement, not a paste. Edited in
  `_V1_INSTRUCTIONS` and the `find_ways` description (server.py), `FIND_WAYS_GUIDE` (resources.py) and the
  AGENTS.md block (`instructions.mjs`). Trimmed to fit the existing budgets (find_ways <= 900 tokens,
  block < 1800 chars).
- Docs: the same prompt to paste as ChatGPT Custom instructions / claude.ai Project instructions
  (`claude_web_connector.md`) and a full opencode test guide (`opencode_test.md`).
- Tests: `backend/tests/test_condense.py` (4); backend 117 passed across condense, triage route,
  tool-description lint and progressive discovery.

## 5. The lookup runs on nearly every prompt (e45fe813)
- Before: a triage verdict of "no lookup needed" skipped the lookup (about one prompt in three passed).
- Now: triage is still asked (dispatch and routing read its verdict) but does not skip. "No lookup
  needed" prompts get a plan-only lookup: it feeds the model plan and the record, nothing is injected.
  `STEALTHLAB_HOOK_TRIAGE_SKIP=on` restores skipping. Slash commands and harness messages are still skipped.
- Tests: the old skip test now sets the switch; a new test checks the default runs `find_ways`.

## Measured
- npm package: 702 tests, 699 pass, 0 fail (3 skipped by the suite).
- Three direct `find_ways` queries (pytest fix, FastAPI rate limiter, Next.js dark mode) all returned
  `no_match`: the local library is nearly empty, so the model plan has no evidence yet.
- Anonymous callers cannot run `call_model` (direct or via `use_tool`): `requires scope 'execution:run'`.

## Not done / limits
- Claude web and ChatGPT: need a public HTTPS server and sign-in; no hooks, so only the pasted prompt and
  the tool description make the model call `find_ways`. Their "cheaper model" path is the server-side
  `call_model(model="auto")`, untested.
- A hook cannot switch the main session's model; the plan binds subagents only. Cursor ignores a model on
  its Task tool and plans only with `STEALTHLAB_CURSOR_CANDIDATES`.
- Claims selection is still keyword/unit matching (first 200 claims server-side); relevance ranking was
  designed but not built.
- A model follows the task prompt as a habit; the server cannot tell a good task statement from a bad one.
