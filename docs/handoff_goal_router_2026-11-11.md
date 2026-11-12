# Goal-router handoff — 2026-11-11

**Read this with `docs/goal_router.md`.** The plan is the map; this file is the state at handoff.
The only prompt the next instance needs is:

> read docs/goal_router.md and complete it

Then read this handoff before touching anything. Main moves fast: **`git pull --rebase origin main` first**, check the
status table and the open issues, and only then claim a milestone.

State described here: `2ffb2bcf`. This handoff file landed in `dfd2f4bd`; run `git pull --rebase origin main` first.

**Update after `1839f665`:** #34 and #29 are fixed and closed (fail-closed `authorize()`, adapter
dispatch in `recommend_goal --call`, environment-first DSN, signing refuses the dev key off
localhost, and `db/197_tool_call_audit_append_only.sql`). Section 3 below is kept as the record of
what was wrong; do not redo it. `197` is new and must be applied with `187-196`.

---

## 1. Where the project is

| Phase | State |
|---|---|
| **Phase 1 — Foundation** | **Done except P1-09.** P1-01…P1-08, P1-10, P1-11 are merged. Hinoki (P1-09) is blocked on the API base/schema, not the credential. |
| **Phase 2 — Evidence** | **Done.** P2-01…P2-07 are merged: corpus, procedure runner, tool runner, scorers, orchestrator, comparison store, paired statistics and per-goal report. The old reports predate the materialized ways (56,741 one-step tool procedures); the procedure arm now runs the top one through the same gate as the tool arm, so re-run the comparison. |
| **Phase 3 — Market** | **Done.** P3-01…P3-07 are merged: trust posterior, funded trials, trust ranking, graduation/retirement, verification market, pricing/metering and the market report. |

Current board (from `docs/goal_router.md`):

- **Done:** P1-01…P1-08, P1-10, P1-11; P2-01…P2-07; P3-01…P3-07.
- **In progress:** none.
- **Blocked:** P1-09 (Hinoki API base/schema) only.

---

## 2. What this agent (opencode) built

| Milestone | Commit | What landed |
|---|---|---|
| P1-01 | `474cd11f` | Security subgoals in `app/tool_routing/goals.py`; `db/187_tool_goals_security.sql` seeds them on K000. |
| P1-02 | `474cd11f` | `scripts/recommend_goal.py` (the Phase 1 harness); `search_goals.py` is now a wrapper; `goal_router.route_goal(explain=True)` and `GoalHit.trace`. |
| P1-03 | `474cd11f` | `map_tool_cards_to_goals.py --pin/--report`; manual pins survive `--replace`; `report_goal_coverage.py`; Exa pinned to the web goal at 9.0. |
| P1-04 | `4bd42342` | `app/tool_routing/gate.py`; `scripts/check_gate.py`; gate wired into `_gateway_call` and `use_tool`; `TOOL_GATE=off / shadow / enforce`; `certificates.load`; fixed the dev signing secret. |
| P1-05/06/07 | `106e11f4` | `policy.py` + `db/188`; `certificates.py` + `db/189`; `audit.py` + `signing.py` + `db/190`; `check_policy.py`, `issue_cert.py`, `check_cert.py`, `verify_audit_chain.py`, `audit_demo.py`. |
| P1-11 | `c43cbb09` | `approvals.py` + `db/196`; gate approval branch; `approve_tool.py`; `/v1/tools/approvals` (web) and `/tools/approvals` (desktop); web/desktop `ApprovalCard`; web `RoomView` banner. |
| P2-03 | — | **Anuj's `72b0c4fb` stands.** This agent's duplicate runner was dropped; a copy is in `.scratch/tool_runner_ours.py` (untracked, do not commit). |
| Hinoki | — | `HINOKI_API_KEY` added to `backend/.env` (git-ignored). `.env.example` placeholders added. Issue #26 updated. |

The important review context is in **#34** and **#29**; read both before deploying or changing the gate/signing.

---

## 3. Fix these first (open review findings)

### #34 — P1-04 review: deploy blocker

`gate_mode()` defaults to `shadow` when `TOOL_GATE` is unset, and `authorize()` calls `policy.evaluate()` /
`certificates.verify()` **without a try/except**. Production has not applied `db/188-190`; the first
`use_tool(server=...)` would raise `UndefinedTableError` out of `_gateway_call` and break every outside-tool call.

Before any deploy:

1. Set `TOOL_GATE=off` on the service **or** apply migrations `187…196` first.
2. Wrap the gate body in `try/except`: a policy/certificate/DB failure must return a **deny decision** (R2+; everything
   with `TOOL_GATE_STRICT=on`), not raise. Write a receipt when the audit sink is reachable.
3. `goal_id` is always `None` in `_gateway_call`, so goal-scoped policies never match. Use
   `store.recent_goal(owner)` (a uuid5 tool-goal key) or thread the task's goal through.
4. Direct `V1_TOOLS` are not gated; the plan says to gate them too. Add a `server='kell'` policy or a deliberate
   exemption.
5. `recommend_goal --call` calls `gateway.call_tool` on the card's connector, so Exa goes to `mcp.exa.ai` instead of
   Anuj's P1-08 adapter on kell's key. Use `app.tool_providers.get(server)` like `tool_runner` does.
6. Scripts read `backend/.env` before the environment (`dsn_from_env`), so an exported `DATABASE_URL` pointing at a
   scratch DB is ignored. Make environment-first in `recommend_goal`, `issue_cert`, `check_cert`, `check_gate`,
   `approve_tool`, `verify_audit_chain`, `audit_demo`.
7. The approval path now exists (`tool_approvals`), but `ToolCall` has no `approval_id` field; the gate finds/consumes
   by call-shape hash. Verify the flow and decide whether an explicit id is needed for the UI.

### #29 — signing secret and append-only audit

`signing.py` uses a fixed development key when `TOOL_CERT_SECRET` is unset. Anyone with the repo knows it. Suggested
fix: allow the dev key only when `KELL_DEV=1` or the DSN host is localhost; otherwise raise, and let the gate deny R2+
when signing is unavailable. Also add a `BEFORE UPDATE OR DELETE` trigger on `tool_call_audit` that raises, so receipts
are append-only for the app role.

**Setting `TOOL_CERT_SECRET` invalidates every existing certificate and audit receipt.** Set it once, before real data,
and then reset the test rows if you want a clean chain.

---

## 4. Environment and local database

- Windows, Python 3.13; Next.js in `routing_kell_frontend` and `desktop`.
- `backend/.env` is **git-ignored** (`git ls-files backend/.env` is empty). It contains:
  - `EXA_API_KEY` — set; Anuj's P1-08 adapter uses it.
  - `HINOKI_API_KEY` — set (kell-managed; **never commit it**).
  - `TOOL_CERT_SECRET` — **unset**; `KELL_DEV=1` is set, so the fixed dev key signs certs/receipts.
- `backend/.env.example` documents `EXA_API_KEY`, `HINOKI_API_KEY`, `TOOL_CERT_SECRET`, `KELL_DEV` and
  `TOOL_GATE` / `TOOL_GATE_STRICT`.
- Migrations applied locally: `187`, `188`, `189`, `190`, `191` (including `comparison_results`), `192`
  (`tool_trust`), `196` (`tool_approvals`), `197` (append-only audit), `193` (trials), `194` (verification),
  `198` (OSV policy), `199` (procedure↔card mapping).
- **Not applied locally:** none; `187-198` are all present.
- `TOOL_CERT_SECRET` is unset and this machine's `DATABASE_URL` is Neon, not localhost, so
  `backend/.env` has `KELL_DEV=1` (git-ignored) to keep the manual checks working. A deployment must
  set `TOOL_CERT_SECRET` instead; without either, the gate denies R2+.
- Local test data: 40+ audit receipts, several intent certificates, pending/used approvals for `demo-5`/`demo-6`, several
  `task_runs` rows from P2-03. All are `demo`/`dev-local`; clean them if you need a fresh chain.
- `tool_trust` is a **materialized view**. Refresh with `SELECT refresh_tool_trust();`.
- The `search group: 6 search members are registered but none of their connection strings is set` warning is benign
  on this local DB. Exact `goal_names` lookup works; the K000 full-text fallback covers the 25 taxonomy Goals.
- `comparison_results` and `tool_trust` were applied manually after Anuj's commits; other environments must run
  `db/191` and `db/192`.

---

## 5. Verify in ten minutes

From `backend/`:

```powershell
# 1. Routing
python scripts/recommend_goal.py "search the web for the latest Postgres release" --explain
#    web goal, Exa first (score 9.0)
python scripts/recommend_goal.py "scan this repository for known CVEs in dependencies" --explain
#    "Find known CVEs..." first

# 2. Gate modes
$env:TOOL_GATE = "enforce"
python scripts/check_gate.py --server ai.exa/exa --tool web_search_exa --owner dev-local     # allow
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local        # deny cert:required
$env:TOOL_GATE = "shadow"
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local        # allow, shadow:deny
Remove-Item Env:TOOL_GATE

# 3. Audit chain
python scripts/audit_demo.py --count 2 --tamper
#    verify ok; tampered FAILED; restored ok
python scripts/verify_audit_chain.py --since 2h
#    ok, N receipts

# 4. Approvals
# .scratch/hinoki_constraint.json
# {"repo_url":{"required":true,"pattern":"^https://github.com/"}}

$out = python scripts/issue_cert.py --task demo-5 --owner dev-local --risk R3 `
    --server hinoki --tool scan_repository --constraint-file ..\.scratch\hinoki_constraint.json
$cert = (($out | Select-String '^cert:').Line -split '\s+')[1]
$env:TOOL_GATE = "enforce"
$g = python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local `
    --cert $cert --task demo-5 --arg repo_url=https://github.com/org/repo
#    effect approve, approval:ap_...
$ap = (($g | Select-String '^approval:').Line -split ':', 2)[1].Trim()
python scripts/approve_tool.py --approve $ap --owner dev-local
python scripts/check_gate.py --server hinoki --tool scan_repository --owner dev-local `
    --cert $cert --task demo-5 --arg repo_url=https://github.com/org/repo   # allow, approval:<id>
Remove-Item Env:TOOL_GATE

# 5. Phase 2
python -m scripts.compare.corpus --check
python -m scripts.compare.tool_runner --task web-001          # pass, Exa, cost reported
python -m scripts.compare.tool_runner --task sec-001 --no-cert # error, gate:denied
python -m scripts.compare.stats --selfcheck
```

TypeScript:

```powershell
cd routing_kell_frontend; npx tsc --noEmit
cd ..\desktop; npx tsc --noEmit
```

---

## 6. Key files and ownership

**Plan / status:** `docs/goal_router.md` (status table, frozen interfaces, file ownership, YAML manifest).

**Backend routing and gate**

- `backend/app/tool_routing/goal_router.py` — goal → ways → tools; `explain=True` fills `GoalHit.trace`.
- `backend/app/tool_routing/goals.py` — 25 tool goals + 5 security subgoals.
- `backend/app/tool_routing/policy.py` — deterministic policy evaluation.
- `backend/app/tool_routing/certificates.py` — issue/verify/revoke/load.
- `backend/app/tool_routing/signing.py` — canonical JSON + HMAC.
- `backend/app/tool_routing/audit.py` — hash-chained receipts.
- `backend/app/tool_routing/gate.py` — `authorize()`, `TOOL_GATE` modes, approval branch.
- `backend/app/tool_routing/approvals.py` — pending/approved/denied/used approvals.
- `backend/app/tool_routing/trust.py` — Beta posterior lower bound (P3-01).
- `backend/app/tool_routing/runs.py` — `task_runs` start/finish/refusal (P1-10).

**Migrations**

- `186` card→goal, `187` security subgoals, `188` policies, `189` certificates, `190` audit, `191` task runs +
  comparison results, `192` trust view, `196` approvals, `193` trials, `194` verification, `195` listings,
  `197` append-only audit, `198` OSV policy, `199` procedure↔card mapping, `200` goal tool rankings,
  `201` approval tiers, `202` moderation promoted action, `203` tool-card vector index.

**Scripts**

- `recommend_goal.py`, `search_goals.py`, `map_tool_cards_to_goals.py`, `report_goal_coverage.py`.
- `materialize_tool_procedures.py` — turns tool cards into one-step Ways (procedures) for their Goals.
- `refresh_goal_rankings.py` — recomputes the materialized per-Goal tool order (db/200); run after new evidence.
- `review_procedures.py` — moves a Way through `submitted` → `proposed` → `approved` (db/201); `--bulk-generated` for the tool-card ways.
- `check_policy.py`, `issue_cert.py`, `check_cert.py`, `check_gate.py`, `verify_audit_chain.py`, `audit_demo.py`,
  `approve_tool.py`.
- `backend/scripts/compare/` — corpus, procedure_runner, tool_runner, scoring, run_compare, store, stats, report.

**Server / routes**

- `backend/app/mcp_server/server.py` — **serialized**. It holds the gate wiring and the desktop `/tools/approvals`
  routes. P3-06 is the next milestone that needs it.
- `backend/app/mcp_server/room_agent_routes.py` — `/v1/tools/approvals` (website session).

**Frontends**

- `routing_kell_frontend/lib/tools/approvals.ts` — approval client.
- `routing_kell_frontend/components/tools/ApprovalCard.tsx` — web card.
- `routing_kell_frontend/components/chat/RoomView.tsx` — polls approvals and renders the cards.
- `desktop/app/page.tsx` — renders the pending-approval cards above the composer (polls `/api/tools/approve`).
- `desktop/app/api/tools/approve/route.ts` — desktop proxy.
- `desktop/components/chat/ApprovalCard.tsx` — desktop card.
- `desktop/lib/tools.ts` — `toolApprovals`, `approveTool`, `denyTool`.

**Providers**

- `backend/app/tool_providers/exa/` — Anuj's P1-08 adapter (kell-managed key).
- `backend/app/tool_providers/hinoki/` — **missing** (P1-09); the inference API is an LLM, not a scanner.
- OSV — no adapter needed: the live public card `registry:io.github.cyanheads/osv-advisory-mcp-server`
  runs through the gateway (db/198 policy + a manual pin to the CVE goal).

---

## 7. Known gaps and traps

1. **Hinoki's inference API is known; the scanner API is not.** `https://inference.hinoki.security/v1`,
   model `hinoki-offensive`, Bearer `HINOKI_API_KEY` (#26). It is an LLM, stateless, with no repository
   access, so the planned `scan_repository(repo_url)` needs a different design (diff/code review is the
   natural surface). No adapter or card is registered, and no call is made without the key owner's
   consent (limited credits). OSV now serves the dependency/CVE subgoal keylessly: db/198 adds the
   policy row and a manual pin maps `registry:io.github.cyanheads/osv-advisory-mcp-server` to the CVE goal
   (`python scripts/map_tool_cards_to_goals.py --pin registry:io.github.cyanheads/osv-advisory-mcp-server c1aedb7a-fc53-50a3-ae21-3cfdeb756c52 9.0 --pin-only`).
2. **Desktop approval card is rendered** above the composer (`desktop/app/page.tsx`), polling the
   `/api/tools/approve` proxy every 15 s and on focus, mirroring `RoomView.tsx`.
3. **`recommend_goal --call` uses the Exa adapter** (fixed in `1839f665`), not `mcp.exa.ai` directly.
4. **Gate defaults to shadow.** Production must set `TOOL_GATE=off` until migrations are applied, then `enforce`.
5. **`goal_id` is `None` in `_gateway_call`.** Goal-scoped policies do not match.
6. **`TOOL_CERT_SECRET` is unset locally, `KELL_DEV=1` is.** The dev key is refused on any deployment
   whose `DATABASE_URL` is not localhost; set `TOOL_CERT_SECRET` before production (invalidates old rows).
7. **`tool_approvals` sweeper is in.** `approvals.expire()` marks pending rows past their window as
   `expired`; `list_pending` calls it lazily (the cards poll it) and `approve_tool.py --expire` runs it
   from cron. Expired rows were already unusable; this is table hygiene.
8. **Audit writes are serialized** by a transaction-scoped advisory lock. Fine now; a bottleneck at high volume.
9. **`comparison_results` / `tool_trust` were applied manually.** Run `db/191` and `db/192` on every environment.
10. **P2-03 is Anuj's runner.** Do not re-add a second `tool_runner.py`; our duplicate is in
    `.scratch/tool_runner_ours.py` (untracked, do not commit).
11. **The four non-CVE security subgoals have no Ways.** The generic `security` Goal has 2,919
    one-step ways; `security_secret_scan`, `security_sast`, `security_auth_review` and `security_pentest`
    have 0 cards, so 0 ways. The mapper's `TOOL_GOAL_TITLES` still lists only the original 25, and no
    card is tagged with a subgoal. Pin a real scanner/reviewer to each (or finish the model-backed
    reviewer) before those prompts can show a tool.
12. **Test rows are real rows.** The audit chain includes `demo`/`dev-local` receipts; certificates and one pending
    approval exist. Clean them if you set a real `TOOL_CERT_SECRET`.

---

## 8. Coordination

- One agent per milestone. Open a GitHub issue `goal-router: <ID> <short name>`, assign yourself, branch
  `goal-router/<ID>-<slug>`, edit only your `Owner` cell.
- `server.py` is serialized. P3-06 is next; do not edit it while another milestone holds it.
- Push after every milestone and at least every 45 minutes. Always `git pull --rebase origin main` before pushing.
  Never force-push, never commit `.env`/`.scratch`/secrets.
- If two agents claim the same ID, the earlier issue wins. Comment with the merged commit and ask the other to review
  rather than rebuild (this happened on P1-02 #24, P1-04 #33, P1-07 #28).
- The doc's machine-readable YAML manifest at the bottom of `docs/goal_router.md` is the quickest way to parse the
  board.

---

## 9. Suggested next order

1. **Fix #34 and #29** — **done in `1839f665`** (fail-closed gate, environment-first DSN, adapter path,
   signing secret, append-only trigger).
2. **Integrate the desktop approval card** — **done** in `desktop/app/page.tsx` (polls `/api/tools/approve`).
3. **P3-02…P3-07** — **done** (`a46be874`…`c673810f`); Phase 3 exit criteria verified on `main`.
4. **P1-09** — the only row left, blocked on the Hinoki API base/schema (#26). Do not invent an endpoint.

---

## 10. Recent commits and issues

Recent commits on `main`:

```
bc937aaa feat(desktop): render pending tool approvals above the composer
c673810f goal-router P3-07: the tool market report
299af453 goal-router P3-06: priced tools metered against the person's own plan wallet
b96b949c goal-router P3-05: verification market (signed results, earned weight, anchors, audits)
eae5709f goal-router P3-04: graduation and retirement from evidence
1839f665 fix(tool_routing): fail-closed gate, adapter dispatch, signing guard (#34, #29)
522f1887 run_compare: the baseline retries a rate limit or a busy model ...
3882975f goal-router P3-03: rank a goal's tools by verified reuse
a46be874 goal-router P3-02: provider-funded trials
80fddfbf goal-router P3-01: trust posterior from verified reuse
88443b14 goal-router P2-07: paired statistics and the per-goal report
```

Open issues: #26 (Hinoki) only. #29/#34 are fixed in `1839f665` and closed; #40-#46 (P3-02…P3-07) are
merged and closed. The only open PRs are dependabot dependency bumps; no goal-router PR is open.

Phase exit criteria re-checked on `main` after `bc937aaa`: corpus 60/6 with verifiers, stats selfcheck ok,
security and web prompts route to the right Goal, R0 passes and R3 refuses without a certificate in
`enforce`, the audit chain verifies, `tool_trust`/`trials`/`lifecycle`/`verification`/`tool_market_report`
all run. The one gap is the P1-09 tool for the security subgoal (Hinoki).

---

## 11. The handoff prompt

> Read `docs/goal_router.md` and `docs/handoff_goal_router_2026-11-11.md`. Fix #34 and #29 first. Then take the first
> unclaimed, unblocked milestone in the status table, claim it with an issue, and push each milestone to `main`.

Do not rebuild P1-02, P1-04, P1-07, P2-03, P2-05, P2-06, P2-07 or P3-01; they are merged. If a claim conflicts, the
earlier issue wins — comment with the merged commit instead.
