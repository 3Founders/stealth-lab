# Goal-first routing: phases, sub-milestones, and file ownership

Status: active plan, 2026-11-11. Phase 1 is complete except P1-09 (blocked on Hinoki's API base/schema); Phase 2 is
complete; Phase 3 is complete (P3-01…P3-07).
This document is the map. The GitHub issue is the lock (see [Claiming](#claiming-a-sub-milestone)).
Current state and open review findings: [handoff_goal_router_2026-11-11.md](handoff_goal_router_2026-11-11.md).

## If you are an agent: start here

The only prompt you need is:

> read docs/goal_router.md and complete it

"Complete it" means: work the status table top to bottom, one unclaimed milestone at a time, in dependency order, and
push each one to `main`. Do not wait for another instruction. Do not stop after one milestone unless you are blocked.

### The work loop

1. Read this whole document.
2. `git pull --rebase origin main`.
3. Pick the first row in [Status](#status) that is `open` (or `blocked` and now unblocked). Start with **P1-02**, then
   the rest of P1, then P2, then P3.
4. Claim it: open the issue, assign yourself, branch `goal-router/<ID>-<slug>`, and edit your `Owner` cell.
5. Build only the files under that milestone's "Files you own". Run its **Manual check** and paste the output in the PR.
6. Commit, `git pull --rebase origin main`, `git push origin main`. Then update your row to `done` and push that too.
7. Pick the next row. Repeat until every row is `done` or `blocked` with a reason in the issue.

### Push cadence (required)

- Push after **every milestone**, and at least **every 45 minutes** while a milestone is in progress. Do not sit on a
  local branch.
- Commit small. One milestone may be several commits, but each must leave `main` runnable.
- Always `git pull --rebase origin main` immediately before `git push origin main`.
- Never `git push --force`, never rewrite `main`, and never commit `.scratch/`, secrets, or files owned by another
  milestone.
- If the push is rejected: pull `--rebase`, resolve only your own files, retry. If the conflict is in a file you do not
  own, stop and comment on that milestone's issue.

### When you are blocked

- Missing credential or provider key: record it in the issue and move to the next unclaimed milestone. Do not invent a
  bypass.
- A file you need is owned by an open milestone: comment on that issue. Do not edit it.
- `server.py` is held by P1-04 or P3-06: wait for it to merge, or pick a Group A milestone.
- Ambiguous requirement: choose the fail-closed behavior and note the decision in the PR.

### Definition of complete

The run is complete when every row in [Status](#status) is `done` or `blocked` with a reason, and the three phase exit
criteria hold:

- Phase 1: `recommend_goal.py` routes the security and web prompts correctly, `--call` allows R0 and refuses R3 without
  a certificate, and the audit chain verifies.
- Phase 2: 60 tasks across 6 goals are compared tool vs procedure vs baseline, with paired deltas and confidence
  intervals.
- Phase 3: ranking is driven by verified reuse, trials are funded and capped, and bad tools retire.

## What this builds

Routing is **goal first**:

```
task / prompt
  → Goal            what the person is trying to do            (goals)
  → Way             how it is done, as ordered steps            (procedures)
  → Tool            what executes a step                        (tool_cards)
  → Gate            whether that call may run at all            (policy + intent + audit)
```

Tools are implementations, not the routing key. A tool card is the **binding target** for a way's step. The
goal-first router (`backend/app/tool_routing/goal_router.py`) already returns goals with their ways and mapped tools;
`tool_card_goals` (db/186) already links 48,124 cards to 25 goal-taxonomy Goals.

Two things are still missing, and they are the reason for this plan:

1. **Enforcement.** Relevance decides who should win; a deterministic, fail-closed gate decides whether anyone may run.
   The pieces now exist — `tool_policies`, `intent_certificates` and the signed `tool_call_audit` (P1-05/06/07) — but
   they are not yet wired into `use_tool` (P1-04), and there is no approval surface (P1-11).
2. **Evidence.** We do not yet measure whether a tool actually does the job **better than the procedure it would
   replace**. Phase 2 is a Python harness that runs the same task through both arms and compares the results. Phase 3
   turns that evidence into ranking, trials and retirement.

**Testing is manual.** There is no automated test suite in this plan. Every milestone ships with a command you run
yourself and an expected result. Phase 1 is tested directly with the recommend-goal script (`P1-02`), which is why it is the
first thing to build.

Non-goals for this plan: cash payouts to providers, a public marketplace, and autonomous offensive security. Trial
credits and pricing are scoped in Phase 3 but ship after the gate and the comparison harness.

## Current state (verified 2026-11-11)

| Piece | State | Where |
|---|---|---|
| Goal index and search | exists | `goal_search_index`, `goal_names`, `app/services/goals.py` |
| Goal-first router | exists; `GOAL_RANKING=trust` ranks a goal's tools by verified reuse (P3-03) | `app/tool_routing/goal_router.py` |
| Goal CLI | exists, read-only | `backend/scripts/search_goals.py` |
| Recommend-goal CLI | **built (P1-02)** | `backend/scripts/recommend_goal.py`; `search_goals.py` is a wrapper |
| Security subgoals | **built (P1-01)** | `app/tool_routing/goals.py`, `db/187_tool_goals_security.sql` |
| Manual pins | **built (P1-03)** | `map_tool_cards_to_goals.py --pin`, `report_goal_coverage.py`; Exa pinned to `web` at 9.0 |
| Policy store | **built (P1-05)** | `tool_policies` (db/188), `app/tool_routing/policy.py`, `scripts/check_policy.py` |
| Intent certificates | **built (P1-06)** | `intent_certificates` (db/189), `certificates.py`, `scripts/issue_cert.py`, `scripts/check_cert.py` |
| Signed audit | **built (P1-07)** | `tool_call_audit` (db/190), `audit.py`, `signing.py`, `scripts/verify_audit_chain.py` |
| Card → goal mapping | exists, 125,656 rows | `db/186_tool_card_goals.sql`, `scripts/map_tool_cards_to_goals.py` |
| Ways from tools | **materialized: 56,741 one-step procedures (the runnable library)** | `scripts/materialize_tool_procedures.py --all`; 20,079 cards. `procedure_tool_cards` (db/199) binds each way's step to its card (56,741 mappings); the router builds the Ways from the **ranked** tools, so the first Way is the first tool (trust under `GOAL_RANKING=trust`). The other 68,916 card→goal edges have no connector/tool |
| Procedure approval tiers | **submitted < proposed < approved** (db/201) | `scripts/review_procedures.py`; the router labels each Way's tier, the procedure runner only executes `approved`. The generated tool-card ways are `submitted`; Exa is promoted to `approved` |
| Tool gateway | exists, phase 1 | `app/tool_routing/gateway.py`, `_gateway_call` in `app/mcp_server/server.py` |
| OAuth / consent | exists, off by default | `tool_oauth_*`, `oauth_client.py`, `TOOL_GATEWAY_OAUTH` |
| Outcome recording | partial | `tool_observations` (db/162), `store.record_outcome` |
| Exa | **adapter built (P1-08)**, card + mapping | `registry:ai.exa/exa`, goal `web`, kell's key |
| OSV | **keyless, policy + pin (db/198)** | `registry:io.github.cyanheads/osv-advisory-mcp-server`, CVE subgoal, `osv_query_package` |
| Hinoki | **blocked (P1-09)**: inference API known, no scanner API | `https://inference.hinoki.security/v1`, model `hinoki-offensive` (#26). An LLM, so `scan_repository` needs a different design; no adapter or card registered, and no call without consent |
| Policy / intent / signed audit | **built (P1-05/06/07)** | `tool_policies`, `intent_certificates`, `tool_call_audit` |
| Gate core | **built (P1-04)** | `gate.py`, `TOOL_GATE=off / shadow / enforce`, wired in `_gateway_call`/`use_tool` |
| Approvals | **built (P1-11)** | `tool_approvals` (db/196), `approvals.py`, `scripts/approve_tool.py`, `/v1/tools/approvals` |
| Tool arm | **built (P2-03)** | `scripts/compare/tool_runner.py` |
| Tool-vs-procedure comparison | **built (P2-01…P2-07)**; reports in `docs/compare_*.md` | `scripts/compare/*`. The procedure arm now runs a tool-backed one-step Way through the same gate as the tool arm; the existing reports predate the materialized ways, so re-run them |
| Trust, trials, lifecycle, verification, pricing | **built (P3-01…P3-07)** | `trust.py`, `trials.py`, `lifecycle.py`, `verification.py`, `pricing.py`, `scripts/tool_market_report.py` |
| Per-goal ranking | **materialized (db/200)** | `goal_tool_rankings`; `scripts/refresh_goal_rankings.py`. The router reads one row per card and applies only the trial overlay; a Goal with no rows falls back to the live ranker |
| Route surface | **built** | `GET /v1/goals/route` (`app/api/goals.py` + `app/tool_routing/route_api.py`) and its MCP twin (`room_agent_routes.py`); `/route` on the website and in the desktop app. The tool tier now ranks a Goal's cards by the request (`goal_router._query_hits`: BM25 + vectors + reranker, 4 s bound, 2 min cache), and a no-Goal request falls back to the tool tier alone. `db/203` adds the HNSW index that makes the candidate scan ~2 s |

Known local constraint: goal semantic/FTS search warns that the search group's members are unconfigured. Exact
`goal_names` lookup works and hydrates from the home shard; the K000 full-text fallback in `goal_router.py` keeps the
25 taxonomy Goals searchable. Do not "fix" this by copying Goals onto K000.

### Try the router today

The entry point is `recommend_goal.py` (`search_goals.py` is a thin wrapper):

```powershell
cd backend
python scripts/recommend_goal.py "check what the page looks like after login" --explain
python scripts/recommend_goal.py "scan this repository for vulnerabilities" --json
python scripts/recommend_goal.py "search the web for the latest Postgres release" --call
python scripts/map_tool_cards_to_goals.py --dry-run --report
```

`GOAL_RANKING=trust` switches the tool order from goal fit to verified reuse (P3-03); `TOOL_GATE=enforce`
turns the gate from shadow into the answer. Neither is on by default yet.

The same answer is now on the wire and in the product:

```powershell
# the website and the desktop app both read this (read-only; nothing is called)
curl "http://127.0.0.1:8000/v1/goals/route?q=send%20a%20slack%20message&k=5"
```

The website page is `/route` (`routing_kell_frontend/app/route/page.tsx`); the desktop page is
`/route` (`desktop/app/route/page.tsx`, opened from the magnifier in the top bar). The desktop
route is served by the MCP server (`room_agent_routes.py`), so it uses the app's existing sign-in.

## Ground rules for parallel agents

1. **One agent per sub-milestone.** The milestone ID is the unit of ownership, not the phase.
2. **Only edit files listed under your milestone's "Files you own".** If you need a change in a file owned by another
   milestone, open a small PR against that milestone's owner or comment on its issue. Do not edit it "just once".
3. **`backend/app/mcp_server/server.py` is serialized.** Exactly one open milestone may own it at a time: `P1-04`, then
   `P3-06`. Everything else integrates through a module the gate imports. This is the main collision point.
4. **Migrations are allocated, not chosen.** Take the number in your milestone. If it is taken, stop and comment.
5. **Ship behind a flag.** New behavior defaults to shadow/off until its manual check passes.
6. **Never weaken a fail-closed default to make a demo pass.** A denied call is a correct outcome.
7. **Do not add static trust as a ranking signal.** Stars and `verified` are metadata. Ranking comes from verified
   reuse (Phase 3), not from a badge.
8. **No test files.** Verify by running the recommend-goal script or the milestone's command and pasting the output in the PR.
9. **Push to `main` periodically.** After every milestone, and at least every 45 minutes while one is in progress. See
   [If you are an agent](#if-you-are-an-agent-start-here).

### Claiming a sub-milestone

1. Open a GitHub issue titled `goal-router: <ID> <short name>` (for example `goal-router: P1-05 policy store`).
2. Assign yourself and paste the issue link in your PR description.
3. Branch: `goal-router/<ID>-<slug>`.
4. Edit only the `Owner` cell for your row in the status table below (one-line change; do not reformat the doc).
5. Open a draft PR early. Merge when the manual check passes and no owned file is left dirty.

If two agents claim the same ID, the earlier issue wins. The later agent picks another unclaimed ID.

## Frozen interfaces

These are contracts between milestones. Do not change them without updating this section and every consumer.

```python
# app/tool_routing/gate.py  (owned by P1-04)
@dataclass
class ToolCall:
    owner_id: str | None
    task_id: str | None
    goal_id: str | None
    server: str
    tool: str
    arguments: dict
    data_class: str = "PUBLIC_SOURCE"
    agent: str = "kell"
    cert_id: str | None = None

@dataclass
class Decision:
    effect: str            # "allow" | "deny" | "approve"
    reason: str            # stable code, e.g. "policy:r3_requires_approval"
    risk: str              # "R0".."R4"
    cert_id: str | None
    approval_id: str | None
    constraints: dict      # max_calls, egress, timeout_s, sandbox
    shadow: bool = False   # true when `shadow` mode let a non-allow through

async def authorize(pool, call: ToolCall) -> Decision: ...
```

```python
# app/tool_routing/policy.py  (P1-05)
async def evaluate(pool, call: ToolCall, cert) -> tuple[str, str, dict]: ...
# -> (effect, reason, constraints); effect is one of allow/deny/approve

# app/tool_routing/certificates.py  (P1-06)
async def verify(pool, cert_id: str, call: ToolCall) -> tuple[bool, str]: ...

# app/tool_routing/audit.py  (P1-07)
async def record(pool, call: ToolCall, decision: Decision, *,
                 status: str, latency_ms: int | None = None,
                 cost_usd: float | None = None, result_hash: str | None = None) -> str: ...
```

```python
# backend/scripts/compare/contracts.py  (P2-01; imported by P2-02..P2-07)
@dataclass
class RunRecord:
    run_id: str
    task_id: str
    arm: str               # "procedure" | "tool" | "baseline"
    goal_id: str | None
    procedure_id: str | None
    card_id: str | None
    started_at: str
    latency_ms: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    status: str            # "pass" | "fail" | "error"
    score: float
    artifact_hash: str | None
    error_class: str | None
    trace_ref: str | None
```

```python
# app/tool_routing/runs.py  (P1-10)
async def start_run(pool, *, task_id: str, arm: str, goal_id: str | None,
                    procedure_id: str | None, card_id: str | None) -> str: ...
async def finish_run(pool, run_id: str, record: dict) -> None: ...
```

## Phase 1 — Foundation: resolve, gate, bind, record

**Goal:** a prompt lands on the right Goal, the Goal shows its ways and tools, every tool call passes one gate, and every
call and run is recorded. Exa runs without a user sign-in. Hinoki is present but R3-gated.

**Start with P1-02.** It is the recommend-goal script you will use to manually test every other Phase 1 milestone. The rest
can be built in parallel against the frozen interfaces.

### Phase 1 manual test loop

Once P1-02 is merged, this is how the phase is tested. Run it after each milestone.

| Prompt | Expected Goal | Expected tool |
|---|---|---|
| `search the web for the latest Postgres release` | Search the web or read a web page | Exa |
| `scan this repository for known CVEs in dependencies` | security dependency/CVE subgoal | OSV or Hinoki |
| `scan this repository for vulnerabilities` | security / pentest subgoal | Hinoki |
| `run the tests and show the failing ones` | run code / CI subgoal | sandbox or CI tool |
| `open the page, log in and screenshot the dashboard` | Drive a real browser | browser tool |

For each row:

```powershell
cd backend
python scripts/recommend_goal.py "<prompt>" --explain
python scripts/recommend_goal.py "<prompt>" --json
```

`--explain` must show which leg matched (`exact`, `fts`, `vector`), the Goal score, and the mapped tools in rank order.
If a row fails, the milestone that owns that behavior is named in the table above.

### P1-01 — Security and intent Goals

**Builds:** the subgoals a security prompt must land on, so "scan dependencies for CVEs" does not collapse into a generic
"security" bucket.

Subgoals: `security_dependency_cve`, `security_secret_scan`, `security_sast`, `security_auth_review`,
`security_pentest`, plus triage markers for each. Keep the existing 25 goal ids stable; add, do not rename.

**Files you own**

- `backend/app/tool_routing/goals.py`
- `backend/db/187_tool_goals_security.sql`

**Manual check**

```powershell
python scripts/recommend_goal.py "scan this repository for known CVEs in dependencies" --explain
python scripts/recommend_goal.py "find hardcoded secrets in this repo" --explain
python scripts/recommend_goal.py "review the auth flow for privilege escalation" --explain
```

Each prompt must put its subgoal in the top 3, and the original 25 goals must still resolve as before.

**Do not touch:** `goal_router.py`, the mapper, `server.py`.

### P1-02 — Recommend-goal script (the Phase 1 harness)

**Builds:** the single command that resolves a prompt and, optionally, calls the chosen tool through the real gate. This
is the manual test driver for Phase 1. It must not add a bypass: `--call` uses the same `authorize()` and gateway path
as `use_tool`.

`backend/scripts/recommend_goal.py`:

```
recommend_goal.py "<prompt>" [--k 5] [--json] [--explain] [--semantic]
                           [--tools-only] [--call] [--tool NAME]
                           [--arg key=value ...] [--data-class PUBLIC_SOURCE]
                           [--owner dev-local] [--cert CERT_ID] [--task-id TASK_ID]
```

Behavior:

- Default: print Goal, score, matched-by, ways (name, verification state, availability) and tools (rank, card id,
  server, why it is offered).
- `--explain`: one line per matching leg, then the ranking inputs (goal fit, trust if Phase 3 exists, risk tier).
- `--json`: the full `GoalHit` list, machine-readable.
- `--call`: resolve, pick the top tool for the top Goal (or `--tool`), build a `ToolCall`, run `authorize()`, and only on
  `allow` call the gateway. Print `REFUSED: <reason>` on deny/approve; never fall through to a direct call.
- `--call` requires `app/tool_routing/gate.py` (P1-04) and `audit.py` (P1-07). If either is missing it prints
  `gate not installed` and exits non-zero. Never add a direct-call fallback, not even for local development.
- `--owner` defaults to `KELL_DEV_OWNER` or `dev-local`; this is a development identity, not a production one.
- Keep `backend/scripts/search_goals.py` as a thin compatibility wrapper that calls the new script with the old flags.

**Files you own**

- `backend/scripts/recommend_goal.py`
- `backend/scripts/search_goals.py`
- `backend/app/tool_routing/goal_router.py`

It imports `authorize()` from `gate.py`; it does not own `gate.py`.

**Manual check**

```powershell
python scripts/recommend_goal.py "check what the page looks like after login" --explain
python scripts/recommend_goal.py "search the web for the latest Postgres release" --json
python scripts/recommend_goal.py "search the web for the latest Postgres release" --call
```

The first shows one line per leg and the Goal's ways/tools. The second prints valid JSON. Once P1-04 and P1-07 are
merged, the third prints a real answer or a clear `REFUSED`/`FAILED`, and leaves one audit row.

**Do not touch:** `goals.py`, `retrieve.py`, `server.py`, `policy.py`.

### P1-03 — Mapping v2 and manual pins

**Builds:** better card→goal coverage and a way to pin a launch tool (Hinoki, Exa) without waiting for data. Extend the
mapper with `--pin card_id goal_id score`, `--report` (coverage per goal, orphan cards), and idempotent upserts with
`source='manual'`. Manual pins must never be deleted by `--replace`.

**Files you own**

- `backend/scripts/map_tool_cards_to_goals.py`
- `backend/scripts/report_goal_coverage.py`

**Manual check**

```powershell
python scripts/map_tool_cards_to_goals.py --dry-run --report
python scripts/map_tool_cards_to_goals.py --pin registry:ai.exa/exa <web_goal_id> 9.0 --dry-run
python scripts/recommend_goal.py "search the web" --tools-only
```

The report prints cards, mappings, goals with zero cards and cards with zero goals. The pin survives a `--replace` run.

**Do not touch:** `goal_router.py`, `goals.py`, `server.py`.

### P1-04 — Gate core (the only milestone that edits `server.py`)

**Builds:** `authorize()` as the single choke point for third-party calls. It runs after kell's own builtins and
`revoke`, and before the server/card, grant, rate-limit and network paths. Modes: `off` (today's behavior), `shadow`
(compute the real decision, write the receipt, never block -- the default), `enforce` (the computed decision is the
answer). An unknown mode is `enforce`.

Fail closed: a policy/certificate error or a missing audit row denies R2+; R0/R1 may run without a certificate, R2+
requires one. A policy `approve` is not an execution: the caller must obtain an approval and call again. `use_tool`
accepts `cert` and `task_id` and passes them through; the resolver's `--call` uses the same `authorize()`.

The existing gateway checks (data class, secrets, grants, rate limits) still run after the gate; the gate does not
duplicate them. Direct kell tools (`V1_TOOLS`, `DISCOVERY_TOOLS`) are not third-party and stay outside this gate.

**Files you own**

- `backend/app/tool_routing/gate.py`
- `backend/scripts/check_gate.py`
- `backend/app/mcp_server/server.py` (serialized; hand back after merge)

**Manual check**

`check_gate.py` exercises `authorize()` without a card or network, so it works even while Hinoki is blocked:

```powershell
$env:TOOL_GATE = "off"
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local
# expect: allow, gate:off

$env:TOOL_GATE = "enforce"
python scripts/check_gate.py --server ai.exa/exa --tool web_search_exa --owner dev-local
# expect: allow (policy:allow:1, R0)
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local
# expect: deny, cert:required (R3)
python scripts/check_gate.py --server unknown --tool x --owner dev-local
# expect: deny, policy:no_rule

$env:TOOL_GATE = "shadow"
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local
# expect: allow, shadow:deny:cert:required, and the audit row records deny
```

With a certificate, an R3 call reaches `approve` (exit 3), not `allow`:

```powershell
$out = python scripts/issue_cert.py --task demo-3 --owner dev-local --risk R3 `
    --server hinoki --tool scan_repository --constraint-file ..\.scratch\hinoki_constraint.json
$cert = (($out | Select-String '^cert:').Line -split '\s+')[1]
$env:TOOL_GATE = "enforce"
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local `
    --cert $cert --task demo-3 --arg repo_url=https://github.com/org/repo
# expect: approve, policy:approve:2
```

End to end, the resolver's `--call` now passes R0 and reaches the network:

```powershell
$env:TOOL_GATE = "enforce"
python scripts/recommend_goal.py "search the web for the latest Postgres release" --call `
    --arg "query=latest Postgres release"
# expect: decision: allow (policy:allow:1), then Exa results
```

Then `python scripts/verify_audit_chain.py --since 1h` must print `ok`.

**Do not touch:** `policy.py`, `certificates.py`, `audit.py` (import them; their owners build them).

### P1-05 — Policy store

**Builds:** declarative, deterministic policy. `tool_policies` rows are keyed by server/tool/goal and carry risk tier,
effect (`allow`/`deny`/`approve`), data classes, max calls, egress allowlist, and a priority. Evaluation order: explicit
deny → the most specific enabled row → data class → risk ceiling → effect → default deny. Rate limits stay in the
gateway, not here. Ship seed rows for Exa (R0 allow) and Hinoki (R3 approve).

**Files you own**

- `backend/app/tool_routing/policy.py`
- `backend/db/188_tool_policies.sql`
- `backend/scripts/check_policy.py`

**Manual check**

`check_policy.py` calls `policy.evaluate()` directly, so it works before P1-04 wires the gate:

```powershell
python scripts/check_policy.py --server ai.exa/exa --tool web_search_exa --expect allow
python scripts/check_policy.py --server hinoki --tool scan_repository --risk-ceiling R3 --expect approve
python scripts/check_policy.py --server unknown --tool x --expect deny
python scripts/check_policy.py --server hinoki --tool scan_repository --risk-ceiling R1 --expect deny
python scripts/check_policy.py --server hinoki --tool scan_repository --data-class USER_PRIVATE --expect deny
```

Deny must win over allow, an empty allowlist must mean "all", and an unknown tool must deny.

**Do not touch:** `gate.py`, `server.py`.

### P1-06 — Intent certificates

**Builds:** task-scoped authorization. A certificate names the task, user, agent, goal, allowed servers/tools, argument
constraints, data class, risk ceiling, egress, max calls and expiry. It can only **narrow** policy. The model cannot
mint one: only `certificates.issue()` from a trusted caller can. Verify signature, expiry, revocation, and that the call
is inside the allowed set.

**Files you own**

- `backend/app/tool_routing/certificates.py`
- `backend/db/189_intent_certificates.sql`
- `backend/scripts/issue_cert.py`
- `backend/scripts/check_cert.py`

It imports `signing.py` (owned by P1-07); it does not own that file.

**Manual check**

Issue a certificate, then check calls against it:

```powershell
$out = python scripts/issue_cert.py --task demo-1 --owner dev-local --risk R0 `
    --server ai.exa/exa --tool web_search_exa --ttl 3600
$cert = (($out | Select-String '^cert:').Line -split '\s+')[1]

python scripts/check_cert.py --cert $cert --owner dev-local --task demo-1 `
    --server ai.exa/exa --tool web_search_exa          # ok
python scripts/check_cert.py --cert $cert --owner dev-local --task demo-1 `
    --server ai.exa/exa --tool web_fetch_exa           # REFUSED: cert:tool
python scripts/check_cert.py --cert $cert --owner other --task demo-1 `
    --server ai.exa/exa --tool web_search_exa          # REFUSED: cert:owner
python scripts/check_cert.py --cert $cert --owner dev-local --task other `
    --server ai.exa/exa --tool web_search_exa          # REFUSED: cert:task
```

Argument constraints (use a file on Windows, where the shell strips JSON quotes):

```powershell
# .scratch/hinoki_constraint.json
# {"repo_url":{"required":true,"pattern":"^https://github.com/"}}
python scripts/issue_cert.py --task demo-2 --owner dev-local --risk R3 `
    --server hinoki --tool scan_repository `
    --constraint-file ..\.scratch\hinoki_constraint.json

python scripts/check_cert.py --cert <cert2> --owner dev-local --task demo-2 `
    --server hinoki --tool scan_repository --arg repo_url=https://github.com/org/repo   # ok
python scripts/check_cert.py --cert <cert2> --owner dev-local --task demo-2 `
    --server hinoki --tool scan_repository --arg repo_url=https://evil.com/x            # pattern
python scripts/check_cert.py --cert <cert2> --owner dev-local --task demo-2 `
    --server hinoki --tool scan_repository                                              # required
```

Revoke and re-check:

```powershell
python scripts/issue_cert.py --revoke $cert --owner dev-local
python scripts/check_cert.py --cert $cert --owner dev-local --task demo-1 `
    --server ai.exa/exa --tool web_search_exa          # REFUSED: cert:revoked
```

An expired certificate is `cert:expired`; a widened scope is `cert:server` / `cert:tool`.

**Do not touch:** `gate.py`, `server.py`, `policy.py`.

### P1-07 — Signed audit and hash chain

**Builds:** append-only receipts. Each row stores caller, agent, task, goal, server, tool, args hash, decision, reason,
risk, data class, latency, cost, result status, previous hash and signature. Add a verifier that walks the chain.
Never store arguments or results, only hashes.

**Files you own**

- `backend/app/tool_routing/audit.py`
- `backend/app/tool_routing/signing.py`
- `backend/db/190_tool_call_audit.sql`
- `backend/scripts/verify_audit_chain.py`
- `backend/scripts/audit_demo.py`

**Manual check**

```powershell
python scripts/audit_demo.py --count 3 --tamper
# expect:
#   wrote: rc_..., rc_..., rc_...
#   verify: ok (ok), 3 receipts
#   tampered: FAILED as expected (hash mismatch at receipt rc_...)
#   restored: ok (ok)

python scripts/verify_audit_chain.py --since 1h
# expect: ok, N receipts
```

`audit_demo.py` writes ordinary `demo` rows, then edits one row's reason, proves the chain fails at
that receipt, and restores it. Nothing else is changed.

**Do not touch:** `gate.py`, `server.py`.

### P1-08 — Exa adapter (R0, no user sign-in)

**Builds:** a thin provider adapter so the gateway can call Exa without a user sign-in. Prefer a kell-managed key
(`EXA_API_KEY`) over per-user OAuth. Expose `web_search` and `web_fetch`. Add response scanning for prompt injection and
mark the result as untrusted third-party data. If the key is absent, report "not configured", never fall back to a
user's credentials.

**Files you own**

- `backend/app/tool_providers/__init__.py`
- `backend/app/tool_providers/exa/__init__.py`
- `backend/app/tool_providers/exa/client.py`

**Manual check**

```powershell
python scripts/recommend_goal.py "search the web for the latest Postgres release" --call
# expect: results, no sign-in prompt

$env:EXA_API_KEY = ""
python scripts/recommend_goal.py "search the web for the latest Postgres release" --call
# expect: FAILED: exa not configured
```

Also fetch one page and confirm the output is labelled as third-party data.

**Do not touch:** `server.py`, `gateway.py`, the existing `tool_cards` row.

### P1-09 — Hinoki adapter (R3, defensive surface first)

**Builds:** an MCP-shaped wrapper around Hinoki with a **read-only first** surface:

- `scan_repository(repo_url, ref?)`
- `analyze_diff(diff)`
- `explain_finding(finding_id)`
- `propose_fix(finding_id)` — returns a patch, never applies it

No exploit execution, no live third-party target, no credential access in v1. The adapter declares risk R3 and requires
`data_class=PUBLIC_SOURCE`, a certificate, and an approval for anything beyond a read-only scan.

**Files you own**

- `backend/app/tool_providers/hinoki/__init__.py`
- `backend/app/tool_providers/hinoki/client.py`
- `backend/app/tool_providers/hinoki/mcp_server.py`
- `backend/scripts/register_hinoki_card.py`

**Manual check**

```powershell
python scripts/register_hinoki_card.py --dry-run
python scripts/recommend_goal.py "scan this repository for vulnerabilities" --tools-only
# expect: Hinoki in the top tools

python scripts/recommend_goal.py "scan this repository for vulnerabilities" --call --arg repo_url=https://github.com/org/repo
# expect: REFUSED without a certificate; findings with one
```

`propose_fix` must return a patch and write nothing to the repository.

**Do not touch:** `server.py`, `gateway.py`, `goals.py`.

### P1-10 — Run and call recording contract

**Builds:** the shared record both arms write, so Phase 2 is measurement only. `task_runs` holds one row per
(task, arm, attempt) with goal, procedure, card, latency, cost, tokens, status, score, artifact hash, error class and
trace ref. Extend `tool_gateway_calls` with `task_id`, `cert_id`, `decision`, `cost_usd` (nullable, backfill-safe).

**Files you own**

- `backend/app/tool_routing/runs.py`
- `backend/db/191_task_runs.sql`
- `backend/scripts/show_runs.py`

**Manual check**

```powershell
python scripts/recommend_goal.py "search the web" --call --task-id demo-1
python scripts/show_runs.py --task demo-1
# expect: one row per arm, with status, latency, cost, goal and tool
```

A refused call must still produce a row with `status='error'` and an error class.

**Do not touch:** `server.py`, `gate.py`, `scripts/compare/*`.

### P1-11 — Approval surface (can ship after P1-04)

**Builds:** the human checkpoint for R2/R3. When policy returns `approve`, the gate creates a pending approval scoped to
the exact call shape (owner, task, server, tool, argument hash, certificate). A card shows the tool, server, target,
argument hash, data class, risk, cost (not priced yet), reversibility and expiry. Approve/deny is short-lived and
single-use: the next identical call consumes it once. A changed argument or certificate is a different approval.

The backend routes are `/v1/tools/approvals` (website session, room_agent_routes) and `/tools/approvals` (desktop MCP
token, server.py). The website card polls the first; the desktop route proxies the second.

**Files you own**

- `backend/app/tool_routing/approvals.py`
- `backend/db/196_tool_approvals.sql`
- `backend/scripts/approve_tool.py`
- `backend/app/tool_routing/gate.py` (the approval branch; P1-04's gate is merged)
- `backend/app/mcp_server/server.py` (the `/tools/approvals` routes; P1-04 is merged)
- `backend/app/mcp_server/room_agent_routes.py` (the `/v1/tools/approvals` routes)
- `routing_kell_frontend/components/tools/ApprovalCard.tsx`
- `routing_kell_frontend/components/chat/RoomView.tsx` (the pending-approval banner)
- `routing_kell_frontend/lib/tools/approvals.ts`
- `desktop/app/api/tools/approve/route.ts`
- `desktop/components/chat/ApprovalCard.tsx`
- `desktop/lib/tools.ts`

**Manual check**

```powershell
$out = python scripts/issue_cert.py --task demo-5 --owner dev-local --risk R3 `
    --server hinoki --tool scan_repository --constraint-file ..\.scratch\hinoki_constraint.json
$cert = (($out | Select-String '^cert:').Line -split '\s+')[1]

$env:TOOL_GATE = "enforce"
$g = python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local `
    --cert $cert --task demo-5 --arg repo_url=https://github.com/org/repo
# expect: effect approve, approval:ap_...
$ap = (($g | Select-String '^approval:').Line -split ':', 2)[1].Trim()

python scripts/approve_tool.py --list --owner dev-local      # the pending row
python scripts/approve_tool.py --approve $ap --owner dev-local

python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local `
    --cert $cert --task demo-5 --arg repo_url=https://github.com/org/repo
# expect: allow, approval:ap_...   (the approval is spent)

python scripts/check_gate.py ...   # a third identical call -> approve with a NEW id
```

Changing an argument, using a different certificate, or letting the approval expire returns `approve` again. The web
card shows the same row from `/v1/tools/approvals`; the desktop card uses `/tools/approvals`.

**Do not touch:** `policy.py`, `certificates.py`, `audit.py`.

**Phase 1 exit criteria**

- `python scripts/recommend_goal.py "<security prompt>" --explain` returns the security subgoal and Hinoki.
- `python scripts/recommend_goal.py "<web prompt>" --explain` returns the web goal and Exa.
- `--call` on an R3 tool without a certificate is refused in `enforce` mode; R0 passes.
- Every decision produces one `tool_call_audit` row and the chain verifies.
- Exa answers a real query with no user sign-in.
- A run can be started and finished for both arms and read back with `show_runs.py`.

## Phase 2 — Evidence: compare tool results with procedure results

**Goal:** answer, with numbers, whether a tool beats the procedure it would replace — per Goal, not globally.

Phase 2 starts only after the Phase 1 exit criteria pass. It is **read-only with respect to production routing**: it runs
tasks, records results, and reports. It does not change ranking. It is run manually with `run_compare.py`.

### The comparison protocol

For each benchmark task:

1. Resolve the task's Goal with `recommend_goal.py` / `route_goal`.
2. Pick one **procedure arm** (the best active procedure for that Goal) and one **tool arm** (the top card mapped to that
   Goal, after the gate says it may run).
3. Optionally run a **baseline arm** (model alone, no procedure and no tool) to separate "the task is easy" from "the
   procedure/tool helped".
4. Run each arm with the same inputs, the same seed where the arm is stochastic, the same budget cap, and at least three
   attempts.
5. Score with the task's own verifier first (tests, exact match, schema check). Use a blind judge only for open-ended
   tasks, and never let the judge see which arm produced the output.
6. Record a `RunRecord` per attempt and a `ComparisonRecord` per (task, procedure, tool).
7. Report paired deltas with confidence intervals. Do not report a winner from a single run.

Guardrails: public data only, no secrets, a hard per-task cost cap, a wall-clock cap, and an automatic stop if an arm
returns an error three times. Security tasks are read-only scans only.

### P2-01 — Corpus and contracts

**Builds:** the task corpus format and the `RunRecord`/`ComparisonRecord` dataclasses. Each task carries: id, prompt,
goal hint, input refs, verifier kind and config, data class, budget cap, and tags. Start with 60 tasks across 6 goals
(web, docs, code_search, packages, security, database), then grow.

**Files you own**

- `backend/scripts/compare/__init__.py`
- `backend/scripts/compare/contracts.py`
- `backend/scripts/compare/corpus.py`
- `backend/scripts/compare/tasks/*.jsonl`

**Manual check**

```powershell
python -m scripts.compare.corpus --check
# expect: 60 tasks, 6 goals, no duplicate ids, every task has a verifier
```

A task without a verifier must be rejected with its id.

### P2-02 — Procedure runner

**Builds:** run the procedure arm through the existing executor (`app/services/procedure_graph`,
`app/services/step_binding.execute_node`), capturing steps, bindings, latency, cost and tokens. Do not reimplement
execution. If a procedure is unbound, record that fact; do not silently substitute a model and call it the procedure arm.

**Files you own**

- `backend/scripts/compare/procedure_runner.py`

**Manual check**

```powershell
python -m scripts.compare.procedure_runner --task web-001
# expect: one RunRecord (arm=procedure) printed and stored
```

An unbound step must print `unbound`, not `pass`.

### P2-03 — Tool runner

**Builds:** run the tool arm through the gateway with a valid intent certificate, and record the same fields. It must
use the real gate, so a refused call is a recorded refusal, not an exception. No special path around policy.

**Files you own**

- `backend/scripts/compare/tool_runner.py`

**Manual check**

```powershell
python -m scripts.compare.tool_runner --task web-001
# expect: one RunRecord (arm=tool) printed and stored

python -m scripts.compare.tool_runner --task sec-001 --no-cert
# expect: status=error, error_class=gate:denied
```

### P2-04 — Normalizer and scorers

**Builds:** turn heterogeneous outputs into a comparable result: exact match, JSON/schema equality, test-suite result,
artifact hash, and a rubric judge. Normalization is per task type; the raw output and its hash are always kept.

**Files you own**

- `backend/scripts/compare/scoring.py`

**Manual check**

```powershell
python -m scripts.compare.scoring --task web-001 --input out/procedure.json
# expect: score + the scorer that produced it, with the raw hash

python -m scripts.compare.scoring --task web-001 --input out/tool.json
# expect: the same fields, comparable
```

A missing verifier must fail closed.

### P2-05 — Orchestrator

**Builds:** `run_compare.py`: select tasks, resolve Goal, pick arms, run repeats, score, store, and print a run summary.
Flags: `--tasks`, `--goal`, `--arms`, `--repeats`, `--budget-usd`, `--dry-run`, `--out`.

**Files you own**

- `backend/scripts/compare/run_compare.py`

**Manual check**

```powershell
python -m scripts.compare.run_compare --tasks web-001,web-002 --arms procedure,tool --repeats 2 --dry-run
# expect: the full plan, no spend

python -m scripts.compare.run_compare --tasks web-001,web-002 --arms procedure,tool --repeats 2
# expect: 8 RunRecords, 2 ComparisonRecords, a summary table
```

The budget cap must stop the run cleanly.

### P2-06 — Comparison store

**Builds:** persist runs and comparisons. `comparison_results` holds per (task, procedure, card): arm scores, paired
delta, cost delta, latency delta, winner, and the evidence ref. Add indexes for goal and date.

**Files you own**

- `backend/scripts/compare/store.py`
- `backend/db/191_task_runs.sql` (add the comparison table; coordinate with P1-10 if it is still open)

**Manual check**

```powershell
python -m scripts.compare.store --goal web --limit 10
# expect: one row per (task, procedure, card) with deltas

# re-run the same comparison:
python -m scripts.compare.run_compare --tasks web-001 --arms procedure,tool --repeats 1
python -m scripts.compare.store --task web-001
# expect: upsert, not a duplicate
```

### P2-07 — Statistics and report

**Builds:** paired bootstrap confidence intervals for score, cost and latency; McNemar for pass/fail; a cost-per-verified-
success ratio; and a Markdown report per goal with sample sizes and caveats. No p-hacking: the report shows every arm and
every failure.

**Files you own**

- `backend/scripts/compare/stats.py`
- `backend/scripts/compare/report.py`

**Manual check**

```powershell
python -m scripts.compare.report --goal web --out docs/compare_web.md
# expect: per-arm table, paired delta with CI, cost per verified success, caveats
```

A synthetic fixture with a known effect must recover the sign; a zero-effect fixture must produce a CI containing zero.

**Phase 2 exit criteria**

- 60 tasks, 6 goals, at least 3 repeats per arm.
- Every comparison has a paired delta and a CI.
- A written report answers, per goal: does the tool beat the procedure, at what cost, and with what confidence.
- The harness runs from a clean checkout with the commands above, with no test suite.

## Phase 3 — Market: trust from reuse, trials, retirement

**Goal:** replace static trust with verified reuse, give new tools a funded chance, and retire tools that stop working.
Phase 3 consumes Phase 2's comparisons; it does not re-derive them.

### P3-01 — Trust posterior

**Builds:** a materialized `tool_trust` view per (card, goal): wins, judged, lower bound of a Beta posterior, last
outcome, trial vs paid calls. Ranking uses the **lower bound**, not the point estimate, so two lucky passes do not beat
two hundred verified ones.

**Files you own**

- `backend/app/tool_routing/trust.py`
- `backend/db/192_tool_trust.sql`
- `backend/scripts/show_trust.py`

**Manual check**

```powershell
python scripts/show_trust.py --goal web --limit 10
# expect: wins/judged, lower bound, last outcome, trial vs paid

python scripts/recommend_goal.py "search the web" --explain
# expect: trust lower bound shown per tool once the view is on
```

A card with 1/1 must score below a card with 90/100.

### P3-02 — Provider-funded trials

**Builds:** `tool_trials`: provider, card, goal, credit, max calls, used calls, window, status. The router allocates a
small, capped share of calls per goal to active trials; the user sees "New · free trial" and opts in. No trial for R3
offensive actions; a trial may only be a read-only scan.

**Files you own**

- `backend/app/tool_routing/trials.py`
- `backend/db/193_tool_trials.sql`

**Manual check**

```powershell
python -c "import asyncio; from app.tool_routing.trials import status; print(asyncio.run(status('web')))"
# expect: active trials, credit left, share of calls

python scripts/recommend_goal.py "search the web" --explain
# expect: the trial tool is marked and only appears inside its share
```

Credits must never go negative; an expired window must stop allocation; per-user and per-goal caps must hold.

### P3-03 — Ranking integration

**Builds:** rank tools inside a goal by `trust_lower_bound + exploration_bonus − cost_penalty − latency_penalty −
risk_penalty`, with `goal_fit` as a gate, not the score. Exact/alias goal still fast-paths. This milestone takes over
`goal_router.py` and `retrieve.py` after P1-02 merges.

**Files you own**

- `backend/app/tool_routing/goal_router.py` (handoff from P1-02)
- `backend/app/tool_routing/retrieve.py`

**Manual check**

```powershell
python scripts/recommend_goal.py "search the web" --explain
# expect: proven tool first; trial tool only inside its share; high-risk tool last
```

Disabling the flag must restore the Phase 1 order.

### P3-04 — Graduation and retirement

**Builds:** a scheduled job: graduate at `judged >= 20` and `lower_bound >= 0.6`; watch between 0.4 and 0.6; retire
below 0.4 or above a 30% failure rate over the recent window. Retirement reduces traffic before delisting and points the
goal at the next best tool.

**Files you own**

- `backend/app/tool_routing/lifecycle.py`
- `backend/scripts/tool_lifecycle.py`

**Manual check**

```powershell
python scripts/tool_lifecycle.py --dry-run
# expect: per tool, one of graduate / watch / retire, with the numbers behind it

python scripts/recommend_goal.py "search the web" --tools-only
# expect: a retired tool is not listed; the goal shows the next best
```

### P3-05 — Verification market

**Builds:** signed verifier results with provenance, verifier reputation from predictive accuracy, hidden anchors and
randomized audits. Providers may fund verification but cannot buy rank. A new version or a drop in real-use success
triggers re-verification.

**Files you own**

- `backend/app/tool_routing/verification.py`
- `backend/db/194_verification.sql`

**Manual check**

```powershell
python -c "import asyncio; from app.tool_routing.verification import report; print(asyncio.run(report('web')))"
# expect: verifier results with signature status and weight
```

A forged signature must be rejected; a verifier whose results disagree with downstream outcomes must lose weight.

### P3-06 — Pricing and metering (second and last `server.py` owner)

**Builds:** `tool_listings` (price per call, provider, free trial calls, status), metering in the gateway, and a
pre-call price line. Credits first; no cash payouts in this plan. This milestone owns `server.py` after P1-04 hands it
back.

**Files you own**

- `backend/app/tool_routing/pricing.py`
- `backend/db/195_tool_listings.sql`
- `backend/app/mcp_server/server.py` (serialized; only after P1-04 merges)

**Manual check**

```powershell
python scripts/recommend_goal.py "search the web" --call
# expect: a price line before the call, then the result

python scripts/recommend_goal.py "search the web" --call --owner broke
# expect: REFUSED: balance, before any network call
```

A priced call must debit exactly once; a trial call must debit the provider, not the user.

### P3-07 — Market report

**Builds:** a weekly report: exploration rate, graduation rate, time-to-graduation, failure and harm rate, replacement
rate, provider credit spend, cost per verified success, and trust calibration (does the lower bound predict the observed
pass rate?).

**Files you own**

- `backend/scripts/tool_market_report.py`
- `docs/tool_market_results.md`

**Manual check**

```powershell
python scripts/tool_market_report.py --since 7d --out docs/tool_market_results.md
# expect: every metric above, with sample sizes and a calibration curve
```

**Phase 3 exit criteria**

- Ranking is driven by verified reuse, not stars.
- Every new tool can get a capped, funded trial and graduate or retire on evidence.
- No provider can buy rank.
- Every high-risk call still passes the Phase 1 gate.

## Dependency graph

```
P1-02 (recommend_goal) ─┐
P1-01 ────────────┤
P1-03 ────────────┼─→ P1-04 (gate) ─→ P1-11
P1-05 ────────────┤
P1-06 ────────────┤
P1-07 ────────────┤
P1-08 ────────────┤
P1-09 ────────────┘

P1-10 ─→ P2-01 ─→ {P2-02, P2-03, P2-04} ─→ P2-05 ─→ P2-06 ─→ P2-07

P2-07 ─→ P3-01 ─┐
          P3-02 ─┼─→ P3-03 ─→ P3-04
          P3-05 ─┘
P1-04 ─→ P3-06 ─→ P3-07
```

The diagram is build order, not a strict dependency list: only P1-04 truly waits on P1-05/06/07. P1-02 is the first
merge: it is the recommend-goal script used to manually test everything else. P2-03 also needs P1-04 (it must go through the real
gate).

Parallel-safe groups:

- **Group A (no `server.py`):** P1-01, P1-02, P1-03, P1-08, P1-09, P1-10.
- **Group B (gate chain):** P1-04 → P1-05 → P1-06 → P1-07, serialized by interface, not by file.
- **Group C (Phase 2):** P2-01 → {P2-02, P2-03, P2-04} → P2-05 → P2-06 → P2-07.
- **Group D (Phase 3):** P3-01, P3-02, P3-05 in parallel; P3-03 after P3-01/P3-02; P3-04 after P3-03; P3-06 serialized
  on `server.py`; P3-07 last.

## Status

Update only your own row. `—` means unclaimed.

| ID | Milestone | Owner | State | PR / issue |
|---|---|---|---|---|
| P1-01 | Security and intent Goals | opencode | done | this commit |
| P1-02 | Recommend-goal script (start here) | opencode | done | this commit |
| P1-03 | Mapping v2 and manual pins | opencode | done | this commit |
| P1-04 | Gate core (`server.py`) | opencode | done | this commit |
| P1-05 | Policy store | opencode | done | this commit |
| P1-06 | Intent certificates | opencode | done | this commit |
| P1-07 | Signed audit | opencode | done | this commit |
| P1-08 | Exa adapter | anuj-claude | done | #27 |
| P1-09 | Hinoki adapter | - | blocked: API base/schema (key in backend/.env) | #26 |
| P1-10 | Run recording | anuj-claude | done | #25 |
| P1-11 | Approval surface | opencode | done | this commit |
| P2-01 | Corpus and contracts | anuj-claude | done | #30 |
| P2-02 | Procedure runner | anuj-claude | done | #32 |
| P2-03 | Tool runner | anuj-claude | done | #35 |
| P2-04 | Scorers | anuj-claude | done | #31 |
| P2-05 | Orchestrator | anuj-claude | done | #36 |
| P2-06 | Comparison store | anuj-claude | done | #37 |
| P2-07 | Statistics and report | anuj-claude | done | #38 |
| P3-01 | Trust posterior | anuj-claude | done | #39 |
| P3-02 | Provider-funded trials | anuj-claude | done | #40 |
| P3-03 | Ranking integration | anuj-claude | done | #41 |
| P3-04 | Graduation and retirement | anuj-claude | done | #42 |
| P3-05 | Verification market | anuj-claude | done | #43 |
| P3-06 | Pricing and metering (`server.py`) | anuj-claude | done | #45 |
| P3-07 | Market report | anuj-claude | done | #46 |

## File ownership index

A file may appear in exactly one open milestone. If you need to change it, coordinate with the owner.

| File / glob | Owner |
|---|---|
| `backend/app/tool_routing/goals.py`, `backend/db/187_*` | P1-01 |
| `backend/scripts/recommend_goal.py`, `backend/scripts/search_goals.py`, `backend/app/tool_routing/goal_router.py` | P1-02 (router then P3-03) |
| `backend/scripts/map_tool_cards_to_goals.py`, `backend/scripts/report_goal_coverage.py` | P1-03 |
| `backend/app/tool_routing/gate.py`, `backend/scripts/check_gate.py`, `backend/app/mcp_server/server.py` | P1-04 (then P3-06) |
| `backend/app/tool_routing/policy.py`, `backend/db/188_*`, `backend/scripts/check_policy.py` | P1-05 |
| `backend/app/tool_routing/certificates.py`, `backend/db/189_*`, `backend/scripts/issue_cert.py`, `backend/scripts/check_cert.py` | P1-06 |
| `backend/app/tool_routing/audit.py`, `backend/app/tool_routing/signing.py`, `backend/db/190_*`, `backend/scripts/verify_audit_chain.py`, `backend/scripts/audit_demo.py` | P1-07 |
| `backend/app/tool_providers/exa/**` | P1-08 |
| `backend/app/tool_providers/hinoki/**`, `backend/scripts/register_hinoki_card.py` | P1-09 |
| `backend/app/tool_routing/runs.py`, `backend/db/191_*`, `backend/scripts/show_runs.py` | P1-10 (then P2-06) |
| `backend/app/tool_routing/approvals.py`, `backend/db/196_*`, `backend/scripts/approve_tool.py`, `backend/app/tool_routing/gate.py`, `backend/app/mcp_server/server.py`, `backend/app/mcp_server/room_agent_routes.py`, `routing_kell_frontend/components/tools/**`, `routing_kell_frontend/components/chat/RoomView.tsx`, `routing_kell_frontend/lib/tools/approvals.ts`, `desktop/app/api/tools/approve/**`, `desktop/components/chat/ApprovalCard.tsx`, `desktop/lib/tools.ts` | P1-11 |
| `backend/scripts/compare/contracts.py`, `corpus.py`, `tasks/**` | P2-01 |
| `backend/scripts/compare/procedure_runner.py` | P2-02 |
| `backend/scripts/compare/tool_runner.py` | P2-03 |
| `backend/scripts/compare/scoring.py` | P2-04 |
| `backend/scripts/compare/run_compare.py` | P2-05 |
| `backend/scripts/compare/store.py`, `backend/db/191_*` | P2-06 |
| `backend/scripts/compare/stats.py`, `report.py` | P2-07 |
| `backend/app/tool_routing/trust.py`, `backend/db/192_*`, `backend/scripts/show_trust.py` | P3-01 |
| `backend/app/tool_routing/trials.py`, `backend/db/193_*` | P3-02 |
| `backend/app/tool_routing/retrieve.py` | P3-03 |
| `backend/app/tool_routing/lifecycle.py`, `backend/scripts/tool_lifecycle.py` | P3-04 |
| `backend/app/tool_routing/verification.py`, `backend/db/194_*` | P3-05 |
| `backend/app/tool_routing/pricing.py`, `backend/db/195_*` | P3-06 |
| `backend/scripts/tool_market_report.py`, `docs/tool_market_results.md` | P3-07 |

## What "done" means per phase

| Phase | Done when |
|---|---|
| 1 | The recommend-goal script routes a security prompt and a web prompt to the right goal and tool; `--call` passes R0 and refuses R3 without a certificate; every call has a decision and a verifiable receipt; Exa runs keyless-to-the-user; Hinoki is R3-gated. |
| 2 | A reproducible Python harness compares tool vs procedure vs baseline on 60 tasks, with paired deltas and confidence intervals, and a report per goal. |
| 3 | Ranking is driven by verified reuse; trials are provider-funded and capped; bad tools retire; verification is continuous and cannot be bought. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agents collide on `server.py` | Serialized ownership (P1-04, then P3-06); everyone else imports the gate |
| Gate blocks legitimate calls during rollout | `TOOL_GATE=shadow` first; compare decisions before enforcing |
| Trial exploration harms users | Cap share per goal and user; R0/R1 only; automatic fallback to the next tool |
| Comparison overfits 60 tasks | Paired design, 3 repeats, CIs, a held-out task set, and no winner from one run |
| Judge bias | Blind to arm and task id; the task's own verifier first |
| Provider-funded trials become pay-to-win | Hard share cap, distinct-user counting, audits, no rank purchase |
| Static trust sneaks back in | `stars`/`verified` are metadata only; ranking reads the posterior |
| Hinoki misuse | Defensive surface only in v1; R3 approval; public data; no live targets |

## Machine-readable manifest

Agents may parse this block to claim work. Keep it in sync with the status table.

```yaml
version: 1
updated: 2026-11-11
testing: manual
start_prompt: "read docs/goal_router.md and complete it"
push_cadence: "after every milestone, and at least every 45 minutes while one is in progress"
phases:
  - id: P1
    name: foundation
    exit: "resolve + gate + bind + record; Exa keyless; Hinoki R3-gated"
  - id: P2
    name: evidence
    exit: "tool vs procedure vs baseline on 60 tasks with paired CIs"
  - id: P3
    name: market
    exit: "trust from reuse; funded trials; retirement; verification market"
milestones:
  - id: P1-01
    title: security and intent Goals
    owns: [backend/app/tool_routing/goals.py, backend/db/187_tool_goals_security.sql]
    depends: []
    manual_check: "recommend_goal.py on CVE, secrets and auth prompts puts the subgoal in top 3"
  - id: P1-02
    title: recommend-goal script (start here)
    owns: [backend/scripts/recommend_goal.py, backend/scripts/search_goals.py, backend/app/tool_routing/goal_router.py]
    depends: []
    manual_check: "--explain shows legs; --json is valid; --call goes through the gate"
  - id: P1-03
    title: mapping v2 and manual pins
    owns: [backend/scripts/map_tool_cards_to_goals.py, backend/scripts/report_goal_coverage.py]
    depends: []
    manual_check: "pins survive --replace; coverage report prints orphans"
  - id: P1-04
    title: gate core
    owns: [backend/app/tool_routing/gate.py, backend/scripts/check_gate.py, backend/app/mcp_server/server.py]
    depends: [P1-05, P1-06, P1-07]
    manual_check: "enforce denies R3 without cert; shadow logs only; fail closed"
  - id: P1-05
    title: policy store
    owns: [backend/app/tool_routing/policy.py, backend/db/188_tool_policies.sql, backend/scripts/check_policy.py]
    depends: []
    manual_check: "deny wins; R0 allow; R2/R3 approve; unknown deny"
  - id: P1-06
    title: intent certificates
    owns: [backend/app/tool_routing/certificates.py, backend/db/189_intent_certificates.sql, backend/scripts/issue_cert.py, backend/scripts/check_cert.py]
    depends: []
    manual_check: "expired/revoked/widened fail; valid passes"
  - id: P1-07
    title: signed audit
    owns: [backend/app/tool_routing/audit.py, backend/app/tool_routing/signing.py, backend/db/190_tool_call_audit.sql, backend/scripts/verify_audit_chain.py, backend/scripts/audit_demo.py]
    depends: []
    manual_check: "chain verifies; tamper fails at the row"
  - id: P1-08
    title: Exa adapter
    owns: [backend/app/tool_providers/exa]
    depends: []
    manual_check: "real search with no sign-in; missing key refuses clearly"
  - id: P1-09
    title: Hinoki adapter
    owns: [backend/app/tool_providers/hinoki]
    depends: []
    manual_check: "four read-only tools; no cert means no call"
  - id: P1-10
    title: run recording
    owns: [backend/app/tool_routing/runs.py, backend/db/191_task_runs.sql, backend/scripts/show_runs.py]
    depends: []
    manual_check: "start/finish/read round-trip; errors recorded"
  - id: P1-11
    title: approval surface
    owns: [backend/app/tool_routing/approvals.py, backend/db/196_tool_approvals.sql, backend/scripts/approve_tool.py, backend/app/tool_routing/gate.py, backend/app/mcp_server/server.py, backend/app/mcp_server/room_agent_routes.py, routing_kell_frontend/components/tools, routing_kell_frontend/components/chat/RoomView.tsx, routing_kell_frontend/lib/tools/approvals.ts, desktop/app/api/tools/approve, desktop/components/chat/ApprovalCard.tsx, desktop/lib/tools.ts]
    depends: [P1-04]
    manual_check: "approve once allows one matching call; hash change invalidates"
  - id: P2-01
    title: corpus and contracts
    owns: [backend/scripts/compare/contracts.py, backend/scripts/compare/corpus.py, backend/scripts/compare/tasks]
    depends: [P1]
    manual_check: "60 tasks validate; duplicate ids rejected"
  - id: P2-02
    title: procedure runner
    owns: [backend/scripts/compare/procedure_runner.py]
    depends: [P2-01]
    manual_check: "unbound step marked, not passed"
  - id: P2-03
    title: tool runner
    owns: [backend/scripts/compare/tool_runner.py]
    depends: [P2-01, P1-04]
    manual_check: "gate refusal recorded as error_class gate:denied"
  - id: P2-04
    title: normalizer and scorers
    owns: [backend/scripts/compare/scoring.py]
    depends: [P2-01]
    manual_check: "each scorer prints pass or fail; judge blind"
  - id: P2-05
    title: orchestrator
    owns: [backend/scripts/compare/run_compare.py]
    depends: [P2-02, P2-03, P2-04]
    manual_check: "2x2x2 run writes 8 runs and 2 comparisons"
  - id: P2-06
    title: comparison store
    owns: [backend/scripts/compare/store.py, backend/db/191_task_runs.sql]
    depends: [P2-05]
    manual_check: "upsert by task/procedure/card; query by goal"
  - id: P2-07
    title: statistics and report
    owns: [backend/scripts/compare/stats.py, backend/scripts/compare/report.py]
    depends: [P2-06]
    manual_check: "known effect recovered; zero effect CI contains 0"
  - id: P3-01
    title: trust posterior
    owns: [backend/app/tool_routing/trust.py, backend/db/192_tool_trust.sql, backend/scripts/show_trust.py]
    depends: [P2]
    manual_check: "1/1 ranks below 90/100"
  - id: P3-02
    title: provider-funded trials
    owns: [backend/app/tool_routing/trials.py, backend/db/193_tool_trials.sql]
    depends: [P2]
    manual_check: "credits never negative; caps hold; no R3 trials"
  - id: P3-03
    title: ranking integration
    owns: [backend/app/tool_routing/goal_router.py, backend/app/tool_routing/retrieve.py]
    depends: [P3-01, P3-02]
    manual_check: "proven wins; trial only in share; flag restores P1 order"
  - id: P3-04
    title: graduation and retirement
    owns: [backend/app/tool_routing/lifecycle.py, backend/scripts/tool_lifecycle.py]
    depends: [P3-03]
    manual_check: "graduate/watch/retire transitions exact"
  - id: P3-05
    title: verification market
    owns: [backend/app/tool_routing/verification.py, backend/db/194_verification.sql]
    depends: [P2]
    manual_check: "forged signature rejected; verifier reputation tracks outcomes"
  - id: P3-06
    title: pricing and metering
    owns: [backend/app/tool_routing/pricing.py, backend/db/195_tool_listings.sql, backend/app/mcp_server/server.py]
    depends: [P1-04]
    manual_check: "debit once; trial debits provider; refuse before network"
  - id: P3-07
    title: market report
    owns: [backend/scripts/tool_market_report.py, docs/tool_market_results.md]
    depends: [P3-04, P3-06]
    manual_check: "calibration curve with sample sizes"
```
