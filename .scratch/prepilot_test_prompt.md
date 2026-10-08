# Prompt: pre-pilot test runs (paste into a fresh Claude Code session at the repo root)

---

You are testing StealthLab before we pitch a 2-week pilot to an analytics firm (their Analytics Head and
Security Head). Read `CLAUDE.md`, `3-hard.md` (including "Handoff notes from the 2026-10-08 session") and
`docs/routing_priors_eval.md` first.

## Goal

Produce **measured numbers** for the claims we want to make, and a list of claims we **cannot** make yet.
Do not write marketing copy. A number we did not measure must never appear as if we did. A loss is reported
as plainly as a win.

## The three runs

Run the same task set and the same measurements three times. Write everything under
`experiments/prepilot/` (code, task set, raw results) and the report at `experiments/prepilot/REPORT.md`.

| Run | Database | Agent model | What it shows |
|---|---|---|---|
| 1 | **No Neon.** Local Postgres + pgvector in Docker (`pgvector/pgvector:pg15`), `python scripts/migrate.py`, seeded with `scripts/bootstrap_demo.py` | default | The product works end to end with no cloud database: install, tool listing, `find_ways`, `call_model`, logging, failover |
| 2 | **Our database**, through a **Neon branch** of production that the owner creates and gives you as a DSN | default | What a real user gets from our actual knowledge |
| 3 | Same as run 2 | **Sonnet** (`--model sonnet`) | Whether a cheaper agent model with StealthLab matches the default model without it |

Rules for run 2 and 3:
- **Never use the production branch.** `find_ways` writes a `retrieval_decisions` row per call, so even a
  "read" writes. Ask the owner for a branch DSN; if you do not have one, stop and say so.
- Keys stay in `backend/.env`. Never print or commit a key or DSN.

Each run has two arms per task, so StealthLab is compared with its absence:
- **baseline**: Claude Code with no MCP servers.
- **stealthlab**: Claude Code with only the StealthLab MCP server (local server for run 1; local server pointed
  at the branch for runs 2 and 3), installed the way users install it (`packaging/npm`, `stealthlab-mcp install`)
  so the install path is tested too.

Run each task headless and record the JSON result, for example:

```bash
claude -p "<task prompt>" --output-format json --model <model> \
  --mcp-config <arm config> --strict-mcp-config --max-turns 40
```

The JSON gives cost (`total_cost_usd`), tokens, turns and duration. Run each task in a fresh copy of its
starting repo state. Note the exact Claude Code version and model IDs.

**Spend cap: $25 total across all runs.** Estimate the cost from a 2-task dry run first. If the estimate is
over the cap, cut tasks, not repetitions, and ask before going over.

## The task set (build it first, freeze it, then run)

12 to 20 tasks shaped like the client's work, **each with an automatic check**: no human or model judges a
pass. Use public material only. Suggested mix:
- SQL: write or fix a query against a small SQLite database; check = the result set equals the expected one.
- Debugging: a failing pytest in a small Python repo; check = the tests pass.
- PySpark/pandas refactor: rewrite a transformation; check = the same output on a fixture.
- BI metric logic: implement a metric definition (for example cohort retention); check = the values match.
- Git: resolve a merge conflict; check = the repo builds and its tests pass.

Freeze the task list, checks and analysis plan in `experiments/prepilot/PLAN.md` (with its hash in the report)
**before** the first real run. Run each (task, arm) **3 times**. Too few tasks gives too little power: say so
in the report rather than overstating.

## What to measure (per task, arm and run)

1. **Pass rate** (check passed), with a 95% CI. For arm differences use a paired test (McNemar on per-task
   outcomes; reuse `experiments/local_eval/stats.py`).
2. **Cost per task** (USD from the JSON), and the paired difference stealthlab minus baseline, with a
   bootstrap CI. StealthLab's own costs are included: its model calls (JEV triage, embeddings, judge) are logged
   by the server.
3. **Time to a passing result** (wall seconds) and turns.
4. **StealthLab latency**: `python scripts/find_ways_latency_report.py --json` over the run's calls (each
   stage's p50/p90). Run 1 shows the cost of a local database; runs 2/3 that of Neon in us-east-2 from here.
5. **Audit-log completeness**: for every `find_ways` / `call_model` / `report_result` the agent made (from the
   Claude Code transcript), check that a matching log row exists (`retrieval_decisions`, provider-call rows).
   Report the share found and what a row contains.
6. **Did the agent use it?** How often `find_ways` was called, and whether its answer was used (the agent read
   a returned way, or ran `call_model` on the plan's model).
7. **Failover (run 1 only, offline)**: configure two endpoints for one model in a test
   `STEALTH_PROVIDER_CONNECTIONS_FILE`, one of them dead (a closed port) and one a local stub; show that
   `call_model` answers from the live one and reports `endpoints_failed_first`. Then add a 2-second delay to the
   stub and show that `max_latency_ms` moves the call on. No real provider is needed.

## Check every pitch claim against the code and the numbers

The pitch draft makes claims below. For each, the report states one of: **measured** (with the number),
**testable in the pilot**, **not built**, or **false today**, with evidence (a file, a test or a number).
Known status as of 2026-10-08. Re-check each against the code; do not just copy this list:

| Pitch claim | Status to verify |
|---|---|
| "50% cost reduction at the same accuracy" | Unmeasured. `docs/routing_priors_eval.md`: on public data **no router, ours included, beats random routing** (APGR 0.463 [0.308, 0.604]). Production has 0 routing observations, so model plans say `not_ready`. Runs 2/3 give the first real cost numbers for *agent + StealthLab*, which is a different claim from "the router saves 50%" |
| "Tested on 310 tasks (SQL, PySpark, BI dashboards...)" | No such task matrix exists in the repo. Do not claim it. This run's task set is what we can cite, with its real size |
| "Hosted in AWS Mumbai, no data leaves India" | **False today.** The databases are Neon **us-east-2**, and providers are US or global. `docs/india_region_plan.md` is a plan; `legal/README.md` flags this exact claim |
| "All AI traffic goes through our router; we block shadow AI / unapproved models" | **Not built.** The MCP server advises; the agent's own model calls (Claude Code, Cursor) do not pass through us. `call_model` enforces policy only for calls made through it |
| "Every routing decision logged; exportable for EU AI Act / DPDP audits" | Partly: measure item 5. Check whether an export exists; if not, say not built |
| "Logs encrypted (AES-256), kept 1 year, in S3" | Verify in code and docs (`docs/security_runbook.md`, `final_prod_docs/`). Neon encrypts at rest; check retention and storage location before stating anything |
| "Bias testing across gender/caste/region" | Not built and not applicable to coding tasks as described. Say so |
| "Read-only access to your repos, no production data" | The server does not read repos; the local hooks and survey read the working copy on the user's machine and send query text. Describe exactly what leaves the machine (`packaging/npm/README.md`) |
| "Plugs into Cursor and Claude Code, no workflow change" | Testable now: the stealthlab arm installs it the user's way. Report any friction |
| "20-30% faster, >85% acceptance, 9x ROI" | Hypotheses for the pilot only. Not measurable here except time-to-pass (item 3) |

## Report (`experiments/prepilot/REPORT.md`)

1. One table per run: pass rate, cost, time and turns for baseline vs stealthlab, with CIs and the paired test.
2. Run 3 vs run 2: Sonnet+StealthLab vs default-model baseline (the "cheaper model, same accuracy" question).
3. Latency per stage, audit completeness, failover result.
4. The claims table filled in, with evidence.
5. "What we can say in the meeting": sentences that are true as measured, each with its number and CI.
   "What we must not say": the claims marked not built or false.
6. Deviations from `PLAN.md`, costs spent, and anything that broke.

## Ground rules

- Stage files by name; this checkout is shared with other sessions. Commit only `experiments/prepilot/` and
  only when asked. Prefix `measure:` and paste the run counts into the message.
- This machine has about 2 GB of free RAM: one heavy process at a time.
- Do not change product code to make a number look better. If you find a bug, write it down and test the
  product as it is; fix it only in a separate change, and re-run the affected tasks.
