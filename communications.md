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

# Build queue 2026-10-12 (handoff 6.6): what was built, and what to check

Written by Chaitanya's Claude after working through section 6.6 of `docs/HANDOFF_abtest_2026-10-11.md`. Every item is its own branch from
`origin/main` (b612733), pushed, **not merged**. Nothing was run live: no benchmark, no `runner.py` run (the new series harness was only
dry-run and tested with a stand-in), no General Compute quota, no model call (model calls are stubbed in tests). No code or config of the
running A-vs-C experiment was touched.

## At a glance

| Item | Branch | New tests | Offline evidence on the saved results | Proof pending (live / bundle) |
|---|---|---|---|---|
| Q0 review | `feat/routing-check-calibration` (unchanged) | n/a | reviewed, see below | the owner's live check |
| 6.1 #1 check detection | `feat/q-61-1-detect-check` | 8 | none needed (benchmark sets the check) | real repos |
| 6.1 #2 hooks-only gaps | `feat/q-61-2-hooks-only-gaps` | 10 | none (no effect on the tasks) | |
| 6.1 #3 continuity | `feat/q-61-3-continuity` | 8 | none | |
| 6.2 A brief, gate, overrides (A1-A3, A5; A4 = #3) | `feat/q-62-a-task-brief` | 20 | guard replay: blocks **0 of 210** cheap wins (a first version blocked 18) | live: kind accuracy >= 90% (`scripts/score_brief.mjs`), no mis-gated benchmark task |
| 6.2 C safe apply (C1-C4) | `feat/q-62-c-safe-apply` | 11 | none needed (same answers applied) | |
| 6.2 B real repos (B1-B3; B4 = L2; B5 = A) | `feat/q-62-b-real-repos` | 10 | benchmark checks pin their test file, so B3 never triggers; B2 needs an ignored node_modules/.venv | live: effect of B1's context on solved tasks |
| 6.1 #5 Cursor | `feat/q-61-5-cursor-dispatch` | 12 | n/a | **cursor-agent not installed here: nothing ran against Cursor** |
| 6.1 #6 long-horizon harness | `feat/q-61-6-longhorizon-harness` | 9 (python) | n/a, harness only | the series themselves |
| L1 stop stuck runs early | `feat/q-L1-early-stop` | 17 | `scripts/replay_early_stop.mjs` ready; **needs the run logs** | "proof pending: run logs, to be checked by the owner" |
| L2 learned time limit (= B4) | `feat/q-L2-learned-timeout` | 8 | **10 of 198 cheap wins (5%) would be cut at mult 2; 2 at mult 3** | run records (`--runs`) and a live run |
| L3 agreement check | `feat/q-L3-agreement` | 12 (9 need hypothesis) | **catches 0 of the 6 known wrong cheap answers** (see the item) | live, function suites |
| GLM profiles | not built | | | needs the provider and the key NAME from the owner |

The three results you should read first, because they are not what the spec expected:
1. **L2 cuts cheap wins** on the saved data (5% at the specified 2x, 1% at 3x). The default is the spec's 2; I would try 3.
2. **L3 could not have caught any of the 6 known wrong answers** (no second passing answer existed, and none is a Python function answer).
3. **A real safety bug found while building B2:** on Windows `git worktree remove --force` deletes THROUGH a junction, so a worktree holding a
   link to the project's `node_modules` emptied the real one. B2 unlinks first; a regression test fails without that.

## Rules that were followed, and where they were bent

- One branch per item from `origin/main`; pushed; never to main, never forced, never merged. (A local `git merge-tree` dry run, which writes
  nothing, was used for the conflict map below.)
- Every new behaviour has an env switch; **with every switch off the old behaviour is restored** (table below).
- Stage by name only; no secrets; `backend/.env` never read.
- Existing tests were never weakened. One existing test asserted behaviour the Cursor prototype changed on purpose
  (`claude_exec.test.mjs`, `--with-exec` with Cursor); it now asserts the old message for another client and the new one for Cursor.
- The full packaging suite (`node --test test/*.test.mjs`, 350-360 tests) was run on every branch. Three tests are load-sensitive and fail
  only when the whole suite runs together on this machine (dispatch "latency: the tests are written while the cheap models run", cursor_hooks
  "only the first tool call waits...", and a runtime "gc" test); each passes alone. The handoff names the first two. The backend suite (about
  18 minutes) was not run: none of my branches touches `backend/`.
- During B2 development a throwaway repo's `node_modules` was destroyed by the very bug described above. No real project was touched.
- One of my own tests, before I added a stand-in `claude` on PATH, called the real `claude mcp remove --scope user stealthlab`. That server is
  registered at project scope on this machine, so nothing was removed (checked afterwards); the test no longer does it.

## Switches: all off = the behaviour before these branches

| Switch | Branch | Default |
|---|---|---|
| `STEALTHLAB_DISPATCH_DETECT=off` | #1 | on |
| `STEALTHLAB_CLAUDE_MD=off`, `STEALTHLAB_AUTO_SURVEY=off`, `STEALTHLAB_LIBRARY_AUTOTIDY=off` | #2 (`--keep-mcp` is a flag) | on (survey: off under `node --test`) |
| `STEALTHLAB_CONTINUITY=off` | #3 | on |
| `STEALTHLAB_BRIEF=off`, `STEALTHLAB_OVERRIDES=off` (`STEALTHLAB_BRIEF_MODEL`, `_TIMEOUT_MS`) | A | on |
| `STEALTHLAB_DISPATCH_UNDO=off`, `STEALTHLAB_DISPATCH_ASK=on` | C | undo on, ask off |
| `STEALTHLAB_DISPATCH_CONTEXT=off`, `STEALTHLAB_EXEC_LINK_DEPS=off`, `STEALTHLAB_EXEC_RELATED=off` | B | on |
| `STEALTHLAB_CURSOR_DISPATCH=off`, `STEALTHLAB_CURSOR_MODEL` | #5 | on with `--with-exec` |
| `STEALTHLAB_EARLY_STOP=off` (`_STEPS`, `_ERRORS`) | L1 | on |
| `STEALTHLAB_DISPATCH_LEARNED_TIMEOUT=off`, `STEALTHLAB_DISPATCH_LEARNED_MULT` | L2 | on, mult 2 |
| `STEALTHLAB_DISPATCH_AGREE=off` (`_WAIT_S`, `_EXAMPLES`) | L3 | on |

**If you merge any of these before the arm C resume finishes, set the switches above to `off` in the runner's environment**, or the A-vs-C
conditions change (handoff 8.1). The ones that would change arm C's behaviour on the benchmark tasks: A (brief and gate), B1 (context), L1, L2,
L3, #2 (CLAUDE.md block, survey, tidy via `setup_c.mjs`). The rest do nothing on one-prompt, one-check benchmark tasks.

## Merging them: the conflicts

All branches touch `packaging/npm/lib/dispatch.mjs` (and several `lib/hook.mjs`); the conflicts are textual (imports, the policy object, the
return objects of `dispatch()`), not logical. `git merge-tree` shows these pairs conflict:

`#1` x C, B, L3 | `#2` x #3, A | `#3` x A, C, L2, L3 | A x C, L2, L3 | C x L2, L3 | B x C, L2, L3 | L2 x L3.

Independent of everything: the long-horizon harness (Python only), Cursor (clients, bin, cursor_*), L1 (agent, executor, runtime; one line in
dispatch). A reasonable order: harness, Cursor, L1, #1, B, C, L2, L3, A, #3, #2. Two semantic points to watch when merging:
- A replaces `payload.prompt` with the brief text inside `dispatch()` and keeps the user's words in `rawPayload`; C's undo point and library
  titles must use `rawPayload.prompt` after A is merged (C currently reads `payload.prompt`).
- #3 and A both turn `runPromptHook` into a wrapper / add a `dispatchImpl` test parameter; take both.

The a16z and letter files in your working tree are unrelated to this queue and are not in any of these branches.

## Q0. Router check calibration (review only) -- branch `feat/routing-check-calibration` (6bdd530)

Not changed. Read the whole diff (8 files).

What I checked:
- `calibrated_check_rates` only touches alpha (false-accept) of the `tests` check, on the bundled prior only, and
  `STEALTH_ROUTING_TESTS_FALSE_ACCEPT=0` gives the old behaviour. Draws are scaled, so their spread stays. Fine.
- Audit counts come from `routing_observations.gold_correct`, which exists (migrations 120/121). `record_audit` labels
  only the caller's own latest accepted whole-task attempt (scope enforced by `_goal_log_pool`). Fine.
- `lib/audit.mjs` runs detached and has a switch (`STEALTHLAB_DISPATCH_AUDIT`, 0 under `node --test`). Fine.

Observations for the owner (not defects):
1. The audit asks a CLAUDE model (the test writer's model, or the session's) to judge a cheap answer. That costs real
   money on 10% of delivered answers and only audits when the model name contains "claude". The handoff's rule is
   "cheap models do the task"; the audit is a reviewer, not the task, so it fits, but the cost is not in the A-vs-C
   saving numbers unless the ledger (`dispatch_costs.jsonl`, what: "audit") is added to arm C's cost.
2. A reviewer says WRONG on right answers more often than the reverse (the file says so), so the false-accept rate it
   learns is biased UP, which makes the router more careful, never less. That is the safe direction.
3. `calibrated_check_rates` is used by the whole-task path only (`step_level` skips the audit read). Fine.
4. No backend test run on my side (slow); the branch ships `tests/test_routing_check_calibration.py` (86 lines) and
   `test/audit.test.mjs`. Not re-run by me.
Open question: should the audit's cost be counted in arm C's cost column? It is ~10% of delivered cheap answers.

## 6.1 #1. Check detection -- branch `feat/q-61-1-detect-check`

Built: `packaging/npm/lib/detect_check.mjs` (`detectCheck(root) -> command | null`), used by `dispatchCheck` after the
configured check and the matched library entry's check. Switch `STEALTHLAB_DISPATCH_DETECT=off`.

Design choices:
- Root only. A monorepo's sub-package tests are not guessed: a wrong check lets wrong answers in, which is worse than
  no dispatch. Weak signals do not count (a stray `test_x.py` with no pytest config and no tests dir).
- Order: dbt, go, cargo, node, python. The specific tool files win over python's loose signals.
- Node: a real `scripts.test` -> `npm test` (npm's "no test specified" placeholder is refused). A script that is just
  `vitest` -> `npx vitest run` (watch mode would hang the check). No script but a vitest or jest dependency ->
  `npx vitest run` / `npx jest`.
- Pure: nothing is run to detect, nothing written.
Interpreted: "pytest (pyproject/ini/tests dir)" = pytest.ini, conftest.py, `[tool.pytest]` in pyproject, `[pytest]` in
tox.ini, `[tool:pytest]` in setup.cfg, or a `tests/` or `test/` directory with a real `test_*.py` / `*_test.py`.
Tests: 8 in `test/detect_check.test.mjs`. Offline evidence on saved results: none needed. The benchmark workspaces set
`STEALTHLAB_DISPATCH_CHECK`, which still wins, so their behaviour is unchanged.
Open question: `python -m pytest -q` assumes `python` is on PATH; on a machine with only `python3` the check fails and
dispatch records a failure. Should detection use `python3` on non-Windows?

## 6.1 #2. Hooks-only gaps -- branch `feat/q-61-2-hooks-only-gaps`

Starting point: `feat/product-fixes-1-3` holds one empty WIP commit ("not started in code", no files changed), so there
was nothing to rebase; I built from origin/main and re-did the design from the handoff text.

Built:
- CLAUDE.md block (`lib/claude_md.mjs`): a marked block in the CLAUDE.md beside the settings.json the hooks are written
  to (~/.claude/CLAUDE.md on a real install). Reuses the marked-block helpers of `lib/instructions.mjs`, so re-install
  replaces the block (never two), the user's own text stays, and uninstall removes it (the file goes if nothing else is
  in it). Written by `installClaudeDispatch`. Switch `STEALTHLAB_CLAUDE_MD=off`.
- First survey (`lib/auto_survey.mjs`): the prompt hook starts `stealthlab-mcp survey <repo>` once per git repo,
  DETACHED, so a prompt never waits for it and its failure is invisible. "Once" = until `.stealth/claims.md` exists or
  the marker `.stealth/.auto-survey` does (written before the start, so a crashing survey is not retried on every
  prompt). Switch `STEALTHLAB_AUTO_SURVEY=off`; unset under `node --test` it is off.
- Tidy (`library_tidy.mjs` `dropExactDuplicates`, `autoTidy`): exact duplicates only = same normalised title, unit,
  outcome, check commands AND the same diff text. The newer-verified one stays; the other goes through the existing
  `dropEntry(.., supersededBy)`, so it lands in the archive. Runs after a library entry is added (dispatch and the
  capture Stop hook). `library tidy --exact` runs it by hand. Switch `STEALTHLAB_LIBRARY_AUTOTIDY=off`. An entry with no
  diff is never "exact".
- `--keep-mcp` (`install --with-exec --keep-mcp`): the hooks-only install AND the MCP server registration.

Not built / interpreted:
- The earlier 6.1 text says the CLAUDE.md block should mention a new `stealthlab-mcp find` command. 6.6's definition of
  done for #2 does not list it and no such command exists, so the block does not mention it. Question: build `find`?
- The block goes to the user-level CLAUDE.md, not each repo's. One install, one block. A per-repo block would need a
  hook-time write into the user's repo, which I did not want to do silently.
Things the owner must know before merging:
- `setup_c.mjs` (the A/B arm C workspaces) calls `installClaudeDispatch`; merged as is, it would add a CLAUDE.md block
  in every arm C workspace and the prompt hook would start surveys there. Both change arm C's conditions. Set
  `STEALTHLAB_CLAUDE_MD=off` and `STEALTHLAB_AUTO_SURVEY=off` in the runner's env, or do not merge during the run.
- After the first prompt a repo has `.stealth/`, which is what lets dispatch run there at all (it needs a root). So this
  item, with #1, is what makes dispatch available in a fresh repo.
Tests: 10 in `test/hooks_only_gaps.test.mjs`: block idempotent / user text kept / uninstall removes / deleted when alone
/ switch; `installClaudeDispatch` twice then the client's uninstall (with a stand-in `claude` first on PATH so the real
one is never asked to remove anything); `--keep-mcp` through the CLI dry run; survey started once, marker, switch,
not-a-repo, a throwing start; tidy exact only. Offline evidence on saved results: none (no behaviour on the tasks).

## 6.1 #3. Continuity -- branch `feat/q-61-3-continuity`

Built: `lib/continuity.mjs` plus a wrapper in `lib/hook.mjs`. After a dispatched (blocked) prompt, one JSON entry
(model, files, the prompt's first line, `shown` flag) goes in `<home>/hooks/continuity/<session_id>.json`, last 5 kept,
swept after 24 h. The NEXT prompt's context starts with "Earlier in this session, <model> changed <files> to <task>
(StealthLab, check passed)." one line per dispatch not yet shown. Switch `STEALTHLAB_CONTINUITY=off`.

Design choices:
- Said once, not on every later prompt: the session's transcript carries the line from then on, and repeating it every
  prompt would add tokens to every turn. "At most the last 5 lines" is the cap on what is remembered.
- If the next prompt is itself blocked (another dispatch), nothing is delivered and the lines wait.
- If the lookup adds no context (no server, triage said no, nothing resolved), the lines go out on their own.
- `runPromptHook` became a wrapper around the old body (`runPromptHookCore`); the only change inside the core is the
  `recordDispatch` call in the handled branch and an injectable `dispatchImpl` for tests. `dispatch()` now also
  returns `model` and `files`.
Interpreted: the task line is the prompt's first line (same `promptTitle` the library uses), at most 160 characters.
Question: should a one-prompt-per-session benchmark worry? Arm C's tasks are one prompt per session, so nothing changes.
Edge not handled: a slash command (/compact) as the next prompt also gets the lines, if any are pending.
Tests: 8 in `test/continuity.test.mjs` (4 unit, 4 through `runPromptHook` with a stand-in dispatch). No offline evidence
on the saved results: the feature only adds context after a dispatch.

## 6.2 A (A1, A2, A3, A5; A4 = 6.1 #3 above) -- branch `feat/q-62-a-task-brief`

Built: `lib/brief.mjs`; `dispatch()` gets the brief, the gate and a `force` flag; the prompt hook parses `!opus` / `!cheap`.
- A1 brief: `makeBrief` calls the `gpt-oss-120b` profile of exec.json (the stealth executor's provider, key by env-var
  NAME), temperature 0, 8 s hard timeout (`STEALTHLAB_BRIEF_TIMEOUT_MS`), the last <=6 text turns of `transcript_path`
  (tool traffic and system reminders dropped, 700 characters per turn, 4 KB total). Any failure gives `null` and today's
  raw-prompt behaviour. The brief text (task, parts, files, acceptance) replaces the prompt for the cheap models, the
  test-first writer and the cross-check. The library entry title and the audit keep the user's own words.
  The brief's cost is appended to `dispatch_costs.jsonl` (`what: "brief"`), next to the test writer's.
- A2/A3 gate: dispatch only for kind=code AND clarity=clear AND size!=large AND a check exists; otherwise `dispatch()`
  returns null and the session handles it as in arm A. Nothing is ever asked of the user from the hook.
- A5: `!opus` at the start of the prompt skips StealthLab for that prompt (no lookup, no dispatch, capture memory cleared).
  `!cheap` forces a dispatch attempt: the plan's "Opus first" skip and the brief gate are bypassed.
Interpretation / question: the spec says the prefix "is stripped from what the session sees". A UserPromptSubmit hook can
only add context or block; it cannot rewrite the prompt. So for `!opus` the hook adds one line telling the session the
prefix is an override and not part of the task; for `!cheap` the hook strips it for itself (lookup, brief, tests), and the
session only sees it if the cheap models fail. Is that enough, or should `!opus` be a different mechanism?
A bug I found by replaying the saved runs (kept here because it is the reason for the guard's final shape): the first
version of the deterministic "question or chat is never code" guard looked at how the prompt STARTS. The benchmark's own
prompts open with a question ("What is the percentage of borderless cards? -- Write one SQLite query ...") and it would have
blocked 18 of the 210 cheap-delivered answers in results_2026-10-11 (11 of 32 BIRD wins). Now it rules a prompt out only when
nothing in the prompt asks for a change (write, fix, implement ...) or names a file. Replay of the full prompts: 0 of 210
blocked (`node scripts/replay_brief_guard.mjs`; the saved rows keep ~200 characters of each prompt, so the fixed tail
every suite's `prompt()` appends is added back from the `*_task.py` files).
Fixture and bar: `test/fixtures/brief_prompts.json`, 36 prompts (12 messy code tasks incl. 2 unclear and 2 large, 12
questions, 12 chit-chat). Offline I can prove: the guard never rules out a code prompt in the fixture (12/12), it catches 18 of
the 24 questions and chat, and with a model that is wrong in the worst way (everything "clear small code") only 6 of the 24
non-code prompts get through, all of which the live model alone must catch. I cannot prove ">=90% kind right" or "never marks
a question or chat as code" without calling the model: `node scripts/score_brief.mjs` does it (one call per prompt, exit code 1
if the bar is missed). PROOF PENDING: that run, and arm C on a few suites to check the gate does not mis-skip benchmark tasks.
Costs and latency to expect: +1 cheap call per dispatch attempt (~$0.0003 at gpt-oss prices, ~1-3 s) before the cheap runs
start. It is awaited, not overlapped with the quota probe; overlapping them would hide most of it (not done: simplest first).
Risk worth a look: a brief that drops a detail from a clean benchmark prompt could cost a solved task. The brief prompt says
keep every name, number, value and example; the 4-6 test-first tests are written from the brief too. Question: should the raw
prompt be appended to the brief text as a safety net? The spec says brief, not the raw prompt, so I did not.
Tests: 20 in `test/brief.test.mjs` (parse, gate, guard on the fixture, worst-case model, transcript reading, the call with a
fake endpoint: cost row, no-profile, 503, bad reply, timeout, off, no-system-role; dispatch with the brief: task text, gate
cases, guard without a brief, fallback, !cheap force, switch off; test writer told the brief; overrides parse and through the hook).
Merge note: this branch and `feat/q-61-3-continuity` both edit `runPromptHook` in `lib/hook.mjs` and both add a
`dispatchImpl` test parameter; expect a small conflict there (take both).

## 6.2 C (C1-C4) -- branch `feat/q-62-c-safe-apply`

Built: `lib/undo.mjs`, `stealthlab-mcp undo`, and the apply path in `dispatch()`.
- C1 undo point: a hidden ref `refs/stealthlab/undo/<id>` written with git plumbing (hash-object -w, a temp index,
  write-tree, commit-tree, update-ref). It holds each file the answer will touch as it is now (including the user's own
  uncommitted edits; a file that does not exist yet is recorded as absent). The user's branch, index, working tree and stash are
  untouched (a test compares `git log`, `status` and `stash list` before and after). The ref keeps the objects safe from gc.
  A manifest under `<home>/undo/<id>.json` records files, model, task and (after the apply) each file's hash.
  `stealthlab-mcp undo [<id>] [--list] [--force]`: restores the latest applied point of the current repo; refuses when a touched
  file differs from what the answer wrote (the user edited it since) unless `--force`; removes files the answer created.
  20 newest per repo kept (older refs and manifests deleted). Switch `STEALTHLAB_DISPATCH_UNDO=off`.
- C2: `applyToCheckout` already refused when a touched file changed since the run started (message "refusing: target
  file(s) changed since the run started"). It threw, and dispatch did not catch it, so the hook logged "dispatch skipped" and the
  cheap work vanished silently. Now dispatch catches `err.changed`, discards the undo point, returns `notApplied`, and `triedNote`
  tells the session "... NOT applied: you edited <files> while it ran". The task then goes to the session as in arm A.
- C3: the block message now ends "To put it back as it was: stealthlab-mcp undo." It already said model, cost and files.
- C4 `STEALTHLAB_DISPATCH_ASK=on`: the run is verified but not applied; the prompt is blocked with the diff stat and
  `stealthlab-mcp exec apply <run_id>`. The worktree is kept by the runtime (7 days).
Design choices and questions:
- Fail open on the undo point: if it cannot be made (odd git setup) the answer is still applied and the message says no
  undo point exists. Fail closed would turn a rare git problem into a lost solved task. Question: prefer fail closed?
- The undo point uses the files of `result.diff.files`, the same list the runtime applies.
- `exec apply` (ask mode) does NOT create an undo point: it goes through the runtime directly. Question: should it?
- In ask mode the prompt is blocked, so Opus does not run; the user applies or drops the change by hand.
- The spec says "a git stash-like ref"; I did not use `git stash create` because it ignores untracked files and
  the index, and the answer may create files.
Tests: 11 in `test/undo.test.mjs`. Offline evidence on saved results: none needed; the same answers are applied.

## 6.2 B (B1, B2, B3; B4 = L2 below; B5 = the size gate in A) -- branch `feat/q-62-b-real-repos`

Built: `lib/exec/deps.mjs` (B2), `lib/exec/related.mjs` (B3), `lib/task_context.mjs` (B1), small hooks in `worktree.mjs`,
`runtime.mjs`, `dispatch.mjs`; `libraryParts` in `hook.mjs` is now exported.
- B1: the cheap models' task text = the prompt (still capped at 2000 characters), then "Context from this repository":
  the survey's facts (`readClaims`, 2000 characters) and the judged library matches (`libraryParts` in lean mode: only entries the
  lookup called a match, with steps and the diff that solved it), 4096 characters in all, labelled "not part of the request".
  The test-first writer keeps the prompt alone. Switch `STEALTHLAB_DISPATCH_CONTEXT=off`.
- B2: worktrees link the project's `node_modules`, `.venv`, `venv` and the run's PATH leads with the venv and `node_modules/.bin`
  (+ `VIRTUAL_ENV`). Links only when git ignores the directory in the project and in the worktree. Switch
  `STEALTHLAB_EXEC_LINK_DEPS=off`.
  SAFETY FINDING you should know about: on Windows, `git worktree remove --force` on a worktree containing a junction to
  node_modules deleted the contents of the REAL node_modules (my first experiment did exactly that to a throwaway repo). The fix:
  `removeWorktree` (the one place worktrees are removed: apply, cancel, gc) first removes the links with `rmdir`/`unlink`,
  which remove only the link. A regression test removes a worktree and asserts the real directory survived; with the unlink
  call disabled that test fails ("node_modules survived"), which I checked. Nothing writes through the link on purpose, but a
  run that runs `npm install` in its worktree WOULD modify the project's real node_modules through it. Question: make the link
  read-only, or refuse installs in the cheap agent's shell tool? (Today the agent's `run_shell` can run anything.)
- B3: related tests run first (pytest: `test_<module>.py` / `<module>_test.py` found by a bounded walk of the worktree; vitest
  `related`; jest `--findRelatedTests`); a failure ends the attempt with that run as its checks, a pass is followed by the full check
  that always decides. A check that already names its test files (every benchmark check does) is left alone, so the saved
  results are unaffected by construction.
Offline evidence on saved results: the benchmark suites' checks are `python -m pytest -q test_check.py` (names its file), `node
--test check.test.js` and similar, so B3 never triggers there; B2 triggers only where the workspace has an ignored
node_modules/.venv (the benchmark workspaces have none); B1 changes the text the cheap models see whenever the workspace has a
survey or a library match. B1 is the one that could move earlier results, and the effect on solved tasks can only be judged live.
PROOF PENDING (live): B1 on arm C. If the owner wants a conservative first step, turn it on only for matches (drop the claims part).
Not verified: the POSIX symlink branch of B2 (this machine is Windows). By construction the link is dropped again unless git
ignores it in the worktree, so the failure mode there is "no link".
Tests: 10 in `test/real_repos.test.mjs`.

## 6.1 #5. Cursor dispatch -- branch `feat/q-61-5-cursor-dispatch`

Starting point: `feat/cursor-dispatch` (7ba8273, "WIP, untested", only on the origin). I rebased that commit onto origin/main on a
new branch (the original is untouched) and ran it: its 11 tests and the Cursor hook / client / instruction tests all passed, so the
prototype was in better shape than its commit message said.
Approach: the blocking `beforeSubmitPrompt` (`{"continue": false, "user_message": "Done by <model> ..."}`), not a `solve_cheaply`
MCP tool. I read Cursor's hooks page on 2026-10-12: beforeSubmitPrompt "can prevent submission", runs before the backend
request, takes a per-hook `timeout`, and cannot rewrite the prompt. A tool the rules tell the model to call first depends on the
model choosing to call it; that is the reason the Claude Code hooks exist.
Added: `docs/cursor_dispatch.md` (the code referenced a doc that did not exist): what Cursor allows, the flow, the limits, the
switches, and the manual proof steps. An install/uninstall test through the real CLI. One existing test (`claude_exec.test.mjs`,
"--with-exec without Claude Code writes nothing") asserted the old behaviour that the prototype changed on purpose (Cursor +
`--with-exec` now installs dispatch); I split it: the old message is still asserted for a client that is neither (windsurf), and the
Cursor case asserts the new message and that nothing is written under `.claude`. That is changing a test to match a decided change,
not weakening it; flagging it so you can check it.
Limits written in the doc: Cursor's "auto" model is not a model (no dispatch unless `STEALTHLAB_CURSOR_MODEL` is set); no prompt
rewriting (so `!opus` / `!cheap` from the A branch cannot be stripped in Cursor); the user sees a blocked prompt with our message,
not an answer in the chat. Uninstall of the Cursor hooks does not reset `dispatch: true` in the saved config (it is only reset when
Claude Code is uninstalled); harmless today, noted.
PROOF PENDING: `cursor-agent` is not installed here, so nothing ran against a real Cursor. Steps are at the end of the doc.
Tests: `test/cursor_dispatch.test.mjs` (11 from the prototype + 1 new). Full suite on this branch: 349 of 355 pass, 3 skipped, 3 fail under whole-suite load only (dispatch latency, the runtime gc test, the Cursor first-tool-call wait; each passes alone). It does not use detectCheck / the brief / undo, which are on
other branches; after merging them the Cursor hook calls the same `dispatch()` and gets them.

## 6.1 #6. Long-horizon test harness -- branch `feat/q-61-6-longhorizon-harness`

Built (harness only; nothing was run, no model, server, key or network): `experiments/abtest/runner.py`, `report.py`,
`series_toy_task.py`, `test_series_offline.py`.
- `runner.py --series module[:name]`: an ordered series of tasks in ONE workspace, in real order; the workspace persists for every arm
  and C's `.stealth/` with it. Existing resumability, pause codes and arm order are unchanged; `--tasks` is unchanged.
- Arm C0 (series only) = C with `.stealth/` wiped before every task (library, survey facts, routing counts, indexes; the directory and
  `.keep` stay so it is still a StealthLab repo). C minus C0 is what a persistent `.stealth` is worth.
- `--dry-run`: prints the tasks, arms, what each arm starts with and the arm order per task; runs nothing, creates nothing.
- A series module may define `setup_project(task, ws)` (shared project, built once per workspace and arm) and `grade_workspace(task,
  ws)` (grade the whole workspace, which a dbt project needs). Result rows get `series` and `position`.
- `report.py <run> --curve`: solved and cost per position per arm, plus cost of positions after the first for C and C0 against A.
- `series_toy_task.py`: a 3-task series in one repo with a repo convention (TypeError for non-str, docstrings) that the hidden grade
  checks across ALL earlier functions, so a task can pass its visible check and still fail the grade. It doubles as the template.
Not built, and why:
- The Spider 2.0-DBT series module. `feat/longhorizon-dbt` only has data notes; the data (49 MB of projects, Google Drive databases) is
  not here, and the series construction is undecided (its README: run each family's projects in order in one workspace, or build a
  union project with all target models removed). I would not guess that. Question for you: which construction? In-order needs each
  later project's state to equal the earlier project's end state, which the families probably do not guarantee; the union project is
  one workspace by construction and fits `setup_project` + `grade_workspace` directly.
- SWE-rebench / SWE-EVO / ADE-bench: need Modal and keys, per the handoff.
Design questions / things the series cannot isolate:
- C and C0 share the server's routing memory (the `kel_abtest` database), which learns across the series for both. The ablation
  isolates the LOCAL `.stealth`; the server-side learning is common to C and C0. A separate database per arm would remove it but
  loses comparability with phase 1/2 C. Question: which do you want?
- The three arms run each task in a hashed order (as today), so within a task C0 and C may feed the server in either order.
- With the auto survey (q2) on, C0 would re-survey after each wipe, giving C0 the survey facts back; set `STEALTHLAB_AUTO_SURVEY=off` in
  the series runs if the ablation should include the survey, or leave it on to measure only the library. Question for the owner.
Tests: 9 in `test_series_offline.py` (stand-in `claude`, real git, real grader).

## L1. Stop stuck cheap runs early -- branch `feat/q-L1-early-stop`

Built: `lib/agent/stall.mjs` (the four rules), wired into `lib/agent/step_agent.mjs` (per tool call), `lib/executors/stealth.mjs`
(passes the switch and thresholds; reads `stalled`), `lib/exec/runtime.mjs` (a stalled attempt is `failed`, no check run, reported),
`lib/dispatch.mjs` (the tried list says "failed, stalled (rule x)").
- (a) the same command's check failed twice with the same signature (failing test names, or the first error line when none are
  recognised: pytest, jest/vitest, go, cargo) and the edits between touched nothing the failure points at (a "touch" = an edited file
  whose path appears in the failure output, or no edit at all). (b) a file's content hash returns to an earlier value (the original
  counts; a rewrite with identical content is a no-op, not a return). (c) 3 consecutive tool calls that returned `error:`.
  (d) 6 consecutive tool calls without a change to the worktree fingerprint (git status + diff HEAD + untracked contents).
- Reported as FAILED with `stalled: {rule, why}`; counted against the model: report_result gets `accepted:false` with
  `check_kind: "tests"` (without that, an attempt with no check run is reported as `self_report`, which the server may weigh
  differently; I chose "tests" so a stall is a failure of the project's check, as the spec's "it did fail" says. Question: is that
  the right evidence kind for the router?).
- Thresholds env: `STEALTHLAB_EARLY_STOP_STEPS` (6), `STEALTHLAB_EARLY_STOP_ERRORS` (3); switch `STEALTHLAB_EARLY_STOP=off`.
Interpretations (please check):
1. Rule d as written ("6 steps with no change to the worktree diff") would stop every run that explores for 6 calls before its first
   edit, which most runs do. I count it only after the first change. Without that guard it would have "stopped" passing runs.
2. "(state `stalled`)": the runtime state stays `failed` (dispatch loops until a TERMINAL state, and `stalled` is not one); `stalled` is
   an extra field on the attempt, the run result and `run.json`, and the event log says which rule.
3. A stalled attempt skips the check (the point is to stop early), so it cannot be rescued by a lucky edit; the test proves it fails
   an attempt whose edit would have passed. That is the spec's reading ("report it as failed").
A real bug the tests found: `fileHash` passed `safePath()`'s `{abs, rel}` object to `readFileSync`, so every hash was "absent" and rule b
never fired. Fixed; the agent-level test with real files covers it.
Risks: rule d (even after the first edit) can stop a legitimate debugging stretch of 6 read-only calls after an edit; (a) can stop a run
that edits a file the traceback does not name (a fix in a config or a different module). Both thresholds are tunable. The replay below
is the way to see how often.
PROOF PENDING (owner): run logs. `node scripts/replay_early_stop.mjs <C_home/runs>`: (i) failing attempts stopped and estimated seconds
saved, (ii) attempts that PASSED but would be stopped, must be 0. A saved log only has progress lines, so it replays (c) exactly and (d)
as an under-estimate (read-only calls only); (a) and (b) need check output and file contents the log does not keep, so they cannot be
replayed; a live arm C run on the 20 JS + 20 OpenAPI tasks is the check for them. Seconds saved are estimated as duration x (steps after
the stop / all steps) because the log has no per-step times.
Tests: 17 in `test/early_stop.test.mjs`; the two other timing tests that fail under whole-suite load are unrelated (they pass alone).

## L2 (= B4). A time limit per kind of task, learned from this repo's cheap wins -- branch `feat/q-L2-learned-timeout`

Built: `lib/library.mjs` (recording, parsing, `routeWins`, `learnedLimitS`), `lib/dispatch.mjs` (uses it, records a win's time,
returns `limit_s`), `scripts/replay_learned_timeout.mjs`.
- Recording: when a cheap attempt is accepted, `recordObs(..., {winSeconds})` appends the time from the start of the cheap tries to the
  pass to the route's list (last 20) and rewrites `p90s=` / `wins=` on EVERY OBS line of that route. Why `wins=` and not only `p90s=`: a
  rolling p90 of the last 20 cannot be updated from the previous p90 alone; the 20 numbers have to be kept. They are short. Old OBS lines
  without them parse and render byte for byte as before (a test) and the server's Python `parse_routing` ignores the new keys (I ran it).
- Limit: `clamp(mult x p90, 60 s, today's limit)` with `mult` = 2 by default; fewer than 5 wins on the route keeps today's limit. The route
  is the Goal's route in `routing.md` (`g=` matches the plan's goal id, which is the Goal or the virtual key: library entry, repo+kind, kind).
- Switches: `STEALTHLAB_DISPATCH_LEARNED_TIMEOUT=off`; `STEALTHLAB_DISPATCH_LEARNED_MULT` (default 2).
Measured from the start of the cheap tries (the same moment the time limit starts), so it is the quantity the limit constrains; a win after a
cross-check includes the cross-check's time.
WHAT THE SAVED RESULTS SAY (not what the spec expected): the spec asks for "how many cheap wins would have been cut (must be 0 by construction
on the data used)". With the suite as the route and the session's wall time as a win's time, 10 of 198 delivered cheap wins (5%) would have
been cut at mult 2 (bcb 5, ds1000 2, mbpp 1, bird 1, ifeval 1), 2 at mult 3 and 2 at mult 4. A cut win goes to the session's model: cost
and latency up. The per-row wall time overstates the real cheap-tries time (it includes the lookup and the test-first call), and the suite
is coarser than the real route, so the real numbers are not these; but nothing suggests they are 0. Seconds saved on failures cannot be got
from the rows at all (Claude's own `duration_ms` includes the hooks, so the cheap tries' wait cannot be separated), so I have no offline
saving to set against the 5%. PROOF PENDING: `node scripts/replay_learned_timeout.mjs --runs <bundle>/C_home/runs [--mult 3]`, which uses each
attempt's own duration and the instance key's goal as the route.
Recommendation: do not run arm C with mult 2 on the strength of this; mult 3 cuts 1% on these rows. The multiplier is an env var, so a live arm
C run can try 3 without a code change.
Question: the spec's floor is 60 s; the replay's win times are mostly 50-200 s because they include hook time, so the floor is not what binds here.
Tests: 8 in `test/learned_timeout.test.mjs`.

## L3 (= 6.3 "Agreement check") -- branch `feat/q-L3-agreement`

Built: `lib/agree.mjs` (a small Python helper written next to the test-first file, run with `python`; the comparison in JS), a second-pass step in
`dispatch()`, `scripts/replay_agreement.mjs`.
- Trigger: after the first cheap answer passes (`judge` ok) and test-first tests exist and the check names pytest and the first answer defines a
  top-level Python function, dispatch waits up to `STEALTHLAB_DISPATCH_AGREE_WAIT_S` (10) for a second cheap run that is still going (or already
  finished); "do not wait for a second model if none is running" is literal: no pending run, no wait. The second is judged like any other (its own
  check), and only a second that PASSES is compared.
- Comparison: seeds = the literal arguments of the calls in the test-first file; a hypothesis strategy per argument inferred from the seeds' types
  (int, float, str, bool, list, tuple, dict, None); 40 derandomized examples per function; each answer runs in its own process in its own worktree;
  compared: return value (floats to 9 places, -0.0 = 0.0), the arguments after the call (in-place changes) and the exception TYPE.
- Outcome: agree -> first applied, second released; disagree -> neither applied, no further cheap rung (the spec: "go up the ladder"), the session is
  told which function and input differed; inconclusive -> accepted as before. `dispatch()` returns `agreement`.
Interpretation: the spec says "accept only if they agree; on disagreement, go up the ladder". I read "go up the ladder" as the session's model takes
over, not "try the next cheap rung": two answers that pass the tests but differ mean at least one is wrong and we cannot tell which; a third cheap
answer could not break the tie by itself. Question: or should a third cheap rung be tried and the majority win?
The router still records both passes as accepted (the checks accepted them); I did not add a "disagreed" outcome to the router.
OFFLINE PROOF (the one the spec asked for), reported straight: of the 6 wrong cheap answers in phase 1 (4 BIRD: bird-1028, bird-1107, bird-879,
bird-1166; 2 DS-1000: ds1000-50, ds1000-831) the check catches 0. (i) no second passing answer existed: in 5 the second parallel run had been
CANCELLED when the first passed and in 1 it had failed, which is exactly what this change alters (it now waits up to 10 s for the second), but the
saved rows cannot say whether those second runs would have passed or differed; (ii) none is a Python-function answer: BIRD is a SQL query, DS-1000 is
a snippet that assigns `result`, so there is nothing to call and compare. By the spec's own scope ("pytest projects only", functions the tests call)
the check applies to BigCodeBench and MBPP+, and none of the 6 is from those suites. So this item cannot be justified by those 6 cases; its value is
a bet on function tasks, unmeasured. Cost to expect: up to 10 s extra latency on a function task when two cheap runs are in flight, and the second
run's tokens for that time.
Environment note: hypothesis is not installed on this machine's Python; I tested in an isolated venv (pytest 9.1.1, hypothesis 6.169) outside the
repo. The 9 tests that need it skip without it, so the default suite does not depend on it; `pip install hypothesis` is the only requirement, and the
benchmark environment already has it (handoff 8.1: hypothesis 6.168.5).
Tests: 12 in `test/agreement.test.mjs`.

## Not built, and why

- **GLM profiles** (`glm-5` code, `glm-4.6v` pages): the spec waits for the owner to name a provider and a key NAME (z.ai or OpenRouter). Nothing
  to build until then; `profile.mjs` already takes `key_env` as a variable name and the router already has GLM priors.
- **The Spider 2.0-DBT / SWE-rebench / ADE-bench series modules**: data and Modal not available here; the dbt series construction is an open
  question (see 6.1 #6). The harness and a template series are built.
- **The latency ideas the handoff said not to build**: not built.
- **`stealthlab-mcp find`** (mentioned in the old 6.1 #2 text, not in 6.6): not built; the CLAUDE.md block does not mention it.

## Open questions, in one place

1. Q0: should the audit's Claude cost be counted in arm C's cost column?
2. #1: use `python3` on non-Windows when `python` is absent?
3. #2: build `stealthlab-mcp find`? A per-repo CLAUDE.md instead of the user-level one?
4. A: is a context line enough for `!opus` (a hook cannot rewrite the prompt)? Should the raw prompt ride along with the brief as a safety net?
5. C: fail closed instead of open when an undo point cannot be made? Should `exec apply` (ask mode) make an undo point?
6. B: make the dependency link read-only, or refuse installs in the cheap agent's shell tool (a run's `npm install` would write through the link)?
7. #6: dbt series construction (in order vs a union project); one server database per arm or shared; auto survey on or off in the series.
8. L1: is `check_kind: "tests"` the right evidence kind for a stalled attempt? Is rule d (after the first edit) acceptable?
9. L2: default multiplier 2 (the spec) or 3 (cuts 1% instead of 5% on the saved rows)?
10. L3: on disagreement, hand to the session's model (built) or try a third cheap rung and take the majority?
