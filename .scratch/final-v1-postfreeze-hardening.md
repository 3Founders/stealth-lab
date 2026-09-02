# Post-Freeze Security Hardening — v1-final-2026-09-03.1

## Lineage

| Point | Commit | Tag |
|---|---|---|
| Historical baseline | `a5dace6ccbe52c7669e13fa0efe8eb17448d05a8` | `v1-baseline-2026-09-02` |
| Historical frozen Final V1 | `d0b173c11553ca0bfa9562712344d2c4bc84fe8c` | `v1-final-2026-09-03` (unchanged) |
| Post-freeze security hardening / current launch candidate | the commit tagged `v1-final-2026-09-03.1` (`git rev-parse v1-final-2026-09-03.1^{commit}`) | `v1-final-2026-09-03.1` |

`v1-final-2026-09-03` (`d0b173c`) stays exactly as frozen — it is the
historical frozen Final V1, not the launch commit. The launch candidate is
the `.1` patch tag on top of it.

## Security issue

The public MCP tool `apply_change_set` was an **ungated arbitrary
knowledge-graph write**: any caller holding a valid bearer token could
apply a hand-constructed `change_set` directly against the graph. There
was **no persisted approval** and **no audit row** — the write did not
pass through the `approvals` table, did not check debate state, and did
not require an `APPROVED` scorecard or a `status='proposed'` decomposition.

## Root cause

`apply_change_set` shipped as a bare `@server.tool()` in
`app/mcp_server/server.py`, registered alongside the two gated mutation
tools `submit_approval` (debate scorecards) and `decide_decomposition`
(decomposition proposals). `CLAUDE.md` describes the intended posture as
"stays behind an opt-in flag and does not ship public", but that posture
was **never enforced by an actual flag** — the tool was live in
`tools/list` on every deployment.

## Fix

- Removed the public `apply_change_set` MCP tool and its now-dead imports
  from `app/mcp_server/server.py`.
- The internal mutation implementation, `KnowledgeUpdater`, is **kept** —
  it is now reachable **only** from:
  - `app/api/approval.py::decide` — debate scorecards. Requires a
    persisted `scorecards` row, the debate state machine gate, actor
    resolution, and writes an `approvals` audit row. Applies the
    **stored** change_set.
  - `app/api/decompose.py::decide` — decomposition proposals. Requires a
    persisted `decompositions` row behind a `status='proposed'` gate,
    re-runs `validate_generative()` (the capability-boundary check) at
    apply time, actor resolution, audit write. Applies the **stored**
    change_set.
- The change_set that is applied is always the persisted / stored one,
  **never caller-supplied** at apply time.
- No new approval system was introduced.

## Approval model

**Unchanged.** Same `approvals` table, same scorecard state machine, same
`decompositions.status` gate. The hardening only removes an ungated bypass
around that model; it does not modify the model.

## Public MCP surface change

- **30 → 29 tools.**
- `tools/list` no longer exposes `apply_change_set`.
- No other tool's signature or behaviour changes.

## Tests

New:
- `backend/tests/test_apply_change_set_removed_security.py` — **21 offline**.
  Covers: tool absent from `list_tools()` + `tools/list` (total 29); no
  module-level `server.apply_change_set`; AST scan asserting
  `KnowledgeUpdater` is imported by **exactly** `{app/api/approval.py,
  app/api/decompose.py}` and `server.py` imports neither it nor
  `apply_debate_result` (the "no second hidden route" guard); `approval.decide`
  missing → 404 / non-`PENDING_APPROVAL` → 409 with no mutation, no
  `approvals` insert; `decompose.decide` `status != 'proposed'` → 409 with
  nothing applied, missing → 404; `DecideRequest` / `ApprovalRequest` have
  no `ops` / `change_set` field (a smuggled kwarg is dropped, never read);
  both gated paths apply the **stored** `row["change_set"]` verbatim;
  resolved OIDC actor beats a spoofed `approver_id` in both paths; failed
  apply → plain 409 leaking no `token` / `password` / `api_key` / `secret` /
  DSN material; re-decide of a decided proposal → 409, not a double-apply;
  the 29 names include the expected authorized non-write tool set.
- `backend/tests/test_apply_change_set_removed_e2e.py` — **2 vs Supabase**
  (`skipif(not DATABASE_URL)`). A real `PENDING_APPROVAL` scorecard through
  `submit_approval` → `approval.decide` still applies + writes an
  `approvals` row (`approver_id` + `applied_ops` from the stored change_set
  + `decided_at`/`applied_at`); a real `status='proposed'` decomposition
  through `decide_decomposition` → `decompose.decide` still applies + sets
  `status='approved'` + `approver_id` + `decided_at` and creates the
  expected `public_generated` task_nodes; a re-decide is 409 with no
  second apply.

Changed:
- `packaging/tests/test_server_offline.py::test_all_registered_tools` — the
  asserted sorted tool list drops `"apply_change_set"` (29 entries).
- `backend/test_apply_change_set_live.py` — hand-run probe (never collected;
  `test_live_scripts_not_collected.py` guards that) reduced to a
  historical-marker `SystemExit`.

Suite results after the change: full offline backend **2139 passed / 289
skipped / 0 failed**; targeted `-k` slice **73 passed / 12 skipped**;
new security file alone **21 passed**; the 2 e2e **passed**; packaging
**95 passed**; `test_live_scripts_not_collected` **1 passed**.

Pre-existing, unrelated: `tests/test_state_e2e.py::test_state_delta_reports_the_real_supersession_as_added_and_removed`
fails against the shared live Supabase (a bi-temporal state-delta timing
assertion, last touched 2026-08-19, not on any path this patch changes —
fails identically on `d0b173c`). Same category as the already-registered
`test_ingestion_admin_endpoint_e2e` limitation.
