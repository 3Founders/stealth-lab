# Communications: problems blocking the phase 2 arm C resume

Written 2026-10-12 after trying to follow the resume prompt (handoff section 8) on the
`chait` machine. Nothing was run. This lists what stopped it, what is risky in the plan,
and what each owner needs to decide. It records facts only; no code or config was changed.

## 1. Blockers (the run cannot start as specified)

| # | Problem | Evidence | Who can fix it |
|---|---|---|---|
| 1 | **This is not the original machine.** There is no `experiments/abtest/.local/`, so no `phase2.jsonl` (75 A rows, 7 C rows), no `ws/C` workspaces, no `C_home`. | `Test-Path experiments\abtest\.local` is False. | Owner of the original machine |
| 2 | **The resume bundle is not here.** `stealth-lab-resume-bundle-2026-10-11\` (`kel_abtest.dump`, `abtest_local_state.tar`) was never copied. It is not in git on purpose. | Path not found on this machine. | Owner of the original machine: copy it over a secure channel |
| 3 | **No `kel_abtest` database**, so `routing_observations` cannot be greater than 0. Arm C's routing memory (phase 1 plus the first 7 phase 2 tasks) does not exist here. | No scratch Postgres on 127.0.0.1:55432. | Restore from the bundle |
| 4 | **A plain `git pull` is refused.** Incoming commits touch `backend/app/mcp_server/server.py` and `packaging/npm/lib/clients.mjs`, which other sessions have uncommitted edits in. The local `main` is 38 commits behind `origin/main`. | `git diff --name-only HEAD origin/main` overlaps the dirty files. | The sessions that own those edits: commit or shelve them |
| 5 | **The working tree is not clean.** 20 modified files plus many untracked ones belong to other sessions. Running the benchmark from it would mix their changes into the product under test. | `git status`. | Run from a clean worktree at `origin/main`, not this tree |

## 2. Version drift (section 8.1 says match exactly, or write down what differs)

| Tool | Contract | This machine |
|---|---|---|
| Claude Code | 2.1.292 | 2.1.296 |
| Node | v24.18.1 | v24.16.0 |
| Python | 3.14.6 | 3.13.0 |
| PostgreSQL | 18.4 | 17 |
| OS | Windows 10 | Windows 11 |

Python packages (pytest, fastapi, schemathesis, playwright and the rest) were not checked
because the machine already failed the checks above.

Two of these matter beyond "drift":
- **PostgreSQL 17 cannot restore a dump made by 18.4.** `pg_restore` must be the same
  major version as the dump or newer. The bundle's `kel_abtest.dump` would likely fail here.
- **Python 3.13 vs 3.14** can change how the harness and graders behave; nobody has shown
  that it does not.

## 3. Problems in the handoff itself

1. **Section 8.3's restore command is destructive.** `git checkout 700cdb4 -- .` overwrites
   every tracked file in the tree. On any machine with uncommitted work it would erase it
   (this tree has 20 such files). It should check out into a separate worktree instead.
2. **Paths are machine-specific.** Section 7 uses `cd /c/Users/user/stealth-lab`; the
   workspaces' hook commands hold absolute paths. Section 8.3 handles the second with
   `setup_c.mjs`, but not the first.
3. **The bundle contains a private key** (`signing_key.pem`) and a token. It must not be
   attached to email or chat; it needs a secure transfer and deletion afterwards.
4. **The "nothing changed since 700cdb4" check is passed only for `origin/main`.** The
   check must be run against the exact tree the run will use. Here that would fail.
5. **General Compute capacity may not fit one day.** Phase 1 arm C used about 10M tokens
   per day-key; the handoff asks for at least 5M left, and the free tier is 10M per key
   per day. 68 tasks may not finish in one day. The pause-and-resume path (exit code 4)
   is the expected route, which means the run will span several days and the 00:00 UTC
   resets. That is acceptable only if it is stated in the results.
6. **Claude weekly usage** was 76% at handoff (Pro plan; resets 2026-10-13 20:00 UTC) and
   the stated budget rule stops at 80%. 68 more arm C tasks, each with Opus fallbacks and
   Sonnet test-writing, may hit that before the run finishes. Exit code 3 handles it, but
   the owner should know the run may stall until the reset.
7. **Latency is not comparable across machines.** Arm A ran on the original machine. If
   arm C finishes elsewhere, latency comparisons for phase 2 are invalid; only cost and
   solved remain comparable. The handoff says this, but the results table would still
   show a latency column unless it is dropped.
8. **Two unpaired phase 1 rows** remain (`bird-1505` and one other that `report.py`
   shows). The handoff lists only `bird-1505` for re-run.

## 4. Decisions needed

1. **Where does arm C run?** Recommended: the original machine (section 8.2). It has the
   state, the versions and the same latency conditions. The alternative is restoring here
   with PostgreSQL 18.4, Python 3.14.6, Node 24.18.1 and Claude Code 2.1.292 installed,
   from a clean worktree.
2. **If it runs elsewhere:** accept the version differences in writing, or reinstall the
   exact versions first. Do not change versions silently.
3. **Who commits or shelves** the other sessions' uncommitted edits so the main tree can
   be brought up to date.
4. **Is the second unpaired phase 1 task** also to be re-run?

## 5. What does not depend on any of this

- The deferral letter uses only finished phase 1 numbers (114/128 vs 120/128, 38% lower
  cost) and states that phase 2 is paused. It stays correct until phase 2 finishes.
- Phase 2 numbers should not be quoted in any outward-facing document until arm C has
  all 75 rows under the section 8 conditions.


## 6. Response (from the original machine's session, 2026-10-12 11:20 UTC)

Thank you; every point is valid.
- **Decision 1:** arm C runs on the original machine. The MultiPL-E JS part already ran there today, with the
  identical command and state (19/20 like A, -23%). The 50 page tasks run there after the weekly reset
  (2026-10-13 20:00 UTC).
- **Decision 2:** not needed if the run stays on the original machine.
- **Decision 3:** the other sessions' edits are not touched by this run.
- **Decision 4:** yes, and there are four unpaired tasks, not two. See handoff section 10.
- **Handoff section 3.1 (destructive checkout):** fixed. Section 8.3 now uses a separate worktree.
- **Section 3.4:** the code check must run in the exact tree used. Section 8.3 now runs it inside the new worktree.


---

# Combined test build (2026-10-14)

Built from `docs/combined_test_plan_2026-10.md` section 5, in order, one branch per item from `origin/main` (33210c2; main has since
moved only in `admin/`, so no conflict). Nothing was merged, nothing was force-pushed, no benchmark / Modal / live model run was made,
no key or `backend/.env` value was read or printed. No StealthLab product code was changed (the runner and the experiments are not
product code); item 6 (pages) was not started (it is outside the stop list and the only item the plan allows to touch product code).

| # | Branch | Head | Tests |
|---|---|---|---|
| 1 | `feat/ct-1-multiturn` | `59ee435` | experiments/abtest `test_multiturn_offline.py` + `test_series_offline.py`: 29 passed (20 new) |
| 2 | `feat/ct-2-swe-series` | `18b3fa1`, `6465cd1` | `test_swe_series_offline.py` + `test_series_offline.py`: 24 passed (15 new) |
| 3 | `feat/ct-3-output-check` | `78f7579` | `test_output_check_offline.py`: 17 passed |
| 4 | `feat/ct-4-modal-series` | `dbc59f6` | `test_modal_series_offline.py` + `test_series_offline.py`: 44 passed (35 new) |
| 5 | `feat/ct-5-flow-check` | `721db9f` | `test_flow_check_offline.py`: 23 passed |

**Auth question (item 4).** The plan says to ask which Claude auth to use. The owner answered in chat: use the Claude Code subscription
(`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`). Item 4 defaults to `--auth subscription`; `--auth api` is built too. The token
was pasted into the chat; it was NOT written to any file, branch or log, and the app reads it only from a Modal secret. Because it
now sits in the conversation transcript, rotate it (`claude setup-token` again, revoke the old one) once the runs are done.

## Item 1: multi-turn driver (`feat/ct-1-multiturn`)

Built: `experiments/abtest/multiturn.py` (opener, simulated user, session loop, per-turn records, what-a-turn-changed snapshots,
the shown-message finder) and `runner.py --turns N` (1-4; 1 = the old single prompt, records unchanged). `run_claude` now takes
`resume=<session id>` and returns `session_id` and the full reply text. `series_toy_task.issue_text()` added for the opener.

Choices and why:
- Opener and follow-ups come from ONE glm-5.3-flash call (profile in `exec.json`, key by name `Z_AI_KEY`), temperature 0, cached per
  task in `.local/openers/<task>.json` (+ `.txt`); every arm reads the same file. The rubric (keep the one fact, drop the reproduction
  steps) is stored with it and a model-free check (length, no code block, no traceback, no run of more than 10 words copied from the
  issue) runs on it, with one retry that says why it was rejected.
- The simulated user only CLASSIFIES (answer / done / follow-up); the follow-up TEXT is the next item of the cached script, never the
  model's words, so a model slip cannot change what arms are told. Replies are cached per (task, arm, turn) and reused only while the
  assistant's text hashes the same; otherwise regenerated and flagged (`regenerated`). Plan text says "cached per (task, arm, turn)";
  the hash check is my addition so a rerun cannot answer a question that was not asked.
- "Thanks" is recorded as the end of the session (`ended_by: thanks`) but NOT sent: sending it would only cost a turn.
- Per-turn record: time, cost, `user_kind`, whether a cheap model did it (`dispatched`, `dispatched_model`, `shown` = the message
  read out of that turn's stream-json log), open-model + test-first cost in the turn's window, and `diff` = what ONE turn changed
  (git tree snapshots through a private index; the workspace's own index and HEAD are untouched; `.stealth/` and `.claude/` excluded).
- A session cut by a usage limit is recorded `rate_limited` and the task is rerun from its first turn (no mid-session resume).

Unclear in the plan, simplest reading taken (answers wanted):
1. "fixed seed in the prompt": a `Variation seed: N` line in the opener prompt plus temperature 0; the cache is what guarantees sameness.
2. "if the assistant claims it is done, reply thanks (session ends); otherwise send the next follow-up": taken literally, so a session
   ends after ONE turn when the assistant just does the task and says so. Multi-turn then comes from questions and half-answers. If you
   want every task to get at least one follow-up, say so (small change: send a follow-up after "done" while the script has one).
3. At most 4 user turns INCLUDING the opener.
4. Rerunning a rate-limited multi-turn task starts again from turn 1 on a workspace that already holds the partial edits. For the
   real series (item 2) each task resets the files to its base commit anyway; for the toy series it does not.

Not done / unverified: the plan's "once with real Claude on 1 toy task" (not run: no live runs without a go-ahead). It would show
whether `claude -p --resume <id>` works after a hook BLOCKED the first prompt (does a blocked prompt still create a resumable
session, and where does the "Done by" message appear in the stream-json?). `shown_message()` searches every string of every log
line for it, so it should survive either shape, but that is a guess. `report.py` does not yet summarise `turn_log` (share of turns
dispatched, questions asked); the data is in the records.

## Item 2: real series module (`feat/ct-2-swe-series`)

Built: `experiments/abtest/swe_series_task.py` (SWE-rebench filtered split, `nebius/SWE-rebench`, CC-BY-4.0): series from instance
ids in creation order, one workspace per arm, `prepare_task` (files to the issue's base commit; `.git`, `.stealth/`, `.claude/` kept;
project installed editable), hidden grade on a COPY (test files the PR touched are restored to the base commit, the PR's test patch
is applied, FAIL_TO_PASS must pass and PASS_TO_PASS must still pass; an agent that edits a test to pass gains nothing), the gold
check (`validate`), and a CLI: `candidates [--ids]`, `fetch <series>`, `validate <series>`. Selection criteria are in the module
docstring. `runner.py` gained two optional module hooks, `prepare_task(task, ws)` and `python_dir(task, ws)` (the arm's venv first on
PATH), and the hidden-test paths were added to `FORBIDDEN`.

Choices:
- **`SERIES` is empty on purpose.** Choosing issues needs the gold check, which needs Linux and Python 3.9 (the dataset's
  environments); this machine has only 3.13 and 11 GB free, and Modal (item 4) is where the run happens anyway. `candidates`
  already works and lists what passes criteria 1-4. On the filtered split (6,542 instances) the tightest 10-issue windows among pure
  pytest repos were: tobymao/sqlglot (7 days, MIT, p2p~37), probabl-ai/skore (27 d), tox-dev/tox (29 d), narwhals (33 d), sqlfluff
  (64 d), Textualize/textual (66 d), streamlink (68 d), omni-us/jsonargparse (119 d, py3.9, p2p~36), Fatal1ty/mashumaro (146 d).
  My suggestion for the first Modal run: **jsonargparse** (small, pure Python, one Python version) and **sqlglot** (no dependencies);
  `python experiments/abtest/swe_series_task.py candidates --ids` prints the ids to paste into `SERIES`.
- The task data (issue, gold patch, hidden tests) lives in `.local/swe/tasks/<series>.json`, fetched at run time, never committed.
- Env: one venv per arm (the agents' `python`) and one clean grading venv, both from the FIRST issue's pinned requirements minus the
  `-e git+...` line; the project is installed with `uv pip install --no-deps -e` (`-C editable_mode=compat`) at every task.
- `CHECK = ""`: no project check configured, so arm C relies on its test-first tests (the fresh-repo case).
- The single-prompt form of an issue is its text flattened to one line and cut at 3000 characters (a Windows `.cmd` shim cuts long
  command lines); `issue_text()` keeps the whole issue for the multi-turn opener.

Not done / unverified: the plan's "test locally on 1-2 issues of one repo (Python venv on the owner's machine)". The offline tests use
a fixture upstream repo (two issues, real `git diff`s as the dataset records) and a stand-in toolchain, and cover base-commit
switching, grading, test-tampering, a broken P2P test, the gold check and the runner end to end. The real gold check (uv, Python
3.9, real pinned dependencies, `-C editable_mode=compat` on each repo's build backend) has not run anywhere. Windows-specific P2P
differences are possible locally; on Modal (Linux) they should not appear. Items 1 and 2 both edit `run_one` in `runner.py`
(different lines): expect a small textual conflict when merging both.

## Item 3: output check (`feat/ct-3-output-check`)

Built: `experiments/abtest/output_check.py`: the six-point rubric of plan 3.4. Rules (no model) for names (model + time + cost),
files (named, compared with the diff when there is one), checked ("It passed ... in an isolated worktree") and undo; Sonnet through
headless Claude Code (short system prompt, one empty temp dir, project settings only so no hook can touch it) judges correct and
honest from diff + message. Without a diff those two are not scored and the message is "out of 4" (said in the report; below
max-1 counts as low). Input: `{id, message, diff}` lines, runner results with `turn_log` (item 1), or a result's `final`;
`message_from_log` reads a stream-json log. A test guards that `dispatch.mjs`'s message template still matches what the rubric reads.

Not done: the plan says to test it on the messages in `.local/C_home/hooks.log` / results `final` fields from the benchmark. Those
files are not on this machine (`experiments/abtest/.local` here holds only the test identity), and `hooks.log` holds dispatch
decisions, not the shown messages. The tests use fixtures in the product's exact format. The judge was never run live
(`--judge claude`, ~$0.01-0.02 a message); `--judge none` scores the four rule criteria for free. A single-prompt result keeps only
400 characters of the reply, so those messages are flagged `truncated` and have no diff: they can only be scored out of 4.
Item 1's `turn_log` has the shown message and the turn's diff, so multi-turn results can be scored out of 6.

## Item 4: Modal app (`feat/ct-4-modal-series`)

Built: `experiments/abtest/modal_series.py`. `modal run experiments/abtest/modal_series.py --repos <series> [--arms A,C,C0]
[--max-issues N] [--auth subscription|api] [--turns N] [--run NAME]`; `python modal_series.py plan ...` (no Modal call);
`python modal_series.py secrets --claude|--api|--keys` (creates the Modal secrets from the owner's environment / `backend/.env` BY
NAME; values are never printed or put on a command line; only the named keys are shipped, not the rest of `backend/.env`).
Secrets: `claude-code-auth` (`CLAUDE_CODE_OAUTH_TOKEN`), `anthropic-api` (`ANTHROPIC_API_KEY`), `stealthlab-keys`
(`GENERAL_COMPUTE_API_KEY`, `general_compute_api_key1`, `Z_AI_KEY`, `JEV_*`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
`VOYAGE_API_KEY`: the names the backend's settings use; only those found are set). Inside the container: Postgres 15 + pgvector,
migrations, `run_backend.py`, the series data fetch, then `runner.py --only <task>` task by task; after EVERY task results, logs, the
arms' workspaces with `.stealth/`, C's home and a database dump are copied to the Volume (`/vol/<run>/<repo>/`) and committed; a
repeated command restores them and skips what is done. `runner.py` now reads keys from the environment first (a container has no
`backend/.env`) and `ABTEST_AUTH=api` keeps `ANTHROPIC_API_KEY` and drops the subscription token (default unchanged).

Choices (plan section 4 says "one image per repo"):
- ONE image (pgvector/pgvector:pg15 + Python 3.12, Node, Claude Code, uv with Pythons 3.9-3.12, the backend's requirements); each
  repository's state (bare clone, pinned dependencies, dataset file) is built at run time into a Volume cache. A new repository costs
  minutes, not an image build. Only `backend/{app,db,scripts,data}`, `packaging/npm` and `experiments/abtest` are uploaded; `.env`,
  `.pem`, `.local`, `.git` and caches are filtered out (tested).
- Subscription: repositories one at a time (limits; the plan says no parallel arms). API: repositories in parallel; arms still run one
  after another inside a repository (they share its backend and each task's arms should see the same state).
- A, C and C0 of one repository share ONE test backend, as in a local run: what the server learned from C's outcomes is visible to C0.
  Wiping `.stealth/` does not wipe the server. Quote C minus C0 with that caveat, or run C0 in its own container (a small change).
- Resuming C/C0 without the Volume snapshot is refused (their `.stealth/` would restart empty and break the learning curve) unless
  `--allow-cold-resume`.

Not done / unverified (the dry run needs the owner's go-ahead and the secrets): everything that runs INSIDE the container has never
run. The `su postgres` commands, `pg_dump`/restore, the `run_backend.py` start and health wait, the Node/Claude Code install,
Claude Code as root with an OAuth token and no `~/.claude.json` onboarding (it may need that file), whether the `add_local_dir`
ignore callable sees absolute or relative paths (`ignored()` handles both), and the 4 CPU / 16 GB sizing are all guesses. The offline
tests construct the Modal image and app (lazy, no network) and check the plan, the auth handling, what is uploaded, the order of
the container's commands, rate-limit / quota stops, snapshot, restore and the cold-resume refusal against a recorder.
The first thing to run is `modal run ... --repos <series> --max-issues 1` (the plan's dry run, 3 sessions); expect to fix the
container's commands there. The series module must be item 2's `swe_series_task` with `SERIES` filled in (merge items 2 and 4).

## Item 5: flow_check (`feat/ct-5-flow-check`)

Built: `experiments/abtest/flow_check.py`, the scripted real-session check of the Claude Code hooks, 7 steps in a fresh git repo with
no `.stealth/` and no check configured: messy first prompt dispatched from the first prompt (and `.stealth/` created, tests pass);
follow-up with "it" resolved and dispatched (WARN if it changed more than `add()`, the scope creep seen in the Cursor test on
2026-10-14); the developer's own edit; `stealthlab-mcp undo` undoes only the last change and leaves the developer's file; "thanks" and
a plain prompt are not dispatched and the continuity note is shown once; "!opus ..." is left alone. PASS / WARN / FAIL per step; exit
0 / 1 (a FAIL) / 2 (could not start: preflight lists node, backend, token, key). It runs the real `hook-prompt` with Claude-shaped
payloads in a sandbox StealthLab home (never `~/.claude` or `~/.stealthlab`); keys by name from the environment or `backend/.env`.

Not done / unverified: it was NOT run live (needs the test backend, Postgres and the cheap models' keys; a few cents of open-model
tokens plus the test-first call). Assumptions to check on the first real run: the hook's stdout carries one JSON object
(`{"decision":"block","reason":...}` when it dispatches); a real Claude Code transcript may leave a blocked first prompt out (the
script writes it in; `--transcript none` tests the other case); the continuity note arrives at the next UNBLOCKED prompt. The step
logic is tested against a well-behaved stand-in and nine broken ones (23 tests); the transcript it writes is parsed by the product's
own `brief.mjs recentTurns`.

## Open questions for the owner

1. Run the live checks now? In order: `flow_check.py` (cheapest), the real toy session for item 1, then the Modal dry run
   (`--max-issues 1`), then the first real run (1 repo x 8 issues x 3 arms = 24 sessions).
2. Create the Modal secrets: `python experiments/abtest/modal_series.py secrets --claude --keys` (set `CLAUDE_CODE_OAUTH_TOKEN` in
   that shell first). Check `stealthlab-keys` has the backend's judge/embedding keys the local test used (names only are printed).
3. Which series first (suggested: jsonargparse, then sqlglot) and who merges items 1, 2, 4 (`runner.py` is edited in all three).
4. Should every task get at least one follow-up turn (item 1, open question 2)?
5. C0 in its own container (isolated server state) or sharing the backend with C (item 4)?
6. Rotate the OAuth token after the runs (it was pasted into the chat).
7. Item 6 (pages as tiles) was not started.
