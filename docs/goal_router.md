# Goal-first routing: phases, sub-milestones, and file ownership

Status: active plan, 2026-11-11. Phase 1 is open for parallel work.
This document is the map. The GitHub issue is the lock (see [Claiming](#claiming-a-sub-milestone)).

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
   Today the gateway checks data class, secrets, grants and rate limits, but there is no policy store, no task-scoped
   intent, no risk tier, no approval, and no signed receipt.
2. **Evidence.** We do not yet measure whether a tool actually does the job **better than the procedure it would
   replace**. Phase 2 is a Python harness that runs the same task through both arms and compares the results. Phase 3
   turns that evidence into ranking, trials and retirement.

Non-goals for this plan: cash payouts to providers, a public marketplace, and autonomous offensive security. Trial
credits and pricing are scoped in Phase 3 but ship after the gate and the comparison harness.

## Current state (verified 2026-11-11)

| Piece | State | Where |
|---|---|---|
| Goal index and search | exists | `goal_search_index`, `goal_names`, `app/services/goals.py` |
| Goal-first router | exists, first cut | `app/tool_routing/goal_router.py` |
| Goal CLI | exists | `backend/scripts/search_goals.py` |
| Card → goal mapping | exists, 125,656 rows | `db/186_tool_card_goals.sql`, `scripts/map_tool_cards_to_goals.py` |
| Tool gateway | exists, phase 1 | `app/tool_routing/gateway.py`, `_gateway_call` in `app/mcp_server/server.py` |
| OAuth / consent | exists, off by default | `tool_oauth_*`, `oauth_client.py`, `TOOL_GATEWAY_OAUTH` |
| Outcome recording | partial | `tool_observations` (db/162), `store.record_outcome` |
| Exa | card + mapping exist | `registry:ai.exa/exa`, goal `web`, score 3.31 |
| Hinoki | not integrated | needs an MCP wrapper and a card |
| Policy / intent / signed audit | not built | this plan, Phase 1 |
| Tool-vs-procedure comparison | not built | this plan, Phase 2 |
| Trust from reuse, trials, pricing | not built | this plan, Phase 3 |

Known local constraint: goal semantic/FTS search warns that the search group's members are unconfigured. Exact
`goal_names` lookup works and hydrates from the home shard; the K000 full-text fallback in `goal_router.py` keeps the
25 taxonomy Goals searchable. Do not "fix" this by copying Goals onto K000.

### Try the router today

```powershell
cd backend
python scripts/search_goals.py "check what the page looks like after login"
python scripts/search_goals.py "scan this repository for vulnerabilities" --json
python scripts/map_tool_cards_to_goals.py --dry-run
```

The first two are read-only. The mapper's dry run writes nothing.

## Ground rules for parallel agents

1. **One agent per sub-milestone.** The milestone ID is the unit of ownership, not the phase.
2. **Only edit files listed under your milestone's "Files you own".** If you need a change in a file owned by another
   milestone, open a small PR against that milestone's owner or add a comment on its issue. Do not edit it "just once".
3. **`backend/app/mcp_server/server.py` is serialized.** Exactly one open milestone may own it at a time: `P1-04`, then
   `P3-06`. Everything else integrates through a module the gate imports. This is the main collision point.
4. **Migrations are allocated, not chosen.** Take the number in your milestone. If it is taken, stop and comment.
5. **Ship behind a flag.** New behavior defaults to shadow/off until its acceptance command passes.
6. **Never weaken a fail-closed default to make a test pass.** A denied call is a correct outcome.
7. **Do not add static trust as a ranking signal.** Stars and `verified` are metadata. Ranking comes from verified
   reuse (Phase 3), not from a badge.

### Claiming a sub-milestone

1. Open a GitHub issue titled `goal-router: <ID> <short name>` (for example `goal-router: P1-05 policy store`).
2. Assign yourself and paste the issue link in your PR description.
3. Branch: `goal-router/<ID>-<slug>`.
4. Edit only the `Owner` cell for your row in the status table below (one-line change; do not reformat the doc).
5. Open a draft PR early. Merge when the acceptance command passes and no owned file is left dirty.

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

@dataclass
class Decision:
    effect: str            # "allow" | "deny" | "approve"
    reason: str            # stable code, e.g. "policy:r3_requires_approval"
    risk: str              # "R0".."R4"
    cert_id: str | None
    approval_id: str | None
    constraints: dict      # max_calls, egress, timeout_s, sandbox

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

## Phase 1 — Foundation: route, gate, bind, record

**Goal:** a prompt lands on the right Goal, the Goal shows its ways and tools, every tool call passes one gate, and every
call and run is recorded. Exa runs without a user sign-in. Hinoki is present but R3-gated.

**Exit criteria (Phase 2 cannot start until all pass):**

- `python scripts/search_goals.py "scan this repository for vulnerabilities"` returns the security goal and Hinoki in
  its tools.
- `python scripts/search_goals.py "search the web for the latest Postgres release"` returns the web goal and Exa.
- A gateway call without a certificate is denied for R2/R3 and allowed for R0, in `enforce` mode.
- Every decision produces one `tool_call_audit` row with a hash chain that verifies.
- Exa answers a real query through the gateway with no user sign-in.
- A run can be started and finished for both arms, and the row is readable by `scripts/compare`.

### P1-01 — Security and intent Goals

**Builds:** the subgoals a security prompt must land on, so "scan dependencies for CVEs" does not collapse into a generic
"security" bucket.

Subgoals: `security_dependency_cve`, `security_secret_scan`, `security_sast`, `security_auth_review`,
`security_pentest`, plus triage markers for each. Keep the existing 25 goal ids stable; add, do not rename.

**Files you own**

- `backend/app/tool_routing/goals.py`
- `backend/db/187_tool_goals_security.sql`
- `backend/tests/test_tool_goals_security_offline.py`

**Acceptance:** the migration is idempotent; a new test asserts every subgoal has a deterministic UUID, a description,
marker words, and that `goal_text()` still works for the original 25.

**Do not touch:** `goal_router.py`, the mapper, `server.py`.

### P1-02 — Router hardening

**Builds:** correctness and diagnostics for `route_goal`: exact/alias normalization, K000 fallback, per-leg scores,
deduplication, deterministic tie-break, and a clear "no match" result. Add a `--explain` flag that shows which leg matched
and why. Do not add new ranking signals; that is Phase 3.

**Files you own**

- `backend/app/tool_routing/goal_router.py`
- `backend/scripts/search_goals.py`
- `backend/tests/test_goal_router_offline.py`

**Acceptance:** `search_goals.py "..." --explain` prints one line per leg (`exact`, `fts`, `vector`) with rank and score;
a test feeds a fixture pool and asserts exact beats FTS, aliases resolve, and an empty query returns `[]`.

**Do not touch:** `goals.py`, `retrieve.py`, `server.py`.

### P1-03 — Mapping v2 and manual pins

**Builds:** better card→goal coverage and a way to pin a launch tool (Hinoki, Exa) without waiting for data. Extend the
mapper with `--pin card_id goal_id score`, `--report` (coverage per goal, orphan cards), and idempotent upserts with
`source='manual'`. Manual pins must never be deleted by `--replace`.

**Files you own**

- `backend/scripts/map_tool_cards_to_goals.py`
- `backend/scripts/report_goal_coverage.py`
- `backend/tests/test_map_tool_cards_offline.py`

**Acceptance:** `--dry-run --report` prints cards, mappings, goals with zero cards, and cards with zero goals;
`--pin registry:ai.exa/exa <web_goal_id> 9.0` survives `--replace`.

**Do not touch:** `goal_router.py`, `goals.py`, `server.py`.

### P1-04 — Gate core (the only milestone that edits `server.py`)

**Builds:** `authorize()` as the single choke point. Wire it at the top of `_gateway_call` and before direct tool
dispatch in `use_tool`. Modes: `off`, `shadow` (log the decision, never block), `enforce` (block). Fail closed: any
exception in policy, certificate or audit denies R2+ and denies everything if `TOOL_GATE_STRICT=on`.

Order inside the gate: authenticate → certificate → allowlist/capability → risk tier → policy → schema → value-flow →
egress → rate limit → sandbox profile → audit. Reuse the existing checks in `_gateway_call` (data class, secrets, grants,
rate limits) rather than duplicating them.

**Files you own**

- `backend/app/tool_routing/gate.py`
- `backend/app/mcp_server/server.py` (serialized; hand back after merge)
- `backend/tests/test_tool_gate_offline.py`

**Acceptance:** `TOOL_GATE=enforce` + a fake R3 call with no certificate returns `REFUSED` and writes an audit row;
`TOOL_GATE=shadow` lets it through and still writes the row; a policy exception denies in enforce mode.

**Do not touch:** `policy.py`, `certificates.py`, `audit.py` (import them; their owners build them).

### P1-05 — Policy store

**Builds:** declarative, deterministic policy. `tool_policies` rows are keyed by server/tool/goal and carry risk tier,
effect (`allow`/`deny`/`approve`), data classes, max calls, egress allowlist, and a priority. Evaluation order: explicit
deny → allowlist → risk → data class → rate → default deny. Ship the seed rows for Exa (R0 allow) and Hinoki (R3
approve).

**Files you own**

- `backend/app/tool_routing/policy.py`
- `backend/db/188_tool_policies.sql`
- `backend/tests/test_tool_policy_offline.py`

**Acceptance:** a table-driven test covers deny-wins, allowlist-empty means all, R0 auto-allow, R2/R3 approve, unknown
tool deny, and an unparseable rule deny.

**Do not touch:** `gate.py`, `server.py`.

### P1-06 — Intent certificates

**Builds:** task-scoped authorization. A certificate names the task, user, agent, goal, allowed servers/tools, argument
constraints, data class, risk ceiling, egress, max calls and expiry. It can only **narrow** policy. The model cannot
mint one: only `certificates.issue()` from a trusted caller can. Verify signature, expiry, revocation, and that the call
is inside the allowed set.

**Files you own**

- `backend/app/tool_routing/certificates.py`
- `backend/db/189_intent_certificates.sql`
- `backend/tests/test_intent_certificates_offline.py`

**Acceptance:** expired, revoked, wrong-user, wrong-goal, and widened-capability certificates all fail with distinct
reason codes; a valid certificate passes; issuing requires an explicit goal.

**Do not touch:** `gate.py`, `server.py`, `policy.py`.

### P1-07 — Signed audit and hash chain

**Builds:** append-only receipts. Each row stores caller, agent, task, goal, server, tool, args hash, decision, reason,
risk, data class, latency, cost, result status, previous hash and signature. Add a verifier that walks the chain.
Never store arguments or results, only hashes.

**Files you own**

- `backend/app/tool_routing/audit.py`
- `backend/db/190_tool_call_audit.sql`
- `backend/scripts/verify_audit_chain.py`
- `backend/tests/test_tool_audit_offline.py`

**Acceptance:** `python scripts/verify_audit_chain.py --since 24h` returns `ok`; tampering with one row makes it fail at
that row; the writer is safe under concurrent inserts (advisory lock or serialized sequence).

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
- `backend/tests/test_exa_adapter_offline.py`

**Acceptance:** a mocked HTTP test covers success, 401, timeout, oversized response, and injection-looking output;
`EXA_API_KEY` missing yields a clear refusal.

**Do not touch:** `server.py`, `gateway.py`, `tool_cards` rows (use the existing card).

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
- `backend/tests/test_hinoki_adapter_offline.py`

**Acceptance:** the wrapper lists exactly the four tools with schemas; a call without a certificate is refused in
enforce mode; a mocked scan returns findings with stable ids; `propose_fix` returns a patch and writes nothing.

**Do not touch:** `server.py`, `gateway.py`, `goals.py`.

### P1-10 — Run and call recording contract

**Builds:** the shared record both arms write, so Phase 2 is measurement only. `task_runs` holds one row per
(task, arm, attempt) with goal, procedure, card, latency, cost, tokens, status, score, artifact hash, error class and
trace ref. Extend `tool_gateway_calls` with `task_id`, `cert_id`, `decision`, `cost_usd` (nullable, backfill-safe).

**Files you own**

- `backend/app/tool_routing/runs.py`
- `backend/db/191_task_runs.sql`
- `backend/tests/test_task_runs_offline.py`

**Acceptance:** start → finish → read returns the same record; a failed run still records `status='error'` and an error
class; the migration is additive and idempotent.

**Do not touch:** `server.py`, `gate.py`, `scripts/compare/*`.

### P1-11 — Approval surface (can ship after P1-04)

**Builds:** the human checkpoint for R2/R3. A card shows tool, server, target, argument summary, data class, risk,
cost, reversibility and expiry. Approve/deny is signed, scoped to the exact call shape, single-use or capped, and
short-lived.

**Files you own**

- `routing_kell_frontend/components/tools/ApprovalCard.tsx`
- `routing_kell_frontend/app/api/tools/approve/route.ts`
- `routing_kell_frontend/lib/tools/approvals.ts`
- `desktop/app/api/tools/approve/route.ts`
- `desktop/components/chat/ApprovalCard.tsx`
- `backend/tests/test_tool_approvals_offline.py`

**Acceptance:** an R3 call returns `approve` with an approval id; approving once allows exactly one matching call;
a changed argument hash invalidates the approval; expiry invalidates it.

**Do not touch:** `server.py` (the gate consumes `tool_approvals`), `gate.py`.

## Phase 2 — Evidence: compare tool results with procedure results

**Goal:** answer, with numbers, whether a tool beats the procedure it would replace — per Goal, not globally.

Phase 2 starts only after the Phase 1 exit criteria pass. It is **read-only with respect to production routing**: it runs
tasks, records results, and reports. It does not change ranking.

### The comparison protocol

For each benchmark task:

1. Resolve the task's Goal with `route_goal`.
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
- `backend/tests/test_compare_corpus_offline.py`

**Acceptance:** loading the corpus validates every task, rejects duplicate ids, and rejects a task without a verifier;
a dry run prints the 60 tasks and their goals.

### P2-02 — Procedure runner

**Builds:** run the procedure arm through the existing executor (`app/services/procedure_graph`,
`app/services/step_binding.execute_node`), capturing steps, bindings, latency, cost and tokens. Do not reimplement
execution. If a procedure is unbound, record that fact; do not silently substitute a model and call it the procedure arm.

**Files you own**

- `backend/scripts/compare/procedure_runner.py`
- `backend/tests/test_compare_procedure_runner_offline.py`

**Acceptance:** a fixture procedure runs to a recorded result; an unbound step is marked `unbound`, not passed; a
mid-run failure produces `status='error'` with an error class.

### P2-03 — Tool runner

**Builds:** run the tool arm through the gateway with a valid intent certificate, and record the same fields. It must
use the real gate, so a refused call is a recorded refusal, not an exception. No special path around policy.

**Files you own**

- `backend/scripts/compare/tool_runner.py`
- `backend/tests/test_compare_tool_runner_offline.py`

**Acceptance:** a mocked gateway call records a pass; a policy refusal records `status='error'` with
`error_class='gate:denied'`; no certificate means no call.

### P2-04 — Normalizer and scorers

**Builds:** turn heterogeneous outputs into a comparable result: exact match, JSON/schema equality, test-suite result,
artifact hash, and a rubric judge. Normalization is per task type; the raw output and its hash are always kept.

**Files you own**

- `backend/scripts/compare/scoring.py`
- `backend/tests/test_compare_scoring_offline.py`

**Acceptance:** each scorer has a pass case and a fail case; a judge prompt is blind to arm and task id; a missing
verifier fails closed.

### P2-05 — Orchestrator

**Builds:** `run_compare.py`: select tasks, resolve Goal, pick arms, run repeats, score, store, and print a run summary.
Flags: `--tasks`, `--goal`, `--arms`, `--repeats`, `--budget-usd`, `--dry-run`, `--out`.

**Files you own**

- `backend/scripts/compare/run_compare.py`
- `backend/tests/test_compare_orchestrator_offline.py`

**Acceptance:** `--dry-run` prints the full plan and spends nothing; a real run over 2 tasks × 2 arms × 2 repeats writes
8 `RunRecord`s and 2 `ComparisonRecord`s; the budget cap stops the run cleanly.

### P2-06 — Comparison store

**Builds:** persist runs and comparisons. `comparison_results` holds per (task, procedure, card): arm scores, paired
delta, cost delta, latency delta, winner, and the evidence ref. Add indexes for goal and date.

**Files you own**

- `backend/scripts/compare/store.py`
- `backend/db/191_task_runs.sql` (add the comparison table; coordinate with P1-10 if it is still open)
- `backend/tests/test_compare_store_offline.py`

**Acceptance:** write → read round-trips; a re-run upserts by (task, procedure, card); the table is queryable by goal.

### P2-07 — Statistics and report

**Builds:** paired bootstrap confidence intervals for score, cost and latency; McNemar for pass/fail; a cost-per-verified-
success ratio; and a Markdown report per goal with sample sizes and caveats. No p-hacking: the report shows every arm and
every failure.

**Files you own**

- `backend/scripts/compare/stats.py`
- `backend/scripts/compare/report.py`
- `backend/tests/test_compare_stats_offline.py`

**Acceptance:** a synthetic fixture with a known effect recovers the sign and a CI that contains the true value; a
zero-effect fixture produces a CI containing zero; the report renders from stored rows only.

### P2-08 — Comparison regression suite

**Builds:** an offline end-to-end test that runs the whole harness against fixtures (no network), plus a golden report.
This is what protects the harness from drift.

**Files you own**

- `backend/tests/test_compare_e2e_offline.py`
- `backend/tests/fixtures/compare/*`

**Acceptance:** `pytest backend/tests/test_compare_e2e_offline.py` passes in under 60 seconds with no network and no
database beyond the fixture.

**Phase 2 exit criteria**

- 60 tasks, 6 goals, at least 3 repeats per arm.
- Every comparison has a paired delta and a CI.
- A written report answers, per goal: does the tool beat the procedure, at what cost, and with what confidence.
- The harness is reproducible from a clean checkout with a documented command.

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
- `backend/tests/test_tool_trust_offline.py`

**Acceptance:** a card with 1/1 scores below a card with 90/100; a card with 40/100 scores below 90/100; the view
refreshes from `tool_observations` + `comparison_results`.

### P3-02 — Provider-funded trials

**Builds:** `tool_trials`: provider, card, goal, credit, max calls, used calls, window, status. The router allocates a
small, capped share of calls per goal to active trials; the user sees "New · free trial" and opts in. No trial for R3
offensive actions; a trial may only be a read-only scan.

**Files you own**

- `backend/app/tool_routing/trials.py`
- `backend/db/193_tool_trials.sql`
- `backend/tests/test_tool_trials_offline.py`

**Acceptance:** credits decrement per call and never go negative; an expired window stops allocation; per-user and
per-goal caps hold; a provider cannot buy more than the configured share.

### P3-03 — Ranking integration

**Builds:** rank tools inside a goal by `trust_lower_bound + exploration_bonus − cost_penalty − latency_penalty −
risk_penalty`, with `goal_fit` as a gate, not the score. Exact/alias goal still fast-paths. This milestone takes over
`goal_router.py` and `retrieve.py` after P1-02 merges.

**Files you own**

- `backend/app/tool_routing/goal_router.py` (handoff from P1-02)
- `backend/app/tool_routing/retrieve.py`
- `backend/tests/test_goal_ranking_offline.py`

**Acceptance:** with a fixture posterior, the proven tool wins; an active trial can win only inside its exploration
share; a high-risk tool never wins on relevance alone; disabling the flag restores the Phase 1 order.

### P3-04 — Graduation and retirement

**Builds:** a scheduled job: graduate at `judged >= 20` and `lower_bound >= 0.6`; watch between 0.4 and 0.6; retire
below 0.4 or above a 30% failure rate over the recent window. Retirement reduces traffic before delisting and points the
goal at the next best tool.

**Files you own**

- `backend/app/tool_routing/lifecycle.py`
- `backend/scripts/tool_lifecycle.py`
- `backend/tests/test_tool_lifecycle_offline.py`

**Acceptance:** fixture transitions produce exactly graduate/watch/retire; a retired tool is not returned by the router;
a re-listed tool starts in trial, not proven.

### P3-05 — Verification market

**Builds:** signed verifier results with provenance, verifier reputation from predictive accuracy, hidden anchors and
randomized audits. Providers may fund verification but cannot buy rank. A new version or a drop in real-use success
triggers re-verification.

**Files you own**

- `backend/app/tool_routing/verification.py`
- `backend/db/194_verification.sql`
- `backend/tests/test_verification_offline.py`

**Acceptance:** a forged signature is rejected; a verifier whose results disagree with downstream outcomes loses weight;
a version change invalidates prior verification for ranking.

### P3-06 — Pricing and metering (second and last `server.py` owner)

**Builds:** `tool_listings` (price per call, provider, free trial calls, status), metering in the gateway, and a
pre-call price line. Credits first; no cash payouts in this plan. This milestone owns `server.py` after P1-04 hands it
back.

**Files you own**

- `backend/app/tool_routing/pricing.py`
- `backend/db/195_tool_listings.sql`
- `backend/app/mcp_server/server.py` (serialized; only after P1-04 merges)
- `backend/tests/test_tool_pricing_offline.py`

**Acceptance:** a priced call debits exactly once; a trial call debits the provider, not the user; an insufficient
balance refuses before the network call; the price is shown before execution.

### P3-07 — Market report

**Builds:** a weekly report: exploration rate, graduation rate, time-to-graduation, failure and harm rate, replacement
rate, provider credit spend, cost per verified success, and trust calibration (does the lower bound predict the observed
pass rate?).

**Files you own**

- `backend/scripts/tool_market_report.py`
- `docs/tool_market_results.md`

**Acceptance:** the report renders from stored rows only; calibration is a reliability curve with sample sizes.

**Phase 3 exit criteria**

- Ranking is driven by verified reuse, not stars.
- Every new tool can get a capped, funded trial and graduate or retire on evidence.
- No provider can buy rank.
- Every high-risk call still passes the Phase 1 gate.

## Dependency graph

```
P1-01 ─┐
P1-02 ─┼─→ P1-03 ─┐
P1-08 ─┤           ├─→ P1-04 ─→ P1-11
P1-09 ─┘           │
P1-05 ─────────────┤
P1-06 ─────────────┤
P1-07 ─────────────┘

P1-10 ─→ P2-01 ─→ {P2-02, P2-03, P2-04} ─→ P2-05 ─→ P2-06 ─→ P2-07 ─→ P2-08

P2-08 ─→ P3-01 ─┐
          P3-02 ─┼─→ P3-03 ─→ P3-04
          P3-05 ─┘
P1-04 ─→ P3-06 ─→ P3-07
```

P1-04 may be built in parallel with P1-05/06/07 against the frozen interfaces above; it merges after they exist.
P2-03 also needs P1-04 (it must go through the real gate).

Parallel-safe groups:

- **Group A (no `server.py`):** P1-01, P1-02, P1-03, P1-08, P1-09, P1-10.
- **Group B (gate chain):** P1-04 → P1-05 → P1-06 → P1-07, serialized by interface, not by file.
- **Group C (Phase 2):** P2-01 → {P2-02, P2-03, P2-04} → P2-05 → P2-06 → P2-07 → P2-08.
- **Group D (Phase 3):** P3-01, P3-02, P3-05 in parallel; P3-03 after P3-01/P3-02; P3-04 after P3-03; P3-06 serialized
  on `server.py`; P3-07 last.

## Status

Update only your own row. `—` means unclaimed.

| ID | Milestone | Owner | State | PR / issue |
|---|---|---|---|---|
| P1-01 | Security and intent Goals | — | open | |
| P1-02 | Router hardening | — | open | |
| P1-03 | Mapping v2 and manual pins | — | open | |
| P1-04 | Gate core (`server.py`) | — | open | |
| P1-05 | Policy store | — | open | |
| P1-06 | Intent certificates | — | open | |
| P1-07 | Signed audit | — | open | |
| P1-08 | Exa adapter | — | open | |
| P1-09 | Hinoki adapter | — | open | |
| P1-10 | Run recording | — | open | |
| P1-11 | Approval surface | — | open | |
| P2-01 | Corpus and contracts | — | blocked on Phase 1 | |
| P2-02 | Procedure runner | — | blocked on P2-01 | |
| P2-03 | Tool runner | — | blocked on P2-01 | |
| P2-04 | Scorers | — | blocked on P2-01 | |
| P2-05 | Orchestrator | — | blocked on P2-02..04 | |
| P2-06 | Comparison store | — | blocked on P2-05 | |
| P2-07 | Statistics and report | — | blocked on P2-06 | |
| P2-08 | Regression suite | — | blocked on P2-07 | |
| P3-01 | Trust posterior | — | blocked on Phase 2 | |
| P3-02 | Provider-funded trials | — | blocked on Phase 2 | |
| P3-03 | Ranking integration | — | blocked on P3-01/02 | |
| P3-04 | Graduation and retirement | — | blocked on P3-03 | |
| P3-05 | Verification market | — | blocked on Phase 2 | |
| P3-06 | Pricing and metering (`server.py`) | — | blocked on P1-04 | |
| P3-07 | Market report | — | blocked on Phase 3 | |

## File ownership index

A file may appear in exactly one open milestone. If you need to change it, coordinate with the owner.

| File / glob | Owner |
|---|---|
| `backend/app/tool_routing/goals.py`, `backend/db/187_*` | P1-01 |
| `backend/app/tool_routing/goal_router.py`, `backend/scripts/search_goals.py` | P1-02 (then P3-03) |
| `backend/scripts/map_tool_cards_to_goals.py`, `backend/scripts/report_goal_coverage.py` | P1-03 |
| `backend/app/tool_routing/gate.py`, `backend/app/mcp_server/server.py` | P1-04 (then P3-06) |
| `backend/app/tool_routing/policy.py`, `backend/db/188_*` | P1-05 |
| `backend/app/tool_routing/certificates.py`, `backend/db/189_*` | P1-06 |
| `backend/app/tool_routing/audit.py`, `backend/db/190_*`, `backend/scripts/verify_audit_chain.py` | P1-07 |
| `backend/app/tool_providers/exa/**` | P1-08 |
| `backend/app/tool_providers/hinoki/**`, `backend/scripts/register_hinoki_card.py` | P1-09 |
| `backend/app/tool_routing/runs.py`, `backend/db/191_*` | P1-10 (then P2-06) |
| `routing_kell_frontend/components/tools/**`, `routing_kell_frontend/app/api/tools/approve/**`, `desktop/**/tools/approve/**` | P1-11 |
| `backend/scripts/compare/contracts.py`, `corpus.py`, `tasks/**` | P2-01 |
| `backend/scripts/compare/procedure_runner.py` | P2-02 |
| `backend/scripts/compare/tool_runner.py` | P2-03 |
| `backend/scripts/compare/scoring.py` | P2-04 |
| `backend/scripts/compare/run_compare.py` | P2-05 |
| `backend/scripts/compare/store.py`, `backend/db/191_*` | P2-06 |
| `backend/scripts/compare/stats.py`, `report.py` | P2-07 |
| `backend/tests/test_compare_e2e_offline.py`, `backend/tests/fixtures/compare/**` | P2-08 |
| `backend/app/tool_routing/trust.py`, `backend/db/192_*` | P3-01 |
| `backend/app/tool_routing/trials.py`, `backend/db/193_*` | P3-02 |
| `backend/app/tool_routing/retrieve.py` | P3-03 |
| `backend/app/tool_routing/lifecycle.py`, `backend/scripts/tool_lifecycle.py` | P3-04 |
| `backend/app/tool_routing/verification.py`, `backend/db/194_*` | P3-05 |
| `backend/app/tool_routing/pricing.py`, `backend/db/195_*` | P3-06 |
| `backend/scripts/tool_market_report.py`, `docs/tool_market_results.md` | P3-07 |

## What "done" means per phase

| Phase | Done when |
|---|---|
| 1 | A security prompt and a web prompt each route to the right goal and tool; every call has a decision and a verifiable receipt; Exa runs keyless-to-the-user; Hinoki is R3-gated. |
| 2 | A reproducible Python harness compares tool vs procedure vs baseline on 60 tasks, with paired deltas and confidence intervals, and a report per goal. |
| 3 | Ranking is driven by verified reuse; trials are provider-funded and capped; bad tools retire; verification is continuous and cannot be bought. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agents collide on `server.py` | Serialized ownership (P1-04, then P3-06); everyone else imports the gate |
| Gate blocks legitimate calls during rollout | `TOOL_GATE=shadow` first; compare decisions before enforcing |
| Trial exploration harms users | Cap share per goal and user; R0/R1 only; automatic fallback to the next tool |
| Comparison overfits 60 tasks | Paired design, 3 repeats, CIs, a held-out task set, and no winner from one run |
| Judge bias | Blind to arm and task id; tests/exact match first |
| Provider-funded trials become pay-to-win | Hard share cap, distinct-user counting, audits, no rank purchase |
| Static trust sneaks back in | `stars`/`verified` are metadata only; ranking reads the posterior |
| Hinoki misuse | Defensive surface only in v1; R3 approval; public data; no live targets |

## Machine-readable manifest

Agents may parse this block to claim work. Keep it in sync with the status table.

```yaml
version: 1
updated: 2026-11-11
phases:
  - id: P1
    name: foundation
    exit: "route + gate + bind + record; Exa keyless; Hinoki R3-gated"
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
    acceptance: "subgoals seeded; original 25 unchanged"
  - id: P1-02
    title: router hardening
    owns: [backend/app/tool_routing/goal_router.py, backend/scripts/search_goals.py]
    depends: []
    acceptance: "--explain shows legs; exact beats fts; empty query returns []"
  - id: P1-03
    title: mapping v2 and manual pins
    owns: [backend/scripts/map_tool_cards_to_goals.py, backend/scripts/report_goal_coverage.py]
    depends: []
    acceptance: "pins survive --replace; coverage report prints orphans"
  - id: P1-04
    title: gate core
    owns: [backend/app/tool_routing/gate.py, backend/app/mcp_server/server.py]
    depends: [P1-05, P1-06, P1-07]
    acceptance: "enforce denies R3 without cert; shadow logs only; fail closed"
  - id: P1-05
    title: policy store
    owns: [backend/app/tool_routing/policy.py, backend/db/188_tool_policies.sql]
    depends: []
    acceptance: "deny wins; R0 allow; R2/R3 approve; unknown deny"
  - id: P1-06
    title: intent certificates
    owns: [backend/app/tool_routing/certificates.py, backend/db/189_intent_certificates.sql]
    depends: []
    acceptance: "expired/revoked/widened fail; valid passes"
  - id: P1-07
    title: signed audit
    owns: [backend/app/tool_routing/audit.py, backend/db/190_tool_call_audit.sql]
    depends: []
    acceptance: "chain verifies; tamper fails at the row"
  - id: P1-08
    title: Exa adapter
    owns: [backend/app/tool_providers/exa]
    depends: []
    acceptance: "mock search/fetch; missing key refuses clearly"
  - id: P1-09
    title: Hinoki adapter
    owns: [backend/app/tool_providers/hinoki]
    depends: []
    acceptance: "four read-only tools; no cert means no call"
  - id: P1-10
    title: run recording
    owns: [backend/app/tool_routing/runs.py, backend/db/191_task_runs.sql]
    depends: []
    acceptance: "start/finish/read round-trip; errors recorded"
  - id: P1-11
    title: approval surface
    owns: [routing_kell_frontend/components/tools, routing_kell_frontend/app/api/tools/approve]
    depends: [P1-04]
    acceptance: "approve once allows one matching call; hash change invalidates"
  - id: P2-01
    title: corpus and contracts
    owns: [backend/scripts/compare/contracts.py, backend/scripts/compare/corpus.py, backend/scripts/compare/tasks]
    depends: [P1]
    acceptance: "60 tasks validate; duplicate ids rejected"
  - id: P2-02
    title: procedure runner
    owns: [backend/scripts/compare/procedure_runner.py]
    depends: [P2-01]
    acceptance: "unbound step marked, not passed"
  - id: P2-03
    title: tool runner
    owns: [backend/scripts/compare/tool_runner.py]
    depends: [P2-01, P1-04]
    acceptance: "gate refusal recorded as error_class gate:denied"
  - id: P2-04
    title: normalizer and scorers
    owns: [backend/scripts/compare/scoring.py]
    depends: [P2-01]
    acceptance: "each scorer pass+fail; judge blind"
  - id: P2-05
    title: orchestrator
    owns: [backend/scripts/compare/run_compare.py]
    depends: [P2-02, P2-03, P2-04]
    acceptance: "2x2x2 run writes 8 runs and 2 comparisons"
  - id: P2-06
    title: comparison store
    owns: [backend/scripts/compare/store.py, backend/db/191_task_runs.sql]
    depends: [P2-05]
    acceptance: "upsert by task/procedure/card; query by goal"
  - id: P2-07
    title: statistics and report
    owns: [backend/scripts/compare/stats.py, backend/scripts/compare/report.py]
    depends: [P2-06]
    acceptance: "known effect recovered; zero effect CI contains 0"
  - id: P2-08
    title: comparison regression suite
    owns: [backend/tests/test_compare_e2e_offline.py, backend/tests/fixtures/compare]
    depends: [P2-07]
    acceptance: "offline e2e under 60s"
  - id: P3-01
    title: trust posterior
    owns: [backend/app/tool_routing/trust.py, backend/db/192_tool_trust.sql]
    depends: [P2]
    acceptance: "1/1 ranks below 90/100"
  - id: P3-02
    title: provider-funded trials
    owns: [backend/app/tool_routing/trials.py, backend/db/193_tool_trials.sql]
    depends: [P2]
    acceptance: "credits never negative; caps hold; no R3 trials"
  - id: P3-03
    title: ranking integration
    owns: [backend/app/tool_routing/goal_router.py, backend/app/tool_routing/retrieve.py]
    depends: [P3-01, P3-02]
    acceptance: "proven wins; trial only in share; flag restores P1 order"
  - id: P3-04
    title: graduation and retirement
    owns: [backend/app/tool_routing/lifecycle.py, backend/scripts/tool_lifecycle.py]
    depends: [P3-03]
    acceptance: "graduate/watch/retire transitions exact"
  - id: P3-05
    title: verification market
    owns: [backend/app/tool_routing/verification.py, backend/db/194_verification.sql]
    depends: [P2]
    acceptance: "forged signature rejected; verifier reputation tracks outcomes"
  - id: P3-06
    title: pricing and metering
    owns: [backend/app/tool_routing/pricing.py, backend/db/195_tool_listings.sql, backend/app/mcp_server/server.py]
    depends: [P1-04]
    acceptance: "debit once; trial debits provider; refuse before network"
  - id: P3-07
    title: market report
    owns: [backend/scripts/tool_market_report.py, docs/tool_market_results.md]
    depends: [P3-04, P3-06]
    acceptance: "calibration curve with sample sizes"
```
