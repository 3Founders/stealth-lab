# Latency check: how long does routing take?

A prompt for a model (or person) that picks this up. Read it whole before starting.

## Why this exists

We know the latency of a whole dispatched prompt, and we do not know the latency of the routing step on its own.

Measured so far (benchmark harness, not a customer's sessions):

| | Median | p95 |
|---|---|---|
| Arm A (plain Claude Code, Opus), 128 tasks | 33 s | 92 s |
| Arm C (with kell), 128 tasks | 56 s | 286 s |
| Later re-run after the stall fixes, A / C | 15-37 s / 42-55 s | not reported |

Sources: `docs/HANDOFF_abtest_2026-10-11.md` (section 1, and the latency list L1-L3 around line 480), `docs/pilot_plan_lead_2026-10.md`,
`experiments/OPTIMIZATION_SUMMARY.md`. A cheap win adds a median of about 22 s over A. A cheap failure that falls back to Opus
adds +99 s (failed early) to +164 s (waited out the time limit).

The Cursor figure of 45-57 s per dispatched prompt came from simulated payloads. It has not been seen in a real Cursor.

None of these isolate **routing**: the triage verdict plus the model plan, before any cheap model starts working. That is
the number to produce.

## The question

How many milliseconds does it take, p50 and p95, from the moment the hook receives the prompt until the dispatcher either
starts a cheap model or decides not to dispatch? Split it into its parts:

1. **Triage.** The hook sends the prompt and earlier turns to the server's `/triage` judge (`callTriage`,
   `packaging/npm/lib/hook.mjs:307`; the flow is `hook.mjs` around lines 499-626; Cursor does the same in
   `packaging/npm/lib/cursor_dispatch.mjs`, around line 89). One LLM call. Capped by `STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS`
   (default 30000).
2. **Plan request.** `find_ways` over MCP (`hook.mjs` around line 281), which computes the model plan on the server
   (`backend/app/routing/plan.py`). The server already records `plan_ms`, the router's own overhead, in
   `retrieval_decisions.detail` (`backend/app/mcp_server/server.py` around lines 1823 and 2246-2271; see
   `docs/telemetry_plan.md`). The client-side time for the whole `find_ways` call is larger than `plan_ms` (network, repo
   claims, retrieval). Measure both and report the gap.
3. **Gate in `dispatch()`.** Local checks before the race starts. Should be negligible; confirm it.

Report: triage ms, find_ways round trip ms, `plan_ms`, gate ms, total routing ms. All as p50 / p95 / max, with n.

## What to do

1. Claim the item on `.scratch/build-board.md` first, under the lane that owns `packaging/npm/` (the `ship:` lane unless the
   board says otherwise). Stay inside your lane's granted paths. If you need to edit outside them, file a board request.
2. Add timing around the three steps. Keep it **off by default** and behind one environment variable
   (for example `STEALTHLAB_ROUTING_TIMING=1`). When on, append one JSON line per prompt to a file under `.kell/`, or
   under the existing hooks log if there is one. Do not print to the user's prompt. Do not change any routing decision.
3. Measure the real path. Start the MCP/API server locally. It failed to connect (ECONNREFUSED) in the last session, so
   `backend/scripts/start_mcp_local.ps1` is the place to start. Run at least **30 prompts** through the hook, and say which
   kinds you used:
   - code changes that should dispatch;
   - questions and chit-chat (triage returns a non-change verdict);
   - a prompt in a repository with no `.kell` root (no plan path).
4. Run once with a warm server and once cold (first call after a restart). Report them separately. The Bayesian model load
   may dominate the cold number.
5. Run the same prompts against a **remote** server if one is reachable. Otherwise state plainly that the numbers are
   localhost-only and that the network adds a round trip on top. Do not extrapolate.
6. Add or extend a test in `packaging/npm/test/` (see `latency_fixes.test.mjs` for the style) proving that: the timing is
   off by default, nothing is written when it is off, and a triage timeout still fails open with the timing on.
7. Write the results in `docs/latency_routing_results.md`: a table, the exact commands, the date, the commit, and what was
   not measured.

## Rules that apply

- Every error fails open: a timing failure must never block, delay or alter the user's prompt.
- Timings contain no prompt text. Log lengths and hashes if you need to tell prompts apart. Prompts go through
  `backend/app/services/trace_redaction.py::redact_event` before any server-side INSERT of free text.
- Keys live in `backend/.env` and stay there. Do not paste them into the results or the commit.
- `FundingGrants/`, `.claude/traces/` and any `.env` are local-only. Never stage them.
- Do not touch frozen files: spec v4, `schema.md`.
- Report only what you measured. If a number comes from a simulated payload, a mocked model or localhost, say so beside
  the number.
- Commit prefix is the lane tag (`ship:` or `measure:`). Paste the pytest/node-test counts into the commit message.
  Rebase onto `origin/main` before pushing. Never force-push.

## Done means

- The table above, filled in with measured numbers and n for every row, for warm and cold.
- The timing code merged, off by default, with a passing test.
- A one-paragraph answer in the results file: of the median 56 s (or 42-55 s) that arm C takes, how much is routing, how
  much is the cheap model's work, and how much is the check?
- If routing is under 5% of the added time, say so and stop. The latency work belongs in L1-L3 of the handoff (stopping
  stuck cheap runs early), not in routing.
- If routing is over 20% of the added time, list the biggest piece and propose one change, with its expected saving.
  Do not make the change in this item.

## Out of scope

- Starting Opus in parallel as a backup. The handoff rules it out: it pays for Opus on tasks the cheap models win.
- Changing the ladder, the acceptance rule or the triage prompt.
- Real-Cursor testing. It is worth doing, but it needs a person with Cursor installed. Flag it; do not fake it.
