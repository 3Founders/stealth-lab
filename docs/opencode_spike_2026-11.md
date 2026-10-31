# Spike: kell inside OpenCode as a plugin (2026-10-31)

Code: [experiments/opencode_kell/](../experiments/opencode_kell/) (README there has the commands). Facts about the hooks:
[harness_facts_2026-11.md](harness_facts_2026-11.md). Nothing was deployed, published or changed on the hosted server.

**Where it stopped.** The comparison runs (Claude Code against OpenCode, three tasks) and the six acceptance prompts were
**not completed**, and Layer 2 was **not run against a live model**. A live OpenCode run with `--auto` (it auto-approves tool
permissions) was refused by the session's permission classifier after earlier ones had been allowed; I did not work around it.
Everything marked "ran" below ran; everything marked "not run" is written and, where stated, unit-tested only.

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

Six prompts from `handoff_claude_code_launch_2026-10-30.md` section 4: **not run**, except the first (a repo question), which
ran twice without the answer path and once with it killed at 9 s.

## Layer 2: native pieces (`plugin/native.mjs`, `KELL_OC_NATIVE=on`)

| Piece | What is written | State |
|---|---|---|
| (a) triage + model | `POST /triage` on each prompt, `switchModel` to the cheap model, or keep the session's own for a hard task or no verdict | unit-tested (model choice); `switchModel` in the prompt hook proven in Step 0 b; not run with the real triage |
| (b) escalation | `tool.execute.after` on the shell tool detects a test command and its exit; two failures on the cheap model switch to the strong one | unit-tested (counter, reset, no-op cases); not run live |
| (c) tools | `POST /tools` for the triage's tool goal; remote https servers with no sign-in added via `mcp.transform` + `reload()` | filter unit-tested; the router's field names for "needs sign-in" are guesses; not run live |
| (d) recording | per idle: used tools and test result to `/tools/outcome`; tokens and cost are read from the last message and logged | the POST is written; **`report_model_run` is not wired**, and tokens/cost go to the log only |

## Results table (the requested measurement)

Not collected. The three tasks (repo question, failing-test fix, tool task) were not run on either arm, so there is no cost,
wall time or pass/fail to report. The only timings seen, from the repo question on the fixture with kell's answer path off:
37 s and 16 s with `gc/gemma-4-31B-it`; with the answer path on and the hook killed at 9 s, 60 s. These are one run each and say
nothing about the comparison.

## What would need a fork

| Need | Why | Where |
|---|---|---|
| Refuse a prompt (Claude Code's block) | `session.prompt` has no failure channel (`packages/core/src/plugin/hooks.ts:23-30`) | `core/src/plugin/hooks.ts` `Failures` and the prompt trigger |
| A real "ask" decision from a hook | only the shell/permission domain asks; the tool hook can only allow or fail | `packages/plugin/src/effect/tool.ts` |
| Hook timeouts that match kell's answer path | not a fork: set `KELL_OC_MIN_TIMEOUT_S`, or raise `timeout` in the installed settings | n/a |

Nothing else in Layers 1 and 2 needed a fork. No server change is needed for the code as written; Layer 2 depends on
`/triage`, `/tools` and `/tools/outcome` as they are today.

## Open problems

1. **Cold-start hangs.** Several first runs after a config or environment change hung for more than 150 s with no output; the
   retry then took 10 to 40 s. Not diagnosed. It blocks any benchmark until understood.
2. **MCP tool names.** In 2.2.5 MCP tools appear to go through a code-mode `execute` tool, so hooks written for
   `mcp__stealthlab__report_result` may never match. Needs a run with an MCP server to see the real tool name.
3. **Prompt text arrives quoted.** The CLI passed the message wrapped in quotes (`"\"...\""`); the hooks see the quotes.
4. **Triage fields.** What marks a task "hard" is a guess (`isHard`). The real vocabulary should be read from `/triage` replies.
5. **Cost and tokens.** They exist per message in OpenCode (`session-message.ts:236-253`), but the plugin only reads them through
   `session.context` and logs them; sending them with `report_model_run` is not done.
6. **kell state is per Claude Code session id.** The bridge passes OpenCode's session id and a small transcript path; kell's
   Stop-hook report reads the transcript for the model and edits, so edits are missing in OpenCode.
7. **The classifier stop.** Re-running with `--auto` needs the person to allow it (or to run the sandbox themselves with
   `experiments/opencode_kell/run-sandbox.sh`).

## Next, in order

1. Run `run-sandbox.sh` with the real hooks after `kell login` against the hosted server (no local server), with
   `KELL_OC_MIN_TIMEOUT_S=90`, and fill the six-prompt table.
2. Find the cold-start hang.
3. Then Layer 2 on the failing-test fixture (`fixtures/make_fixture.sh`), and the three-task comparison against Claude Code.
