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

  See section 6; **the build queue for Chaitanya's Claude is section 6.6.**

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

### 3b. Every mechanism and service, in short

**External services and models**

| Service | What it is used for |
|---|---|
| **Claude Opus 5.5** (subscription, via Claude Code) | The session model; it does the task when nothing cheaper passes. Arm A uses only this. |
| **Claude Sonnet 5.5** (`claude -p`, tool-less) | Writes the test-first acceptance tests (about $0.01-0.02 a task). No longer used as a dispatched coding model. |
| **General Compute** (OpenAI-compatible API) | Hosts the 3 cheap coding models (gemma-4-31b-it, gpt-oss-120b, minimax-m2-7) and backs the server's find_ways LLM client (`general_compute_api_key`). Free tier: 10M tokens/day, 200k tokens/min per key. Keys: `GENERAL_COMPUTE_API_KEY`, `general_compute_api_key1`. |
| **JEV** (`JEV_BASE_URL`, remote semantic judge) | The first judge in the semantic chain: find_ways triage (is this prompt a reusable task worth a lookup?), judging Goal candidates and library matches against the request and repo facts, and goal resolution. Falls back to OpenAI-compatible providers. |
| **OpenRouter embeddings** (`EMBEDDING_PROVIDER_CHAIN=openrouter`, gemini-embedding-2) | Embeds queries and Ways for hybrid search, for semantic codes of Ways (`/routing/codes`), and now for each request's **kind of task** (routing key). Same vector space as the Vertex codebook. |
| **Postgres** (local scratch DB `kel_abtest` for the test; Neon in production) | Goals, procedures, routing observations and decisions, cost and token stats, library ingestion. Row-level security scopes every read to the caller. |
| **Bundled routing data** (`backend/app/routing/data/`) | `prior_bundle.*` is the fitted prior over models x tasks; `prior_cards.json` holds model prices; `codebook.npz` holds 24 x 8 task groups built from 20,731 Ways. |

**Mechanisms, in the order they act**

1. **Hooks-only install.** Three Claude Code hooks; no MCP server in the session (`installClaudeDispatch`).
2. **Token refresh.** The hook renews a signed-in token shortly before it expires.
3. **Triage** (JEV). One cheap classification decides whether to look the prompt up at all. It fails toward the
   lookup.
4. **find_ways.**
   - Hybrid lexical + vector search for Goals, each candidate judged against the request and repo facts (JEV);
     bounded hierarchy expansion; related examples; matches against the repo's own `library.md`.
   - Knowledge is returned only when the request resolves; nothing is stored except the request's kind.
5. **Repo facts.** `.stealth/claims.md` and the units' claim pages break ties between Goals and procedures.
6. **Routing (model plan).** Per Goal, or per virtual key (library entry / repo+kind / kind / generic):
   - the success model is a Bayesian prior: Thompson draws per request;
   - the target is **matched to the session model's own reliability**, with a tolerance;
   - the plan picks the cheapest feasible ladder, ending with the session model;
   - local learning: this repo's `routing.md` counts plus the caller's server-side outcomes on the key, combined
     Beta-binomial (prior strength 4);
   - **costs learned** from reported dollars; untried units are sized optimistically;
   - **exploration:** each cheap unit gets 3 tries per key;
   - a **20% re-check** of kinds where Opus goes first.
7. **Kind of task.** The request's codebook group keys routing when no Goal resolves. The full code sets the prior.
8. **Dispatch.**
   - **Check:** configured, or from a matched library entry.
   - **Quota probe:** provider rate-limit headers; skip below 300k tokens.
   - **Candidate selection:** plan rungs plus configured open models; Sonnet off by default.
9. **Test-first.** Sonnet writes tests from the prompt and small project files under a general brief; they are
   written at the same time as the cheap runs. `run_spec.py` waits for them. Code fences are cleaned; tests that
   do not parse are dropped (SKIP).
10. **Cross-check** (only when no test-first tests exist). A second cheap model writes extra tests; strict mode
    rejects answers nothing could confirm.
11. **Executor runtime** (`lib/exec/runtime.mjs`).
    - Each run happens in an isolated git worktree based on the working tree.
    - The check runs inside the worktree; hang and hard timeouts apply.
    - Only verified work is applied.
    - Every finished attempt is reported to the server (`report_result`, with tokens and cost). Cancelled and
      provider-limited attempts are not reported.
12. **Cheap agent** (`step_agent.mjs`). A tool loop over the provider's chat API:
    - context compaction and no resent repeat reads;
    - 429 back-off;
    - tool-call error retries;
    - a step limit.
13. **Parallel tries and time limit.** Up to 2 runs at once, the first pass wins and the rest are cancelled. The
    limit is 150 s plus a task-size allowance.
14. **Apply and block.** The winning diff goes into the working copy; the prompt is blocked with a report.
15. **Fallback note.** If nothing passed: one line, then Opus works as plain Claude Code.
16. **Capture hooks.** Test verdicts from Bash/PowerShell; at Stop, a library entry and an outcome report through
    the **outbox** (retries offline).
17. **Library** (`.stealth/library.md`). Solved problems with their check and diff, grouped into reusable
    Goals/Ways, plus semantic codes for Ways. Grows automatically from dispatch and capture.
18. **Survey** (`stealthlab-mcp survey`). Scanner facts and a worklist for `.stealth`. **Manual today**; an
    automatic survey is queued.
19. **Logs and ledgers:** `hooks.log`, `dispatch_costs.jsonl`, per-run `runs/<id>/` (events, patch, stdout),
    `provider_quota.json`.
20. **Not active in hooks-only mode:** the model guard (blocked in-session edits for delegation), the delegator
    agents, in-session hand-overs, plan-and-run, and the MCP tools (find_ways, report_result, discover_tools,
    use_tool, call_model) as session tools. They remain for other clients and `--with-exec --no-hooks`.

**Benchmark-only tools** (not product): Playwright + Chromium (page grading), Schemathesis + Hypothesis (OpenAPI
grading), DS-1000's eval env, dbt-duckdb (planned), Node `--test` (MultiPL-E).

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

### 6.6 BUILD QUEUE for Chaitanya's Claude (agreed 2026-10-12): build, push branches, the owner reviews

The owner wants these BUILT now, by Chaitanya's Claude, and pushed for review. Work through them in the order
below. Each item has: where, exactly what, what NOT to do, and how to prove it.

**How the owner wants things built (read before designing anything):**
- The product's rule: **cheap models do the task**; Opus is the fallback, never a parallel backup.
- **Simple over clever.** The owner pushes back on over-engineering: prefer a deterministic rule to a trained
  model, a small change to a new subsystem. If a spec here seems to need something big, stop and ask.
- **Real product path only:** everything runs through the hooks-only Claude Code install (`install --with-exec`),
  the same way a user gets it -- no benchmark-only shortcuts in product code.
- Explanations to the owner in plain, short language.
- `feat/cursor-dispatch` is WIP and untested (7ba8273); `feat/product-fixes-1-3` is a design start, not finished.
- L1's offline proof needs the arm C run logs, which exist only in the resume bundle on the owner's PC
  (`C:\Users\user\stealth-lab-resume-bundle-2026-10-11\abtest_local_state.tar`, `C_home/runs`; it also holds a
  private key -- ask the owner to send only `C_home/runs`). Without them, build L1 with unit tests and write
  "proof pending: run logs" in the commit.

**Rules for every item (non-negotiable):**
- One branch per item, from `origin/main`: `feat/q-<id>-<short-name>` (e.g. `feat/q-L1-early-stop`). Push the
  branch; never push to `main`, never force-push, never merge. The owner pulls and reviews.
- **Nothing may make earlier results worse.** Every item must show, offline where possible, that solved count and
  cost on the existing results do not get worse (section 2 numbers; results in
  `experiments/abtest/results_2026-10-11/`). If an item can only be judged live, say so in the commit message and
  stop; do not run live benchmarks (they need the owner's Claude usage and General Compute quota).
- Existing tests stay green and are never weakened: `cd packaging/npm && npm test`; in `backend`:
  `python -m pytest tests/test_routing*.py tests/test_*plan*.py tests/test_find_ways*.py -q` (slow, ~18 min).
  Add tests for every behaviour you add, in the style of the neighbouring tests.
- Every new behaviour gets an env switch to turn it off, named `STEALTHLAB_*` (client) or `STEALTH_*` (server),
  documented in the code where it is read.
- Write code like the surrounding code (comment density, naming). No secrets in code, logs or commits; never read
  or print `backend/.env` values; stage files by name (never `git add -A`).
- Commit message: what changed, why (with the measured numbers that motivated it), how it was checked.

**Q0. Router check calibration -- ALREADY BUILT, review only.** Branch `feat/routing-check-calibration` (6bdd530).
- Why: the router assumed a 'tests' check lets ~19% of wrong answers through, so it expected 13-30% of accepted
  cheap answers to be wrong and sent 9 of 20 MultiPL-E JS tasks straight to Opus. Measured: 6 wrong accepted of
  ~79 wrong cheap attempts (~0.08). Giving JS its own routing group changed nothing -- the coarse grouping was NOT
  the cause.
- What it does: `tests_false_accept` = 0.08 on the bundled prior (`STEALTH_ROUTING_TESTS_FALSE_ACCEPT`, 0 = old
  behaviour); per-kind learning from audits (`report_audit` MCP tool; dispatch reviews 10% of delivered cheap
  answers in a detached process, `lib/audit.mjs`, `STEALTHLAB_DISPATCH_AUDIT`).
- Offline evidence: replay of all 228 saved routing decisions, cheap-first 179 -> 213; projected phase 1 + JS
  C cost $11.87 -> ~$10.50 (saving -34% -> ~-42%), ~0.7 fewer solved; balance tests unchanged.
- Your job: review only. The live check (arm C on the 20 JS + 20 OpenAPI tasks after the weekly reset) is the
  owner's.

**6.1 fixes 1-6 and 6.2 hardening A/B/C (above): build them.** Order: 6.1 #1, #2, #3, then 6.2 A (A1-A5), C
(C1-C4), B (B1-B5), then 6.1 #4 (= 6.2 A), #5 Cursor, #6 long-horizon test harness (build the harness only; do
not run it). What "done" means for each:
- 6.1 #1 check detection: a pure function `detectCheck(root) -> command | null` in `packaging/npm/lib/`, used by
  `dispatchCheck` when no `STEALTHLAB_DISPATCH_CHECK` is set. Fixture repos in tests for: pytest (pyproject/ini/
  tests dir), `npm test` with a real script (NOT npm's default "no test specified" placeholder), vitest, jest,
  `dbt build`, `go test ./...`, `cargo test`, and a repo with none (-> null = no dispatch, as today).
- 6.1 #2 hooks-only gaps: start from `feat/product-fixes-1-3` (rebase it). CLAUDE.md block is idempotent (re-install
  does not duplicate it; uninstall removes it). First survey runs once per repo in the background and never blocks
  a prompt. Tidy removes exact duplicates only. `--keep-mcp` keeps the MCP servers alongside the hooks.
- 6.1 #3 continuity: after a dispatched (blocked) prompt, the NEXT prompt's hook context carries one line per
  earlier dispatch in this session: "Earlier in this session, <model> changed <files> to <one-line task>
  (StealthLab, check passed)". Kept in a per-session file under the StealthLab home; at most the last 5 lines.
- 6.2 A1 task brief: a cheap model (gpt-oss-120b via the existing stealth executor's provider, NOT a Claude model)
  turns the last <=6 turns of `transcript_path` plus the prompt into a brief: {task, files_hinted, acceptance,
  parts[], clarity: clear|unclear, kind: code|question|chat, size: small|medium|large}. Budget: <=3 s typical,
  hard timeout 8 s; on timeout or error, fall back to today's behaviour (raw prompt). The brief, not the raw
  prompt, goes to the cheap models and to the test writer.
- 6.2 A2/A3: dispatch only if kind=code AND clarity=clear AND size!=large AND a check exists; otherwise no
  dispatch (the session model handles it, as in arm A). Never ask the user anything from the hook.
- 6.2 A5 overrides: a prompt starting with `!opus` skips StealthLab entirely (the prefix is stripped from what the
  session sees); `!cheap` forces a dispatch attempt even if routing would skip.
- 6.2 C1-C4: before applying a cheap answer, record an undo point (a git stash-like ref, NOT a commit on the user's
  branch); `stealthlab-mcp undo` restores it. If any file the answer touches changed in the user's tree during the
  run, do not apply: report "not applied, you edited <file>". The block message states model, files, cost, and
  the undo command. `STEALTHLAB_DISPATCH_ASK=on` stops before applying and shows the diff summary instead.
- 6.2 B1: the cheap models' task text gets the relevant `.stealth` facts and the judged library matches (the same
  ones find_ways returned), capped at 4 KB. B2: worktrees symlink/junction `node_modules` and reuse the project's
  venv instead of reinstalling. B3: run tests related to changed files first (fail fast), full check before
  accepting. B4 = L2 below. B5: size gating comes from A1's `size`.
- How to test A without live runs: a fixed set of >=30 prompts (messy rewrites of existing benchmark tasks, plus
  questions and chit-chat), committed as a fixture with the expected kind/clarity; the brief classifier must get
  >=90% kind right and must never mark a question or chat as a code task.

**Latency: only these three are worth building.** (The research reports also suggested starting Opus in parallel
as a backup, a trained failure-prediction model, a separate upfront classifier, out-of-order speculation and
neural answer scorers. Do NOT build those: a parallel Opus pays for Opus on tasks the cheap models win, against
the product's rule that cheap models do the task; we already have an upfront router; the rest need training data
we do not have or bring little for this design.) The data that decides it: of the 28 tasks where the cheap tries
failed and Opus took over, **20 waited out the full time limit (median +164 s vs arm A)**; the 8 that failed
earlier still added a median 99 s. Cheap wins add a median 22 s.

- **L1. Stop stuck cheap runs early** (biggest win). Where: `packaging/npm/lib/agent/step_agent.mjs` (the cheap
  agent loop) and `lib/exec/runtime.mjs`. Stop a run and report it as `failed` (state `stalled`, counted against
  the model -- it did fail) when ANY of: (a) the check failed twice with the same failing test names/first error
  line and the edits in between changed nothing those tests touch; (b) the same file content hash recurs (an edit
  undone and redone); (c) 3 consecutive tool calls errored; (d) 6 steps with no change to the worktree diff.
  Deterministic rules only -- no model. Switch: `STEALTHLAB_EARLY_STOP` (default on). Proof, offline: replay the
  run logs of the phase 1/2 arm C runs (in the resume bundle, `C_home/runs`; ask the owner for the bundle if you
  do not have it): count (i) failing runs the rules would have stopped and how many seconds earlier, and (ii)
  runs that eventually PASSED that the rules would have stopped. (ii) must be 0 or the rule is too eager. Report
  both numbers in the commit message.
- **L2. A time limit per kind of task**, learned from this repo's own cheap wins. Record each verified cheap
  run's duration in `.stealth/routing.md` OBS lines (add `p90s=<seconds>`, the 90th percentile of that route's
  winning runs, last 20). Limit = clamp(2 x p90s, 60 s, today's limit (150 s + allowance)); with fewer than 5 wins
  on the route, keep today's limit. Proof, offline: from results, how many cheap wins would have been cut (must
  be 0 by construction on the data used) and the seconds saved on failures.
- **L3. Agreement check** (accuracy, small latency cost; = 6.3 "Agreement check"). When a second cheap answer
  passes while the first is still being judged, or within 10 s of the first pass, run both against the
  test-first file plus `hypothesis`-generated inputs for the functions the tests call (pytest projects only),
  and accept only if they agree; on disagreement, go up the ladder. Do not wait for a second model if none is
  running. Switch: `STEALTHLAB_DISPATCH_AGREE` (default on). Proof: the 6 cheap answers that passed their checks
  but failed the hidden tests (4 BIRD, 2 DS-1000: arm C rows with a verified cheap attempt, `turns` 0 and
  `resolved` false) -- report how many it would have caught, where a second passing answer existed.
- Later, only if the owner asks: a one-shot fast lane (one plain model call writes the change, check it, then
  the agent loop if it fails). Do not build it in this round.

**GLM.** Not on General Compute. When the owner gives a provider and key name (z.ai or OpenRouter), add a profile
(see `packaging/npm/lib/executors/profile.mjs`; `key_env` is the NAME of the variable, never the key) for glm-5
(code) and glm-4.6v (pages), and add them to the stealth executor's models. The router already has GLM priors.
No code change otherwise; do not run it.

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

---

## 8. Resuming phase 2 arm C IDENTICALLY (no drift from how it was run)

Phase 2 arm A (all 75 tasks) and arm C's first 7 tasks ran on the original machine under exactly the conditions
below. To keep the remaining 68 arm C tasks comparable, every item here must match. If any cannot, write down
which in the results.

### 8.1 What must NOT change

- **Code:** run from commit `700cdb4` (or any later commit that only touches `docs/`). Check with:
  `git diff --stat 700cdb4 -- packaging backend/app experiments/abtest/*.py experiments/abtest/*.mjs experiments/abtest/exec.json`.
  It must print nothing. No product or harness edits before the run ends.
- **The exact runner command** (same flags, same seed, same task spec):
  ```
  python -u experiments/abtest/runner.py --run phase2 --seed 20261010 --tasks multiple_js:20,d2c:30,websight:25 --arms C --parallel --testfirst-model claude-sonnet-5-5 --gc-key-name general_compute_api_key1
  ```
  - `--gc-min-tokens` stays at its default, 100000. The runner then also sets dispatch's provider floor to 100000.
  - Do **not** add `--exclude-runs`, `--only`, or `--arms A`.
- **State** (where arm C left off):
  - **the `kel_abtest` database:** arm C's routing memory from phase 1 and the first 7 phase 2 tasks;
  - **the `.local/ws/C/{multiple-js,d2c,websight}` workspaces:** their `.stealth` libraries and routing.md;
  - **`.local/C_home`:** exec.json copy, config `dispatch: true`, quota cache, specs, run logs;
  - **`.local/results/phase2.jsonl`:** A's 75 rows and C's first 7. The runner skips them.

  **Never reset the database or delete these for this resume.**
- **Models and settings** (all from committed code and config):
  - session model `claude-opus-5-5` (runner `MODEL`);
  - test writer `claude-sonnet-5-5`;
  - cheap models gemma-4-31b-it, gpt-oss-120b, minimax-m2-7 on General Compute (`experiments/abtest/exec.json`);
  - dispatch defaults: parallel 2; time limit 150 s plus 4 s/KB up to +210 s; re-check 0.2; test-first on;
    strict cross-check;
  - backend: `experiments/abtest/run_backend.py` with backend/.env (JEV judge,
    `EMBEDDING_PROVIDER_CHAIN=openrouter` with gemini-embedding-2, General Compute for the find_ways LLM client).
- **Tool versions used for everything so far:**

  | Tool | Version |
  |---|---|
  | Claude Code | 2.1.292 |
  | Node | v24.18.1 |
  | Python | 3.14.6 (DS-1000 eval env 3.11.9) |
  | PostgreSQL | 18.4 |
  | pytest | 9.1.1 |
  | fastapi | 0.141.1 |
  | uvicorn | 0.52.1 |
  | schemathesis | 4.30.1 |
  | hypothesis | 6.168.5 |
  | hypothesis-jsonschema | 0.23.1 |
  | playwright | 1.63.0 (Chromium installed via `python -m playwright install chromium`) |
  | pyarrow | 25.0.1 |
  | numpy | 2.5.1 |
  | asyncpg | 0.31.0 |
  | nltk | 3.10.3 (+ punkt_tab) |
  | langdetect | 1.0.9 |
  | immutabledict | 4.3.1 |
  | absl-py | 2.5.1 |

  OS: Windows 10, with Git Bash for the shell commands.
- **General Compute:** the same key, `general_compute_api_key1` in backend/.env, on the same free tier
  (10M tokens/day). Start only when it reports at least about 5M tokens left today (section 7, step 2), so the
  run does not pause mid-way.
- **One run at a time:** nothing else may use the machine heavily during the run (it affects latency). Never two
  runners at once. Check with: `wmic process where "name='python.exe'" get ProcessId,CommandLine | grep runner.py`.

### 8.2 Best option: resume on the ORIGINAL machine

The state is already there. Follow section 7, steps 1-4, and do NOT reset the database.

### 8.3 On another machine: restore the exact state first

A bundle was made on the original machine at the moment of handoff. It is not in git; copy it securely, because
it contains the local test identity's private key:

```
C:\Users\user\stealth-lab-resume-bundle-2026-10-11\
  kel_abtest.dump            pg_dump -Fc of the test database (653 KB)
  abtest_local_state.tar     experiments/abtest/.local/{results, ws/C, C_home, jwks.json, signing_key.pem, token.txt} (27 MB)
```

Restore (Git Bash; adjust the Postgres bin path):

```bash
# NEVER `git checkout <commit> -- .` in a tree with other work: it overwrites uncommitted files. Use a separate, clean
# worktree at the pinned code and run everything from it:
git -C <repo> fetch origin && git -C <repo> worktree add ../stealth-lab-run origin/main && cd ../stealth-lab-run
git diff --stat 700cdb4 -- packaging backend/app experiments/abtest/*.py experiments/abtest/*.mjs experiments/abtest/exec.json   # must print nothing
PATH="<postgres-bin>:$PATH"
psql -h 127.0.0.1 -p 55432 -U kel_owner -d postgres -c "DROP DATABASE IF EXISTS kel_abtest WITH (FORCE)"
psql -h 127.0.0.1 -p 55432 -U kel_owner -d postgres -c "CREATE DATABASE kel_abtest"
pg_restore -h 127.0.0.1 -p 55432 -U kel_owner -d kel_abtest --no-owner <bundle>/kel_abtest.dump
mkdir -p experiments/abtest/.local && tar -xf <bundle>/abtest_local_state.tar -C experiments/abtest/.local
# the workspaces' hook commands hold ABSOLUTE paths of the original machine: re-install them with the product's own
# hooks-only installer, which is exactly what the runner did when it created each workspace
for ws in experiments/abtest/.local/ws/C/*/; do STEALTHLAB_HOME="$(pwd)/experiments/abtest/.local/C_home" node experiments/abtest/setup_c.mjs "$ws"; done
```

Then follow section 7, steps 2-4.

Also needed on the new machine:
- the scratch Postgres on 127.0.0.1:55432 with role `kel_owner` (trust auth);
- the phase 2 datasets under `experiments/multiple/data`, `experiments/design2code/data` and
  `experiments/websight/data` (sources in `multiple_js_task.py` and `page_task.py`);
- Claude Code logged in with access to Opus 5.5 and Sonnet 5.5.

**Not comparable across machines:** latency (A ran on the original machine). Cost and solved stay comparable.
State in the results which machine C's remaining tasks ran on.

### 8.4 When it finishes

- `python experiments/abtest/report.py phase2` gives the final table (A vs C per suite, saving, latency,
  not dispatched).
- Also redo bird-1505 for phase 1 (section 7, step 5), then `python experiments/abtest/report.py phase1`.
- Copy `.local/results/*.jsonl` into a new `experiments/abtest/results_<date>/` and commit it with the reports.

---

## 9. Update 2026-10-12 11:10 UTC: phase 2 MultiPL-E JS finished in arm C

- **What ran:** the identical phase 2 arm C command with `--only` the 18 remaining MultiPL-E JS task ids, on the
  original machine and the same state. Claude weekly usage went from 94% to 95%.
- **MultiPL-E JS, 20 tasks:**

  | Arm | Solved | Cost | p50 / p95 |
  |---|---|---|---|
  | A | 19 | $1.70 | 15 / 24 s |
  | C | 19 | $1.31 (-23%) | 29 / 136 s |

  - Cheap delivered 7; **not dispatched 10/20**.
  - C's only miss (mjs-51) was missed by A too.
- **Follow-up:** check which codebook groups these JS prompts fall into. Routing sent half of them straight to
  Opus, likely because they share a group with kinds where cheap models failed in phase 1.
- **Left in phase 2 arm C:** the 50 page tasks (d2c: 27, websight: 23).
  - Run with the identical command, no `--only`; the runner skips everything already done.
  - Expect a cost close to A's (the cheap models cannot see screenshots), so roughly 7-9% of a weekly limit.
  - Do it after the weekly reset (2026-10-13 20:00 UTC).
- The results snapshot `experiments/abtest/results_2026-10-11/phase2.jsonl` is updated. On the original machine,
  `.local/results/phase2.jsonl` is current. On another machine, the bundle's copy predates this update: replace
  its results/phase2.jsonl with the snapshot after extracting.


## 10. Corrections after Chaitanya's review (communications.md, 2026-10-12)

- **Run the rest on the ORIGINAL machine** (section 8.2). Restoring elsewhere needs exact versions:
  - PostgreSQL 18 (a 17 server cannot restore the 18.4 dump);
  - Python 3.14.6, Node 24.18.1, Claude Code 2.1.292;
  - a clean worktree.

  The MultiPL-E part of phase 2 C was finished there on 2026-10-12 (section 9). The 50 page tasks follow after
  the weekly reset, also there.
- **Section 8.3's restore no longer uses `git checkout 700cdb4 -- .`**, which was destructive. It now uses a
  separate worktree.
- **Unpaired phase 1 tasks (all four, not one):**
  - **arm C missing:** bird-1037, bird-1376, bird-1505. Run the phase 1 command with
    `--arms C --only bird-1037,bird-1376,bird-1505`.
  - **arm A missing:** bird-480. Run `--arms A --only bird-480`, without the arm C flags.
- **The resume bundle** contains a private key: secure transfer only, and delete it after use. It is not needed
  on the original machine.
- **General Compute:** the remaining 50 page tasks may span more than one daily reset (exit code 4, then rerun
  after 00:00 UTC). Say so in the results if it happens.
- **Phase 2 latency:** compare only if arm C finishes on the original machine; otherwise drop the latency column.
