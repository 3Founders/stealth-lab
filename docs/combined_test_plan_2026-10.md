# Combined real-use test: long horizon, `.kell`, multi-turn, messy/vague input, what the user sees (plan, 2026-10-14)

The A-vs-C benchmark (docs/HANDOFF_abtest_2026-10-11.md, sections 11-12) measured kell on one-prompt, clean,
independent tasks: on 150 code tasks C saves 49% at 137 vs 140 solved. This test measures what that cannot:

1. **Long horizon:** a series of real issues in ONE repository, in the order they happened, so `.kell/` (library,
   survey facts, routing record) builds up the way it does for a developer working in a repo for weeks.
2. **What `.kell/` is worth:** arm C0 = C with `.kell/` wiped before every task. C minus C0 is the value.
3. **Messy, vague, multi-turn input:** each task starts with a vague message and continues over 2-4 turns in one
   session, with a simulated user who knows the real issue and answers questions.
4. **What the user sees:** the message shown when a cheap model did the task (who, time, cost, undo, explanation) --
   is it correct, clear, and enough?

Read section 6 (rules) before building anything. Everything here is to be BUILT unless marked BUILT.

---

## 1. Current product configuration (what arm C runs)

- Main at `329a0d5` or later. Hooks-only install (`kell install --with-exec`), as in the benchmark.
- Cheap models (`experiments/abtest/exec.json`): gemma-4-31b-it, gpt-oss-120b, minimax-m2-7 on General Compute, and
  **glm-5.3-flash on z.ai** (`strong: true` -- it alone tries tasks the brief calls large, starts in the first wave, gets
  +120 s; `reasoning_effort: low`; `vision: false`).
- **Tests written in advance: Sonnet** (`--testfirst-model claude-sonnet-5-5`). GLM-5.3 as test writer was tried and
  rejected correct answers too often (first 20 tasks: saving -55% -> -35%) -- do not use it.
- **Page (screenshot) tasks go to Opus.** GLM-5.3-flash pages were good ~5 in 10; a Sonnet judge (lib/visual_check.mjs,
  ~$0.02/page) is built but off for pages (`vision: false`). Open item: send long screenshots as tiles (section 5.6).
- Keys by NAME in `backend/.env`: `GENERAL_COMPUTE_API_KEY` / `general_compute_api_key1`, `Z_AI_KEY`, `NVIDIA_API_KEY`
  (NIM: too slow for live use). Never print a key.

## 2. Already built (use, do not rebuild)

| Piece | Where | What it does |
|---|---|---|
| Series mode | `experiments/abtest/runner.py --series module[:name]` | ordered tasks in one workspace per arm; `.kell/` persists |
| Arm C0 | same | C with `.kell/` wiped before each task (dir and `.keep` kept) |
| Learning curve | `report.py <run> --curve` | solved and cost per position, per arm |
| Series template | `series_toy_task.py`, `test_series_offline.py` | 3 tasks, a repo convention later tasks must keep; `setup_project` / `grade_workspace` hooks |
| Product: messy input | `packaging/npm/lib/brief.mjs` | brief from the last 6 turns + files the prompt names + the project's file list; gate (code/clear/size); `!opus` / `!cheap` |
| Product: multi-turn | `lib/continuity.mjs` | the next prompt is told what a cheap model did earlier in the session |
| Product: output | `lib/explain.mjs`, `lib/undo.mjs` | explanation by a cheap model in the hook's message; `kell undo` |
| Real-flow script | (was a scratch script) | drove the hook directly in a fresh repo; turn it into `experiments/abtest/flow_check.py` (5.5) |

## 3. Design of the test

### 3.1 Tasks: real issues in order
- Source: SWE-rebench (or SWE-Gym / SWE-bench-extra) -- real GitHub issues with the fixing PR's tests:
  FAIL_TO_PASS (must start passing) and PASS_TO_PASS (must keep passing).
- Pick **2-3 pure-Python repositories** with **>= 8 issues each** that can be ordered by date and that share one
  environment (one Python version, dependencies installable from the repo at the first issue's commit; skip repos
  needing system libraries or a database). Prefer small-to-medium repos (< 50k lines).
- Ordering: by issue creation date. Each task starts from the repo at that issue's base commit **with the previous
  tasks' accepted fixes NOT applied** (each issue has its own base commit; the workspace is reset to it), BUT the
  `.kell/` directory is carried over in C (and wiped in C0). This keeps grading exact (each issue's tests at its own
  commit) while knowledge carries over. Write this down in the module's docstring.
- Grading: run FAIL_TO_PASS + PASS_TO_PASS at the issue's base commit with the agent's diff applied; solved = all pass.
  Never show these tests to any arm (they live outside the workspace, like today's hidden tests).

### 3.2 Messy, vague, multi-turn sessions
- **Turn 1 (vague opener):** a cheap model (glm-5.3-flash, temperature 0, fixed seed in the prompt) rewrites the issue
  as a busy developer would type it: short, no file names unless the issue names them, may refer to "the parser" or
  "that error". Cached per task (`.local/openers/<task>.txt`) so every arm gets the SAME opener. Store the rubric:
  must keep the one fact needed to find the problem; must drop the reproduction steps.
- **Simulated user:** glm-5.3-flash holding the full issue text. After each assistant turn it decides: if the assistant
  asked a question, answer it from the issue (never reveal the tests or the fix); if the assistant claims it is done,
  reply "thanks" (session ends); otherwise send the next follow-up from a short script derived from the issue (e.g.
  "also it breaks when the input is empty"). At most 4 user turns. Cached per (task, arm, turn) for reruns.
- **Sessions:** one Claude Code session per task, continued with `claude -p --resume <session_id>` for each turn
  (the runner today sends one prompt per task: `run_claude` in runner.py). Same session for all turns, so the hooks see
  `transcript_path` with the earlier turns (that is what the brief and continuity read).
- Cost and latency: sum `total_cost_usd` and wall time over all turns; also record per-turn time and which turns were
  dispatched to cheap models.

### 3.3 Arms
- **A:** plain Claude Code, Opus 5.5.
- **C:** kell hooks-only, configuration of section 1, `.kell/` persists across the series.
- **C0:** as C, `.kell/` wiped before each task.
- Same opener, same simulated user (cached), same hidden tests for all arms. Arms run each task in a hashed order
  (as the runner does) so no arm always goes first.

### 3.4 What the user sees (output check)
For every turn C (or C0) answered with a cheap model (the hook blocked the prompt), store the shown message and score
it with a fixed rubric by a judge model (Sonnet, through Claude Code with a short system prompt, as in
lib/visual_check.mjs) -- 1 point each: names the model and the time/cost; names the changed files; explanation is
correct for the diff (judge sees diff + message); says how it was checked; says how to undo; no claim the diff does not
support. Report the mean and every message scoring < 5/6.

### 3.5 Metrics (per arm; per repo and overall)
Solved (hidden tests), cost (sum over turns), wall time p50/p95/p99 per task and per turn, share of turns dispatched,
cheap wins, wrong cheap answers (accepted but hidden tests fail), questions asked by the assistant, output-check score,
learning curve (solved and cost by position in the series). C minus C0 = value of `.kell/`.

## 4. Where it runs: Modal

- Each repo needs its own environment (Docker image) -> Modal, one image per repo: the repo's Python and deps, Node,
  Claude Code, the kell package from this repo, Python with pytest/playwright, **Postgres and the kell
  backend inside the container** (`backend/scripts/migrate.py`, `experiments/abtest/run_backend.py`; a Modal container
  cannot reach a laptop's backend). Results and logs to a Modal Volume; the runner's resumability keyed by run name.
- **Claude auth:** either (a) the owner's subscription: `claude setup-token` on the owner's machine, the token stored as
  Modal secret `CLAUDE_CODE_OAUTH_TOKEN` (counts against the owner's 5-hour/weekly limits; no parallel arms), or (b) an
  Anthropic API key (`ANTHROPIC_API_KEY`, billed per token; parallel OK). The app uses (a) when set, else (b). Ask the
  owner which before running. Note the runner strips `ANTHROPIC_API_KEY` from the session env today (arm_env) -- make
  that conditional for Modal.
- Other secrets: `GENERAL_COMPUTE_API_KEY`, `Z_AI_KEY` (Modal secret `stealthlab-keys`; created by the owner).
- Size: start with **1 repo x 8 issues x 3 arms = 24 sessions** (~$15-30 of Opus-equivalent; on the subscription ~a
  day's normal use). Full: 3 repos x 10 issues x 3 arms = 90 sessions (~$50-150 on the API).

## 5. Build list (in order) -- each item: branch `feat/ct-<n>-<name>`, tests, push, never merge

1. **Multi-turn driver** (`experiments/abtest/multiturn.py` + runner flag `--turns`): opener generation and caching,
   simulated user, `--resume` turns, per-turn records, summed cost. Test on `series_toy_task.py` locally with a
   stand-in `claude` (as test_series_offline.py does) and once with real Claude on 1 toy task.
2. **Real series module** (`experiments/abtest/swe_series_task.py`): repo/issue selection (write the chosen repos and
   issue ids into the module, with the selection criteria), per-issue base commit checkout, hidden-test grading,
   `setup_project` for the environment. Test locally on 1-2 issues of one repo (Python venv on the owner's machine).
3. **Output check** (`experiments/abtest/output_check.py`): the rubric of 3.4, run on stored messages; test it on the
   messages already in `.local/C_home/hooks.log` / results `final` fields from the benchmark.
4. **Modal app** (`experiments/abtest/modal_series.py`): image per repo, backend + Postgres in the container, secrets,
   volume, `--repos --arms --max-issues --auth subscription|api`; a dry run on 1 issue per arm.
5. **flow_check.py:** the scripted real-session check (fresh repo, no check configured, messy prompt, follow-up with
   "it", own edit, `kell undo`, "thanks") as a committed script -- every kell change should pass it.
6. **Pages (separate, small):** send long screenshots as tiles (to GLM and to the judge) instead of cutting at 2400 px;
   re-run GLM-flash on the 5 failed pages of the 10-page test. If the pass rate rises clearly, turn `vision: true` back
   on for glm-5.3-flash with the Sonnet judge and re-run the page tasks.

## 6. Rules (same as the build queue, handoff 6.6)

- Branch per item from `origin/main`, push the branch, never push to main, never force-push, never merge.
- No live benchmark or Modal run beyond the dry runs named above without the owner's go-ahead (they use Claude usage,
  General Compute/z.ai quota or Modal credits).
- Never read or print `backend/.env` values; keys by NAME only; stage files by name; no secrets in logs or commits.
- Nothing may make the existing A-vs-C results worse: the product code is not changed by this test work, except where
  an item says so (then with tests and a switch).
- Measure honestly: cached openers and simulated-user replies so arms see the same inputs; report every number with
  what it does and does not include (e.g. the server-side lookup cost is not counted).
- Notes as you go in communications.md: per item, branch, what was built, choices made and why, open questions.
