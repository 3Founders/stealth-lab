# Handoff: StealthLab A-vs-C product test and product state (2026-10-11, 15:30 UTC)

For Chaitanya (or anyone resuming). Everything needed to continue from this exact state: what was measured,
what the product does now, what is queued, and verbatim steps to resume. Older context:
`docs/abtest_followups_2026-10.md` (running notes, same facts in more detail).

---

## 1. TL;DR

- **The test:** arm A is plain Claude Code on Opus 5.5. Arm C is the same Claude Code with StealthLab installed
  the product way (`stealthlab-mcp install --with-exec`, hooks only). Correctness is graded on each task's full
  hidden tests; the agent and dispatch only ever see the visible ones.
- **Phase 1** (130 tasks, 5 suites; this is the main result): C costs **38% less** than A and solved **114/128
  vs A's 120/128**. Latency, p50/p95: A 33 s / 92 s, C 56 s / 286 s.
- **Phase 2** (75 tasks, JS and HTML pages):
  - arm A is done;
  - arm C is **paused after 7 tasks** because both General Compute keys ran out of daily tokens;
  - resume after General Compute's daily reset (apparently 00:00 UTC).
- **Claude weekly usage** on the Pro plan was 76% at handoff. It resets 2026-10-13 20:00 UTC.
- **Biggest open product gaps:**
  - the check is not detected automatically (real users get no dispatch without it);
  - wrong cheap answers still get through sometimes;
  - latency is about 1.7× A at p50;
  - no dispatch for Cursor;
  - messy and multi-turn prompts are not handled;
  - `.stealth` knowledge is not exercised on long series of tasks in one repo.

  See section 6.

---

## 2. Every measurement

All numbers come from `experiments/abtest/results_2026-10-11/*.jsonl`, the committed snapshot. Reproduce any
table with:
`ABTEST_RESULTS_DIR=experiments/abtest/results_2026-10-11 python experiments/abtest/report.py <run>`

"Saving" is C's cost relative to A's on the same tasks. "Cheap delivered" counts tasks where a cheap model's
answer was applied and Opus never ran. "Not dispatched" counts tasks where routing sent the task straight to Opus.

### 2.1 History of runs: the system changed between runs

| Run | Tasks | A solved / cost | C solved / cost | Saving | C p50 / p95 | What C was |
|---|---|---|---|---|---|---|
| batch1 | 30 (DS-1000 + BigCodeBench) | 30 / $2.87 | 26 / $1.59 | -44% | 44 / 217 s | first dispatch; weak checking |
| batch2 | same 30 | 29 / $2.65 | 24 / $1.48 | -44% | 82 / 319 s | + cross-check, learned costs |
| batch3 | 45 (+ MBPP+) | 44 / $3.80 | 41 / $2.69 | -29% | 55 / 466 s | + Opus writes tests first (test-first) |
| batch4 | 45 new tasks | 42 / $4.01 | 40 / $2.31 | -43% | 72 / 348 s | Sonnet writes the tests; Sonnet no longer a candidate |
| tf-sonnet | 10 (C only) | -- | 8/10 | -- | -- | trial: Sonnet vs Opus as test writer (Sonnet's tests ~69% cheaper, same verdicts) |
| phase1, sequential C (first 16) | 16 | | | | | backup: `phase1.sequential-C.bak.jsonl` |
| phase1, parallel C (first 19) | 19 | | | | | backup: `phase1.parallel-C.bak.jsonl` |
| phase1, "final" C (spoiled) | 128 | 120 / $15.38 | 111 / $10.08 | -34% | | General Compute's daily limit ran out at the end |
| **phase1, current C** | **128** | **120 / $15.38** | **114 / $9.56** | **-38%** | **56 / 286 s** | **current main** |
| phase2 A | 75 | 39 / $16.87 | -- | -- | -- | arm C paused after 7 tasks |

### 2.2 Phase 1, current system (run `phase1`)

| Suite | Tasks | A solved | A cost | C solved | C cost | Saving | A p50 / p95 / p99 | C p50 / p95 / p99 | Cheap delivered | Not dispatched |
|---|---|---|---|---|---|---|---|---|---|---|
| BIRD SQL | 48 | 45 | $5.37 | 44 | $2.66 | -50% | 32 / 78 / 462 | 56 / 197 / 299 | 32 | 8 |
| BoolQ | 10 | 10 | $0.77 | 10 | $0.09 | -89% | 21 / 35 / 35 | 39 / 45 / 45 | 10 | 0 |
| DS-1000 | 30 | 29 | $2.68 | 27 | $1.53 | -43% | 23 / 89 / 126 | 43 / 191 / 247 | 19 | 6 |
| IFEval | 20 | 20 | $2.01 | 20 | $0.77 | -62% | 37 / 68 / 92 | 50 / 222 / 664 | 15 | 2 |
| OpenAPI | 20 | 16 | $4.56 | 13 | $4.52 | -1% | 64 / 118 / 170 | 96 / 325 / 329 | 3 | 11 |
| **All** | **128** | **120** | **$15.38** | **114** | **$9.56** | **-38%** | **33 / 92 / 443** | **56 / 286 / 329** | **79** | **27** |

- Of C's 14 unsolved tasks, A also failed 7.
- 4 C-only misses were wrong cheap answers that passed the visible check and the test-first tests:
  bird-1028, bird-1166, ds1000-50, ds1000-831.
- The other C-only misses are OpenAPI tasks done by Opus in both arms (run-to-run variation).
- 2 tasks are not paired:
  - **bird-1505:** its arm C row was removed because two runners overlapped for about a minute. Re-run it (section 7).
  - one other task, which `report.py` shows.

### 2.3 Phase 2 (run `phase2`)

| Suite | Arm | Tasks | Solved | Cost | p50 / p95 |
|---|---|---|---|---|---|
| Design2Code HARD | A | 30 | 5 | $10.73 | 54 / 244 s |
| WebSight | A | 25 | 15 | $4.44 | 41 / 57 s |
| MultiPL-E JS | A | 20 | 19 | $1.70 | 15 / 24 s |
| Design2Code HARD | C | 3 | 0 | $0.66 | -- |
| WebSight | C | 2 | 1 | $0.35 | -- |
| MultiPL-E JS | C | 2 | 2 | $0.03 | -- |

Design2Code's bar is strict (text recall >= 0.8 and visual closeness >= 0.5). It is the same bar for both arms.
Expect page tasks to cost C about what they cost A: the cheap models are text-only and cannot see the
screenshot. MultiPL-E JS is where C should save.

### 2.4 Findings that shaped the product (all fixed unless noted)

- **Test-first works:** a cheaper Claude model writes acceptance tests, and a cheap answer must pass them. It cut
  wrong cheap answers from 4-6 per 30 tasks to about 1-2.
- **Sonnet writes those tests about as well as Opus, at about a third of the price** (tf-sonnet trial).
- **Sonnet as a dispatched model costs about what Opus does** on small tasks (Claude Code's overhead), so it was
  removed from the cheap rungs.
- **Parallel cheap tries** (at most 2 at once now) cut latency and did not hurt the saving.
- **OpenAPI's poor result had stacked causes:**
  - General Compute's per-minute **and** daily token limits;
  - a 150 s time limit too short for big tasks;
  - agents re-reading 20-80 KB contracts every step;
  - gpt-oss provider errors;
  - test-first tests rejecting some correct answers;
  - routing that could not tell OpenAPI from other tasks.

  All fixed, see the commits in section 4. The result: OpenAPI went from +7% to -1%, with 11 of 20 OpenAPI tasks
  skipped by routing.
- **General Compute free tier: 10M tokens/day and 200k tokens/minute per key.** Phase 1 arm C uses about 10M.
  The daily limit spoiled the end of one phase 1 run (cheap success fell from 68% to 44% after 8M tokens).
- **Routing bugs found and fixed:**
  - cancelled runs (parallel losers, time-outs) and provider-refused runs were reported as model failures;
  - every task fell into one "generic" routing key.
- **Remote "cloud" subagents launched from a session ran locally**, on the Pro subscription, not on the cloud
  credits.

---

## 3. The product, end to end, as it is now

Claude Code, `stealthlab-mcp install --with-exec`, without the global library.

### Setup (once)

1. `stealthlab-mcp install --with-exec` does three things:
   - saves the server URL and token, plus `dispatch: true`, in `~/.stealthlab/config.json`;
   - removes the StealthLab MCP registration (hooks-only mode; `--keep-mcp` is queued, not built);
   - writes three hooks into Claude Code's `settings.json`: UserPromptSubmit (`hook-prompt`), PostToolUse on
     Bash|PowerShell (`capture-tool`), and Stop (`capture-stop`).
2. `~/.stealthlab/exec.json` lists the cheap models (gemma-4-31b-it, gpt-oss-120b, minimax-m2-7 on General Compute)
   with their `key_env`. The key itself is never in the file.
3. Per repo, `stealthlab-mcp survey` creates `.stealth/`. This is **manual today**; an automatic survey is queued.

### Every prompt

4. **Prompt hook:** refreshes the token, then runs triage (should this prompt be looked up?) and, if so,
   `find_ways` on the server with:
   - the prompt;
   - `.stealth` facts and the library payload;
   - `my_model` (the session model);
   - the candidates: the open models from exec.json. Other Claude models are dropped when open models exist.
5. **Server:**
   - matches a Goal. With no Goal, it labels the prompt's **kind of task** (codebook group, one embedding call)
     and keys routing by kind, or by repo plus kind;
   - builds the model plan: success estimates from the bundled prior, updated by the caller's outcomes on that
     key across all repos, plus this repo's `routing.md` counts;
   - chooses a ladder. Opus goes first when trying cheap models does not pay for that kind.
6. **Dispatch** (`packaging/npm/lib/dispatch.mjs`), in order:
   - **No check:** none configured and no matching library entry, so no dispatch. **Auto-detecting the check is
     queued**; until then real users rarely get dispatch.
   - **Opus first:** if the plan puts Opus first, no dispatch, except a re-check with probability
     `STEALTHLAB_DISPATCH_EXPLORE` (0.2).
   - **Candidates:** the plan's cheap rungs plus the other configured open models (up to 3). Providers with fewer
     than `STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS` (300k) tokens left today are dropped; this reads the provider's
     rate-limit headers with one tiny request, cached 5 minutes.
   - **Test-first:**
     - Sonnet (`STEALTHLAB_DISPATCH_TESTFIRST_MODEL`, set by the test runner) writes 4-6 pytest tests from the
       prompt and the repo's small files, from a general brief (`SPEC_SYSTEM`);
     - the tests are **written while the cheap models start**;
     - the cheap models' check is `<project check> && python run_spec.py`, which waits for the tests.
   - **Cheap tries:**
     - at most `STEALTHLAB_DISPATCH_MAX_PARALLEL` (2) at once, each in its own git worktree;
     - time limit: 150 s plus 4 s per KB of the task's top-level files (at most +210 s);
     - the first answer that passes is accepted and the others are cancelled.
   - **Pass:** the change is applied to the working copy, a library entry is added, and the prompt is **blocked**
     with "Done by <model> ...". Opus never runs.
   - **No pass:** a one-line note, and Opus does the task like plain Claude Code.
   - **What counts against a model:** only finished passes and failures. Cancelled and provider-limited runs are
     not counted.
7. **After the session:** `capture-tool` records test verdicts; `capture-stop` adds a library entry, reports the
   outcome to the server's outbox, and codes the entry.
8. **Logs:** `~/.stealthlab/hooks.log` (dispatch decisions), `~/.stealthlab/dispatch_costs.jsonl` (test-writing
   cost), and `~/.stealthlab/runs/` (each cheap run's events, patch and stdout).

### Cheap agent (`lib/agent/step_agent.mjs`)

- Tools: read_file, list_files, search, edit_file, write_file, run_shell, finish; 30 steps.
- Context capped at 160k characters; an unchanged repeat read is not resent.
- On 429, waits for the provider's `retry-after` (or 10-40 s), up to 6 attempts.
- gpt-oss tool-call errors are retried with sampling variation.

### Cursor and other clients

- **No dispatch** (queued). Cursor gets knowledge only, after its first tool call.

---

## 4. Code state

- **Branch `main`:** everything above, pushed to origin.
- **Today's key commits:**
  - `b1efa59`: OpenAPI fixes;
  - `e63599f`: honest routing evidence and the quota skip;
  - `fd7d817`: routing per kind of task;
  - `a97d3f0` and `fdcad0b`: runner pause on low General Compute tokens;
  - this handoff.
- **Tests:**
  - `cd packaging/npm && node --test test/*.test.mjs` should pass about 335 tests (Windows). Two timing-sensitive
    tests (cursor_hooks waits, exec_runtime MCP stdio) sometimes fail when the machine is loaded; they pass when
    run alone.
  - Backend routing: `cd backend && python -m pytest -q tests/test_routing_task_kinds_offline.py
    tests/test_routing_*_offline.py`. `tests/test_mcp_minimal_surface_offline.py` fails independently of this
    work (it expects tools the progressive tool list hides).
- **Branches with partial work (not merged):**
  - `feat/phase1b-suites` (pushed): the validation suite is nearly done; reporting data and references are done;
    the Node and Git suites are not started.
  - `feat/longhorizon-dbt` (pushed): Spider 2.0-DBT data notes and a series plan.
  - `feat/product-fixes-1-3` (pushed): design notes only.
  - `feat/cursor-dispatch` (**local only**, on the original machine): worktree
    `.claude/worktrees/agent-a6abcdfc7a4cc9b6b`, WIP commit `7ba8273`, untested. Push it from that machine if
    needed: `git -C .claude/worktrees/agent-a6abcdfc7a4cc9b6b push origin feat/cursor-dispatch`.

---

## 5. Test harness (experiments/abtest)

- **`runner.py`:** runs arms A and C task by task with `claude -p` (Opus 5.5).
  - Arm C's workspaces get the product's hooks-only install (`setup_c.mjs`).
  - Resumable: it skips (task, arm) pairs already in `.local/results/<run>.jsonl`.
  - Exit codes: 3 = Claude usage limit (rerun after the reset); 4 = General Compute key below
    `--gc-min-tokens` (100k).
  - Options: `--only`, `--exclude-runs`, `--parallel`, `--testfirst-model`, `--gc-key-name`.
- **Suites:** `ds1000_task.py`, `bcb_task.py`, `mbpp_task.py`, `bird_task.py`, `ifeval_task.py`, `boolq_task.py`,
  `openapi_task.py` (+ `openapi_select.py`), `multiple_js_task.py`, `page_task.py` + `html_grade.py`
  (`d2c_task.py`, `websight_task.py`).
  - Each docstring states its fixed visible/hidden split.
  - Data lives under `experiments/<suite>/data/` and is git-ignored; download notes are in the docstrings.
- **`report.py`:** solved, cost, saving, latency p50/p95/p99, cheap delivered, and not dispatched, per suite.
- **`run_chain.sh`:** phase 1 C, then phase 2 A, then phase 2 C, with the exit-code handling above.
- **Backend:** `run_backend.py` serves the real server on 127.0.0.1:8765 against a local Postgres database,
  `kel_abtest` (no hosted DB, empty global library).
- **Secrets** come from `backend/.env`: `GENERAL_COMPUTE_API_KEY`, `general_compute_api_key1`, the JEV and
  OpenRouter keys. Never print them.

---

## 6. Queued work: agreed, not built

Each item needs the owner's go-ahead.

### 6.1 Product fixes (from the end-to-end review)

1. **Find the check automatically:** pytest config or test files, `npm test` (not the placeholder), vitest/jest,
   `dbt build`, `go test`, `cargo test`. Without this, real users rarely get dispatch.
2. **Hooks-only gaps:**
   - a CLAUDE.md StealthLab block (record fixes, attach to existing entries, finish the survey worklist, use the
     new `stealthlab-mcp find` command for sub-tasks);
   - an automatic first survey (background, once per repo);
   - an automatic library tidy (exact duplicates);
   - `install --with-exec --keep-mcp`.

   A design is in `feat/product-fixes-1-3`.
3. **Tell the session what a cheap model did:** a dispatched prompt is blocked, so the next prompt should carry
   "Earlier, <model> changed <files> to do <x>".
4. **Messy prompts:** dispatch only when a request is clear enough, and give the test writer the conversation.
   See 6.2.
5. **Cursor dispatch:** either a blocking `beforeSubmitPrompt`, or a `solve_cheaply` MCP tool the Cursor rules
   call first. Prototype in `feat/cursor-dispatch`. Test with cursor-agent.
6. **Long-horizon `.stealth` test:** a series of tasks in one repo, workspace and `.stealth` kept; arms A, C, and
   C0 (`.stealth` wiped each task).
   - Spider 2.0-DBT, locally: groups sharing a database (quickbooks 001-003, xero, f1 001-003, shopify, tickit, ...);
   - SWE-rebench / SWE-EVO / ADE-bench, on Modal with an API key.

### 6.2 Product hardening: messy prompts, multi-turn sessions, real repos, real user flow

**A. Messy and multi-turn prompts**

1. A **task brief:** a cheap call turns the last few turns (`transcript_path` in the hook payload) plus repo
   context into a precise brief. It resolves "that bug", fills in details from earlier turns, and splits
   multi-part requests. The cheap models and the test writer get the brief.
2. **Request type:** code task / question / chat, plus clarity, size and checkability. Only clear, checkable,
   bounded tasks are dispatched.
3. **Unclear request:** no dispatch; Opus may ask the user.
4. **Continuity:** a per-session log of StealthLab's actions, carried into follow-ups (fix 3 is the first step).
5. **Overrides:** `!opus` skips StealthLab; `!cheap` forces a try.

**B. Long, real repositories**

1. Give the cheap models the relevant `.stealth` facts, library matches and file hints (today they get only the
   task text).
2. Worktrees reuse the project's installed dependencies (`node_modules`, the venv), so checks can run.
3. Run the tests related to the changed files first, and the full suite before accepting.
4. Set the time limit from the project's own test duration.
5. Size gating: big multi-file changes go to Opus, or to bounded steps of a plan.

**C. Real user flow**

1. Safe apply: an undo point, a diff summary, `stealthlab-mcp undo`.
2. Do not overwrite files the user changed during the run.
3. A clear message: who did it, what changed, the cost, and how to undo.
4. An optional "ask before applying" mode.

**How to test A** (build first, then test):
- messy rewrites of the same tasks, with a fixed rubric;
- a simulated user who holds the hidden spec and answers questions (τ-bench style);
- non-task prompts mixed in;
- real prompts from a pilot.

### 6.3 Accuracy, latency and routing follow-ups

- **Agreement check:** when two cheap answers pass, accept only if they agree on the tests plus extra inputs;
  otherwise hand to Opus. This targets the 4 wrong cheap answers in phase 1.
- **Latency:** C is about 1.7× A at p50 and about 3× at p95. Most of it is fallbacks after failed cheap tries.
  Better per-kind skipping, and running the test writer earlier, are the levers.
- **Routing per kind:** watch the "not dispatched" column. A kind should be skipped only with enough genuine
  evidence. The tests in `backend/tests/test_routing_task_kinds_offline.py` encode the expected balance.

### 6.4 Remaining benchmark work

- **Phase 2 arm C:** 68 of 75 tasks left (section 7).
- **bird-1505 arm C:** one task (section 7).
- **Phase 1b suites:** finish the `feat/phase1b-suites` branch (validation, reporting, Node, Git), then run A and C.
- **Phase 3, Modal:** SWE-bench Verified Mini; needs a Modal account (`modal token new`).
- The test-runner side of the 285-task suite list: see `docs/abtest_followups_2026-10.md`.

### 6.5 Known issues

- On a fresh database, `backend/scripts/migrate.py` sometimes fails once with "constraint edges_source_table_check
  already exists". Drop, recreate and rerun.
- **Never start the backend while migrating.** It also creates schema.
- When you stop a run, make sure its `python runner.py` process is gone too. Stopping the parent shell left one
  alive once, and two runners overlapped.
- General Compute's free keys have 10M tokens/day each. A full phase 1 arm C uses about 10M.

---

## 7. Resume from this exact state: verbatim (Windows, Git Bash)

```bash
# 0. repo
cd /c/Users/user/stealth-lab && git pull origin main

# 1. scratch Postgres (127.0.0.1:55432, user kel_owner, trust auth)
PATH="/c/Program Files/PostgreSQL/18/bin:$PATH" pg_ctl -D "C:/Users/user/pg-scratch/data" -o "-p 55432 -c listen_addresses=127.0.0.1" -l "C:/Users/user/pg-scratch/pg.log" start

# 2. check General Compute's remaining tokens before any arm C run (prints the numbers, not the key)
python - <<'EOF'
import json, re, urllib.request
env = dict(re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*)", l).groups() for l in open("backend/.env", encoding="utf-8") if re.match(r"\s*[A-Za-z0-9_]+\s*=", l))
for name in ("GENERAL_COMPUTE_API_KEY", "general_compute_api_key1"):
    key = env.get(name, "").strip().strip('"').strip("'")
    req = urllib.request.Request("https://api.generalcompute.com/v1/chat/completions", method="POST",
        data=json.dumps({"model": "gpt-oss-120b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: h = r.headers
    except urllib.error.HTTPError as e: h = e.headers
    print(name, "tokens left today:", h.get("x-ratelimit-remaining-tokens-day"))
EOF
```

**To CONTINUE phase 2 arm C** on the same database: arm C's routing has already learned from phase 1 and the
first 7 phase 2 tasks, so do NOT reset the database.

```bash
# 3. backend (leave running; separate terminal)
python experiments/abtest/run_backend.py

# 4. phase 2 arm C (resumes after the 7 done tasks; pauses itself below 100k General Compute tokens)
cd experiments/abtest
python -u runner.py --run phase2 --seed 20261010 --tasks multiple_js:20,d2c:30,websight:25 --arms C --parallel --testfirst-model claude-sonnet-5-5 --gc-key-name general_compute_api_key1 >> .local/phase2.out 2>&1
#    exit 3 = Claude usage limit: rerun the same command after the reset
#    exit 4 = General Compute low: rerun after 00:00 UTC, or use --gc-key-name GENERAL_COMPUTE_API_KEY if that key has tokens

# 5. the one phase 1 task to redo
python -u runner.py --run phase1 --seed 20261010 --tasks ds1000:30,bird:50,ifeval:20,boolq:10,openapi:20 --arms C --parallel --testfirst-model claude-sonnet-5-5 --gc-key-name general_compute_api_key1 --exclude-runs batch1,batch2,batch3,batch4,tf-sonnet,smoke-hooksonly,smoke-phase1 --only bird-1505

# 6. results
python report.py phase1
python report.py phase2
```

**On a different machine:**
- `.local/` (results, workspaces, run logs) is not in git. Copy the snapshot first:
  `mkdir -p experiments/abtest/.local/results && cp experiments/abtest/results_2026-10-11/*.jsonl experiments/abtest/.local/results/`
- Arm C's routing memory lives in the `kel_abtest` database. A fresh database means C starts cold (still valid,
  but say so in the results).
- The setup also needs:
  - backend/.env with the keys;
  - Claude Code logged in (Opus 5.5, Sonnet 5.5);
  - Node 24;
  - Python 3.14 with pytest, fastapi, uvicorn, schemathesis, hypothesis-jsonschema, playwright (+ `python -m
    playwright install chromium`), pyarrow, langdetect, nltk (+ punkt_tab), immutabledict, absl-py;
  - DS-1000's eval env at `experiments/ds1000/.evalenv` (with pytest copied in);
  - the datasets under `experiments/<suite>/data/` (download sources are in each `*_task.py` docstring).

**To reset a fresh test database** (only when starting a new comparable run):

```bash
PATH="/c/Program Files/PostgreSQL/18/bin:$PATH"
psql -h 127.0.0.1 -p 55432 -U kel_owner -d postgres -c "DROP DATABASE IF EXISTS kel_abtest WITH (FORCE)"
psql -h 127.0.0.1 -p 55432 -U kel_owner -d postgres -c "CREATE DATABASE kel_abtest"
(cd backend && DATABASE_URL= python scripts/migrate.py --dsn postgresql://kel_owner@127.0.0.1:55432/kel_abtest)   # rerun once if it fails on edges_source_table_check
rm -rf experiments/abtest/.local/ws/C experiments/abtest/.local/C_home experiments/abtest/.local/worktrees
```

**Budget rules the owner set:**
- stop at 80% of the Claude weekly limit (Pro);
- check usage before big runs;
- spawned cloud/remote agents run on the subscription, not on credits.
