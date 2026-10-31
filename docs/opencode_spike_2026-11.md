# Spike: kell inside OpenCode as a plugin (2026-10-31)

Code: [experiments/opencode_kell/](../experiments/opencode_kell/) (README there has the commands). Facts about the hooks:
[harness_facts_2026-11.md](harness_facts_2026-11.md). Nothing was deployed, published or changed on the hosted server.

**Where it stands.** After the first pass was cut short (a live `--auto` run was refused by the permission classifier), the
person allowed `run-sandbox.sh` runs with `--auto` in fixtures under `experiments/opencode_kell/fixtures/`, each under five
minutes. Under that, the six acceptance prompts and Layer 2 ran live (below). The Claude Code arm of the comparison was
**not run**: it is a different tool and was outside that permission, so there is no side-by-side cost or time.

## Step 0

| Check | Result |
|---|---|
| a. V2 plugin API in a released version | **Yes.** `@opencode/plugin`, `@opencode/cli`, `@opencode/sdk` are on npm at `2.2.5` (`latest`). The globally installed `opencode` here is 2.1.4. Pinned to 2.2.5 in `experiments/opencode_kell/package.json`. (`opencode-ai` 1.19.5 is the older line.) |
| b. `switchModel` inside the `session.prompt` hook | **Works.** The hook called `ctx.session.switchModel` to `minimax-m2.7`; the next `model.request` for the primary request and the title request both used `gc/minimax-m2.7`, and the run header read `build · minimax-m2.7` although the config default was gemma (`sandbox/plugins/switch-test`). |
| c. An existing OpenCode plugin that runs Claude Code hooks | **Candidates exist, none usable as is.** `@monks1975/opencode-agent-hooks` 1.0.1 (MIT, 2026-07-02) and `opencode-claude-hooks` 0.1.0 (MIT, 2026-02-02) both target the 1.x plugin API (`@opencode-ai/plugin` ^1.17.12 / `*`); `oh-my-opencode` is source-available (SUL-1.0), not permissive. I read the first's manifest but did not run any of them (running third-party code with auto-approve was refused). So Layer 1 is our own plugin, with DeepSeek Harness's `hooks-claude-code` as the payload reference. |

## Layer 1: the Claude Code hooks bridge

Built: `plugin/index.mjs` and `plugin/lib/`. 6 offline tests pass.

| Claude Code event | OpenCode hook | Ran in OpenCode |
|---|---|---|
| `SessionStart` | first `session.prompt` of a session | yes: kell's `ensure-server` ran |
| `UserPromptSubmit` | `session.prompt`; context added to the system text via `session.context` | yes, with a stand-in hook: the model repeated the secret word the hook added |
| `PreToolUse` | `tool.execute.before`; a deny throws | yes, with a stand-in hook: the denied `echo forbidden-thing` failed with the hook's reason and the model reported it |
| `PostToolUse` | `tool.execute.after`; extra context appended to the result | yes: the hook received `{stdout, stderr, exit_code}` for Bash with `file_path`-style fields mapped |
| `Stop` | `experimental.idle`; a block sends a synthetic message (at most twice) | the hook fired; a block was not exercised |

With kell's real hooks (`~/.claude/settings.json`) the bridge ran all of them, and `hooks.log` shows kell's triage
(`kind=repo_specific work=answer category=code_question`) and its answer path (glm-5.3-flash answered in 24 s after reading the
code, Sonnet reviewed it "good", $0.0236). Two things stopped a clean result:

- kell's installed `UserPromptSubmit` hook has `timeout: 9`; the answer takes 24 s, so the bridge killed it at 9 s and the
  session's own model answered. `KELL_OC_MIN_TIMEOUT_S` exists for this. The run that used it hung before any hook ran
  (see open problems), and the retry was refused.
- kell's server is the local one (`127.0.0.1:8765`) and was down at first; `/readyz` answered `migrations: behind` (the local
  code is ahead of its database). `/mcp` worked. Hosted use needs `kell login`, which needs a person.

### The six prompts (OpenCode 2.2.5 + bridge, real kell hooks, local kell server, `gc/gemma-4-31B-it`, one run each)

| Prompt | Expected (handoff section 4) | What happened |
|---|---|---|
| "what does `clamp` in `mathx.py` do?" | triage `work=answer`; a cheap model answers, Sonnet checks | triage was `work=answer`, but kell added nothing ("no knowledge added: aborted due to timeout" after ~25 s); the session's own model read the file and answered. 53 s. **Not as expected.** (An earlier run did get glm-5.3-flash's reviewed answer: 24 s, $0.0236.) |
| a small bug with a failing test | a cheap model fixes it in a worktree, tests pass, Sonnet reviews, report, `kell undo` | triage `work=change`; kell did **not** dispatch (no worktree, no review). The session's model fixed `mathx.py` and the tests passed (4 passed). 77 s. **Fix works, dispatch not as expected.** |
| "check that the login page still renders in a real browser" | triage `tool=browser`, `tools goal=browser N found`, a tool note | triage `tool=browser`, log shows `tools goal=browser 5 found`. The model used a built-in browser skill and asked for a URL. 116 s. **As expected for the router; whether the note reached the model was not checked.** |
| "rename foo to bar in utils.py" | no tool note, no knowledge dump, no long wait | triage `trivial_edit`; renamed correctly. 40 s, of which ~25 s was the kell lookup timing out. **Outcome right, wait not short.** |
| "thanks, looks good" | nothing added | triage `conversation`; "lookup not_needed, not dispatched"; plain reply. 33 s. **As expected.** |
| "!opus explain the architecture" | kell skips the prompt | kell triaged it as a normal question (the `!` prefix is not honoured by the hook itself; Claude Code strips it before the hook). 46 s. **Not as expected** in the first pass; fixed by stripping the CLI's quotes (second pass). |

Why the answer and dispatch paths did not fire here: every real prompt shows the same ~25 s "no knowledge added (aborted due to
timeout)", so kell's lookup against the local server (down at first, then `migrations: behind`) times out and the later
answer/dispatch steps do not run. That is a local-server problem to separate from the bridge; hosted use needs `kell login`.

### Second pass: hosted server after `kell login`, and a transcript fix (one run each, gemma session)

Two things changed: kell now ran against `https://mcp.kell.org.in/mcp` (lookup in ~3 s, no more 25 s timeouts), and the bridge writes
a Claude Code-shaped transcript (timestamped user and assistant lines) and strips the CLI's wrapping quotes from the prompt.
Without those, kell's answer path and `!model` skip could not run.

| Prompt | Wall | What happened |
|---|---|---|
| repo question | 50 s | triage `work=answer`; the answer path ran but the reviewer judged the cheap reply "bad" once (standalone check), so the session's model answered. Path works; outcome depends on the reviewer. |
| failing-test fix | 119 s | **dispatch ran**: brief, locate (`mathx.py`), goal file, then three cheap tries (minimax, glm-5.3-flash, gemma) each "no pass (failed)", then the session's model took over and fixed it; 4 tests pass; `capture-stop reported=true accepted=true`. The cheap tries failing here is unexplained (the fixture sits inside the main repo's tree and Windows worktrees are a suspect). |
| browser task | 66 s | `tools goal=browser 5 found`; the model still asked for a URL |
| rename | 77 s | triage `trivial_edit`; dispatch tried the same three cheap models, all failed, the session's model renamed correctly |
| "thanks" | 11 s | `conversation`, nothing added |
| "!opus ..." | 22 s | **kell skipped it** (no triage logged), as in Claude Code |

So with the hosted server and a proper transcript, the bridge reproduces kell's flow: triage, lookup, dispatch with fallback, tool
router, capture, and the `!` skip. What is still unlike Claude Code: no cheap-model win in these runs, and no `kell undo` check.

## Layer 2: native pieces (`plugin/native.mjs`, `KELL_OC_NATIVE=on`)

| Piece | What is written | State |
|---|---|---|
| (a) triage + model | `POST /triage` on each prompt, `switchModel` to the cheap model, or keep the session's own for a hard task or no verdict | **ran live**: triage `work=answer` and `work=change` both picked `gc/minimax-m2.7`, and the session ran on it |
| (b) escalation | `tool.execute.after` on the shell tool detects a test command and its exit; two failures on the cheap model switch to the strong one | unit-tested (counter, reset, no-op cases); **not triggered live**: the cheap model fixed the bug on the first test run |
| (c) tools | `POST /tools` for the triage's tool goal; remote https servers with no sign-in added via `mcp.transform` + `reload()` | filter unit-tested; the router's field names for "needs sign-in" are guesses; not run live |
| (d) recording | per idle: used tools and test result to `/tools/outcome`; tokens and cost are read from the last message and logged | **ran live**: record `used: shell, glob, read, edit; outcome accepted; check_kind tests; model gc/minimax-m2.7`. Tokens and cost came back empty (the `session.context` read did not return them), and **`report_model_run` is not wired** |

## Results (the requested measurement)

OpenCode + plugin arm only, one run each, on the `clamp` fixture; cost was not recorded (tokens and cost were not readable, and
the models bill per token on General Compute).

| Task | Setup | Wall time | Pass/fail |
|---|---|---|---|
| Repo question | bridge + kell hooks, gemma | 53 s | answered correctly (kell added nothing) |
| Failing-test fix | bridge + kell hooks, gemma | 77 s | tests pass (4 passed) |
| Failing-test fix | bridge + Layer 2, triage picks minimax | 45 s | tests pass (4 passed), recorded `accepted` |
| Tool task (browser) | bridge + kell hooks, gemma | 116 s | router found 5 tools; no URL to test, so no pass/fail |

**Claude Code + kell arm: not run.** These are single runs and say nothing about the comparison.

## What would need a fork

| Need | Why | Where |
|---|---|---|
| Refuse a prompt (Claude Code's block) | `session.prompt` has no failure channel (`packages/core/src/plugin/hooks.ts:23-30`) | `core/src/plugin/hooks.ts` `Failures` and the prompt trigger |
| A real "ask" decision from a hook | only the shell/permission domain asks; the tool hook can only allow or fail | `packages/plugin/src/effect/tool.ts` |
| Hook timeouts that match kell's answer path | not a fork: set `KELL_OC_MIN_TIMEOUT_S`, or raise `timeout` in the installed settings | n/a |

Nothing else in Layers 1 and 2 needed a fork. No server change is needed for the code as written; Layer 2 depends on
`/triage`, `/tools` and `/tools/outcome` as they are today.

## Open problems

1. **Cold-start hangs.** About one run in three (a fresh fixture, `KELL_OC_NATIVE=on`) produced no output for the whole 280 s
   and was killed; the retry took 36 to 45 s. Not diagnosed. It blocks any benchmark until understood.
2. **MCP tool names.** In 2.2.5 MCP tools appear to go through a code-mode `execute` tool, so hooks written for
   `mcp__stealthlab__report_result` may never match. Needs a run with an MCP server to see the real tool name.
3. **Prompt text arrives quoted.** The CLI passed the message wrapped in quotes (`"\"...\""`); the hooks see the quotes.
4. **Triage fields.** What marks a task "hard" is a guess (`isHard`). The real vocabulary should be read from `/triage` replies.
5. **Cost and tokens.** They exist per message in OpenCode (`session-message.ts:236-253`), but the plugin only reads them through
   `session.context` and logs them; sending them with `report_model_run` is not done.
6. **kell state is per Claude Code session id.** The bridge passes OpenCode's session id and a small transcript path; kell's
   Stop-hook report reads the transcript for the model and edits, so edits are missing in OpenCode.
7. **`!model` prefix and the answer/dispatch paths** (see the prompt table): a leading `!opus` should skip kell; the answer and
   dispatch steps never ran because the local lookup times out.

## Next, in order

1. `kell login` against the hosted server (no local server), then repeat the six prompts and look for the cheap-model answer and
   dispatch. 2. Find the cold-start hang. 3. Escalation live (and why the three cheap tries fail in this fixture): a fixture where the cheap model fails its first fix. 4. The Claude
   Code arm of the comparison, once someone decides to spend on it.

## Why the cheap tries failed (diagnosed 2026-10-31, from the run logs; fixture/bridge not at fault)

Every cheap try on the rename produced the right patch (`~/.stealthlab/runs/r-20261031100701-*/a1.patch`, 220 bytes, `foo`->`bar`)
and then failed verification: `check_done cmd="node --test packaging/npm/test/hook.test.mjs" exit=1 seconds=0.1`
(`events.jsonl` of all six tries). That is this repo's own test, which cannot run in a fixture. It came from
`STEALTHLAB_DISPATCH_CHECK`, set for Claude Code sessions in this repo by `.claude/settings.local.json` and inherited by the
OpenCode run; kell uses it before any detection (`packaging/npm/lib/dispatch.mjs:87` reads it into `policy.check`, `:899`
returns it first in `dispatchCheck`). Not dispatch on Windows, not the fixture's location, not the bridge.

Fixes: `fixtures/run_prompt.sh` now unsets that variable. The fixture also needs a check kell can detect:
`detectCheck` (`lib/detect_check.mjs:70-88`) only finds pytest from a config file or a `tests/` directory, so
`make_fixture.sh` now puts the tests in `tests/` with a `pytest.ini`. Re-run of the failing-test fix: dispatch passed the
project's check with `glm-5.3-flash|direct` and `gemma-4-31b-it|stealth` ("1 test already fails on the unchanged code ... judged
on new failures only"), and the fixture's 4 tests pass. Not done, as instructed: moving the fixture outside the repo and a
Claude Code run (the cause made them unnecessary; the permission also covered only fixtures under this directory). The
rename fixture has no tests, so kell correctly says "not dispatched (no check)".
Note for kell: a leaked `STEALTHLAB_DISPATCH_CHECK` silently judges every repo by one command; a log line naming it when it is
used would have made this a one-minute diagnosis.

## Third pass (2026-10-31): the hang, escalation, undo

**Cold-start hang: fixed in the bridge.** Reproduced first: 5 of 5 runs with the plugin stalled for 90 s with two log lines
("spawning process ... serve --stdio", "cli starting"), the same fixture without the plugin took 14 s, and the bare
`opencode serve --stdio` printed its URL at once. So the wait is the host loading the plugin. The plugin's `setup` awaited
`ctx.mcp.list()` before registering anything (`plugin/index.mjs`, the old line 30); asking the host for its MCP servers while
plugins are still loading never returned. It is now asked lazily on the first tool call, with a 2 s limit. After the change, 7
of 7 plain runs took 17-23 s. Caveat: one intermediate version of the fix still stalled 2 of 3 runs, and I did not isolate why
before the final edit (which also added setup timestamps), so treat "stable" as 7 runs, not a proof. `setup` now logs each hook
registration to `plugin.log` with `KELL_OC_DEBUG=1`.

**Live escalation: not triggered.** `fixtures/make_esc_fixture.sh` builds a two-file bug (rounding mode in `tax.py` and
tax-the-sum in `invoice.py`; each fix alone leaves failures). With Layer 2 on (`gc/minimax-m2.7` cheap), the cheap model fixed
both files and all 5 tests passed on its first full test run: 141 s, `record ... outcome accepted`. So there were no failed cheap
tries and no escalation; the mechanism remains unit-tested only. A harder task is needed (or a forced-weak cheap model), and the
strong rung here would be another General Compute model, not Claude.

**`kell undo` after a dispatched fix: not confirmed.** In the failing-test fixture dispatch verified two cheap fixes (runs
`r-20261031102656-*`: `state verified, winner 0, applied false`), but kell's Sonnet review step failed every time
(`review: claude-sonnet-5-5 gave no usable verdict (exit 1)`, and `review-in-repo` the same), so nothing was applied and
`kell undo --list` says "no undo points". The files were then changed by the session's own model. The reviewer is the headless
`claude` that kell starts; its exit 1 was not diagnosed because running Claude Code is outside what was allowed so far.

**Comparison (OpenCode + plugin vs Claude Code + kell): not run.** It needs Claude Code runs, and the reviewer fault above is a
Claude Code question too.

## Fourth pass (2026-10-31): the reviewer's exit 1 (diagnosis so far) and what blocks the rest

With headless `claude` runs allowed in the fixtures, the reviewer's exit 1 was checked first, as cheaply as possible:

| Test (run from a shell, in a fixture) | Result |
|---|---|
| `claude -p` with the inherited environment (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`, ... all set) | exit 0, `ok` |
| the same with those variables removed | exit 0, `ok` |
| kell's own call (`lib/claude_call.mjs` `runClaudeAsync` + `leanArgs`) with the sandbox's `XDG_*` variables | exit 0, `is_error false` |
| the reviewer's call (`readOnlyArgs`, cwd = fixture) with `CLAUDE_PROJECT_DIR`, `STEALTHLAB_*`, `KELL_OC_*` and the `XDG_*` set, with and without `CLAUDE_PROJECT_DIR` | exit 0, valid JSON verdict, no stderr |
| the environment the hook really runs with inside OpenCode (names logged by the plugin) | the same `CLAUDE*`, `STEALTHLAB_*` and `XDG_*` names as the shell |

So **the sandbox's config folders and the inherited variables do not hide the sign-in or make `claude` think it is nested**; the
reviewer call works in every variant I could build. The real stderr of the failing 10:27 calls was never logged
(`review.mjs:203,223` print only the exit code), so the cause stays unknown. Candidates not yet excluded: two `claude`
calls started at the same moment (the run showed two tries reviewed within seconds of each other), or a transient error on the
Anthropic side at that time. A one-line change to log `r.stderr` there would settle it; I left kell's code alone as instructed.

**Why the rest did not run.** Config was restored to the local server, as asked, and the local server is a poor substrate for
dispatch: its lookup times out (`no knowledge added (The operation was aborted due to timeout)`) and `/readyz` says
`migrations: behind`, so even a standalone `hook-prompt` for the failing-test fix ended with no dispatch (26 s). Dispatch,
its reviewer and `kell undo` ran only against the hosted server, which needs the OAuth sign-in that `kell logout` removed. A run
pointed at the hosted URL with the local token gets "triage gave no verdict" and does nothing.
Also: a fixture directory could not be deleted once ("Device or resource busy"), so fixtures are created under new names per run.

Not done: one dispatched fix applied, `kell undo` restoring it, and the three-task comparison against Claude Code. Needs
`kell login --url https://mcp.kell.org.in/mcp` again; after the runs, `kell logout` and the local config go back.

## Fifth pass (2026-10-31): hosted sign-in, dispatch applied, undo, and the comparison

**Reviewer exit 1: gone after the sign-in, cause never seen.** With the hosted server (OAuth sign-in) the Sonnet review worked on
every run: `review: claude-sonnet-5-5 says fixes` / `review-in-repo ... fixes`. The earlier exit-1 runs were on the same
machine, so a transient failure (or two concurrent `claude` calls) remains the likeliest cause. No fix was needed in the bridge.

**Dispatched fix applied, and `kell undo` restores it: confirmed.** Fresh fixture `d4`: minimax-m2-7 passed the project's check,
Sonnet said "fixes", "accepted by the rule", `mathx.py` was changed, 4 tests passed, and `kell undo --list` showed
`u-20261031121510-f1df3c`. `kell undo` answered "undone ... restored mathx.py"; the file was back to the bug and pytest showed
`1 failed, 3 passed`. (The OpenCode session itself then ended with `Provider request failed with HTTP 403` from General
Compute on its own gemma call, exit 1 — the fix was already in.)

**Comparison, one run each, same fixtures, both arms with kell's hooks from `~/.claude/settings.json` against the hosted server.**
Claude Code arm = headless `claude -p` (Sonnet 5.5) with Bash/Read/Edit/Write/Glob/Grep allowed. OpenCode arm = the plugin
(Layer 1; Layer 2 on for the escalation task), session model `gc/gemma-4-31B-it`.

| Task | Arm | Wall | Cost | Result |
|---|---|---|---|---|
| Repo question (`clamp`) | OpenCode + plugin | 70 s | not measured | correct answer (names the bug) |
| | Claude Code + kell | 15 s | $0.096 | correct answer (names the bug) |
| Failing-test fix | OpenCode + plugin | 71 s | not measured | pass: kell dispatch (glm-5.3-flash, Sonnet "fixes") applied the fix, 4 tests pass; the session's own call then hit HTTP 403 |
| | Claude Code + kell | 45 s | $0.106 | pass, 4 tests; the session fixed it itself in 5 turns |
| Two-file bug (escalation task) | OpenCode + plugin | 197 s | not measured | pass, 5 tests; kell dispatch (glm-5.3-flash, accepted after a Sonnet in-repo review) |
| | Claude Code + kell | 38 s | $0.109 | pass, 5 tests |

What this does and does not say:
- **Cost:** only the Claude Code arm has a number (its own `total_cost_usd`). OpenCode's per-message tokens and cost were not readable
  through the plugin, and General Compute's price per token was not applied, so the cheap-model arm has no dollar figure. No
  claim about savings can be made from this table.
- **Time:** the OpenCode arm was slower on all three (70, 71, 197 s against 15, 45, 38 s), the session model being gemma and the
  dispatch waits (lookups, tries, Sonnet reviews) adding to it.
- **Quality:** all six runs ended with passing tests (or a correct answer). One run each; no variance.
- **Escalation by the plugin: still not seen.** On the two-file task Layer 2 chose the strong model because its `/triage` call
  (which reads `~/.stealthlab/config.json`, whose URL is the stopped local server) returned an empty verdict; dispatch then solved it.
  Layer 2 should take its server from `STEALTHLAB_MCP_URL` like the hook does; not yet changed.
- Claude Code's own kell hook said it "didn't record this in the kell library" — the headless runs carry no kell workflow.

## Phase 1 (2026-10-31): the session runs on the cheap model

- **Server:** Layer 2 now takes the server from `STEALTHLAB_MCP_URL` (then `STEALTHLAB_URL`, then the saved config), as the hook does.
- **Real `/triage` fields** (three live replies): `{kind, work, confidence, provider, category, needs_retrieval, reason?, tool?}`;
  kind is `repo_specific | reusable_task | knowledge_question | trivial_edit | conversation`, work is `answer | change |
  conversation | unclear_change`. There is **no difficulty field**, and `confidence` is confidence in the label (a plain failing-test
  fix 0.42, a sweeping redesign 0.98). The guess is gone: the strong model is chosen only for no verdict or `work=unclear_change`;
  everything else starts cheap and the failed-test escalation catches a task that was too hard.
- **Mode is a setting:** plugin option `mode` or `KELL_OC_MODE`: `session` (default; Layer 2; the hook's dispatch and answer paths are
  switched off for the hooks it runs: `STEALTHLAB_DISPATCH=off`, `STEALTHLAB_ANSWERS=off`) or `dispatch` (the bridge's earlier flow,
  Layer 2 off). Dispatch mode was not re-run live after this change.
- **Live, dispatch off (local server, minimax-m2.7 cheap, gemma-4-31B-it strong):** failing-test fix 66 s, 4 tests pass; two-file bug 58 s,
  5 tests pass. Each: triage `work=change` -> `-> cheap gc/minimax-m2.7`, the session ran on minimax, ran the project's pytest, edited,
  re-ran, `record ... accepted check_kind tests`; no dispatch lines in `hooks.log`.
- **Forced escalation** (`fixtures/weak_provider.mjs`: an OpenAI-compatible endpoint that answers every turn with `python -m pytest -q`
  and never fixes anything): triage -> `cheap weak/weak-1`; `escalate after 2 failed test runs -> gc/gemma-4-31B-it`; gemma fixed both files;
  5 tests pass; 163 s; recorded on `gc/gemma-4-31B-it`.
- Still seen: about one run in four (here two of six) stalls with no output for the whole 280 s, so the earlier fix removed one cause
  of the cold-start hang, not all of them.
