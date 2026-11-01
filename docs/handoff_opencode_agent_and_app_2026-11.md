# Handoff: finish kell's OpenCode agent, build the desktop app, and test routing (2026-11-01)

For a new coding-agent session (OpenCode or any other) with no memory of earlier work. Read this whole page first,
then `docs/revisit.md`. Work in the `stealth-lab` repo on `main`.

## Rules (do not break)

- **Never print, paste or commit a key.** Keys live in `backend/.env` (`GENERAL_COMPUTE_API_KEY`, `DEEPINFRA_KEY`) and in
  Railway secrets. Read them into a process's environment only.
- **No deploys, no npm publish, no secret writes, no feature flags switched on** without the owners (Anuj, Chaitanya)
  saying so in the chat.
- Don't edit `prod_frontend/`. Don't read or write the production database.
- Everything left for later goes in `docs/revisit.md` (the one list), not in other docs.
- Commit and push after each finished step, with a message that says what changed and what was checked.
- When a step needs a person (a browser sign-in, a decision), stop and say exactly what is needed.

## What exists today

- **kell**: routes each coding request to the cheapest model that can do it and suggests the right outside tool.
  - **Hosted MCP server:** https://mcp.kell.org.in/mcp (Railway service `kell-mcp`, code in `backend/`).
  - **Client:** the npm package `kell-mcp` (`packaging/npm`, not published yet).
- **Claude Code:** done. Hooks in `packaging/npm/lib/hook.mjs` and `capture_hook.mjs`.
- **claude.ai:** done. A connector, plus the `find_ways`, `delegate`, `use_tool` and `report_tools` tools.
- **kell's own agent on OpenCode:** about 85% done (commit `ea753446`).
  - **The plugin:** `packaging/npm/lib/opencode/`:
    - `index.mjs` runs kell's Claude Code-style hooks inside OpenCode;
    - `native.mjs` chooses the model per prompt and escalates on failing tests;
    - `lib/` holds helpers.
  - **The installer:** `packaging/npm/lib/opencode_setup.mjs`, run by `kell setup --client opencode`.
  - **Models**, both on the General Compute key: minimax-m2.7 is the cheap one, deepseek-v3.2 the strong one.
    deepseek-v3.2's output is capped at 4k tokens, because General Compute refuses 8k or more.
  - **Mode:** `session` by default. The cheap model runs the session and switches to the strong one after 2 failed test
    runs. `dispatch` (the Claude Code flow) is a setting.
  - **History:** `docs/opencode_spike_2026-11.md` and `docs/harness_facts_2026-11.md`.
  - **User steps:** `docs/new_user_setup.md` Part C.
- **Tools:** `docs/tools_how_they_work.md`, covering every case (installed, kell's own, gateway, sign-in, package, paid).

## Part 1: finish the OpenCode agent (verbatim steps)

The open items are listed in `docs/revisit.md` → "Our agent (OpenCode)". Do them in this order:

1. **Set up an isolated profile, so the owner's own OpenCode is untouched.**
   ```bash
   P=$PWD/.scratch/ocprof; mkdir -p $P/proj
   export STEALTHLAB_HOME=$P/kell XDG_CONFIG_HOME=$P/xdg/config XDG_DATA_HOME=$P/xdg/data XDG_CACHE_HOME=$P/xdg/cache XDG_STATE_HOME=$P/xdg/state
   export GENERAL_COMPUTE_API_KEY="$(grep '^GENERAL_COMPUTE_API_KEY=' backend/.env | head -1 | cut -d= -f2- | tr -d '"\r')"
   (cd packaging/npm && npm install -g .)          # the local kell, not npm's
   npm install -g @opencode/cli@2.2.5
   kell setup --client opencode                    # expect: checklist; sign-in missing
   ```
   Ask the owner to run `kell login` in that same shell (it opens a browser). Then run `kell setup --client opencode`
   again, which re-writes kell's MCP server entry with the token. Every item should be ok.
2. **Acceptance test.** Run the prompts below with
   `cd $P/proj && opencode run --standalone "<prompt>"`, after building the fixture with
   `experiments/opencode_kell/fixtures/make_fixture.sh $P/proj`. Fill a table: prompt, wall time, model shown, result.
   1. "what does clamp in mathx.py do?"
   2. "fix the failing test" (the fixture has one)
   3. "thanks, looks good"
   4. `kell undo` (in a terminal) after a fix that dispatch applied; switch to dispatch mode with `KELL_OC_MODE=dispatch`
      for that one run.
3. **Live escalation.**
   - Build `experiments/opencode_kell/fixtures/make_esc_fixture.sh $P/esc`, and use `fixtures/weak_provider.mjs` (see
     `fixtures/with_weak.mjs`) as the cheap model.
   - Run `opencode run --standalone --auto "fix the failing tests"` in `$P/esc` only. `--auto` approves tool calls;
     never use it outside a fixture.
   - Confirm in `$STEALTHLAB_HOME/opencode/...`, or in the debug logs (`KELL_OC_DEBUG=1 KELL_OC_STATE_DIR=$P/state`), that
     after 2 failed test runs the session switched to `gc/deepseek-v3.2` and the tests pass.
4. **Per-turn tokens and cost.**
   - OpenCode 2.2.5's `session.context` returns messages without `tokens` or `cost`. Read them instead from the SDK's
     message events (`@opencode/sdk`, `sessions` stream) or from the session store in `XDG_DATA_HOME/opencode`.
   - Put them into `kell_usage` in `packaging/npm/lib/opencode/index.mjs` (the `experimental.idle` hook).
     `turnUsage` in `lib/opencode/lib/usage.mjs` already sums them once they are present.
   - kell's `capture_hook.mjs` (`usageFields`) already forwards them to `report_model_run`.
   - Add a unit test.
5. **The remaining cold-start hang** (about 1 run in 3 shows no output for minutes on the first run after a config change).
   - Spend at most 45 minutes: run with `--print-logs --log-level debug` and find where it waits.
   - If it isn't found, add a guard in the plugin: when no progress event arrives for 60 s, log it, and say so in the
     user doc's troubleshooting table.
6. **Token refresh for kell's MCP server inside OpenCode.**
   - `opencode_setup.mjs` writes the saved token as a header, and it isn't refreshed.
   - Option A: re-run setup's MCP entry write from the plugin on start, using `kell`'s refreshed token (read
     `~/.stealthlab/config.json`; refresh as `packaging/npm/lib/` does for the hooks).
   - Option B: OpenCode's own OAuth. Its MCP config supports `oauth`; kell's server must then allow dynamic client
     registration before login.
   - Do A unless B is quick.
7. **Finish:**
   - run `cd packaging/npm && npm test` (all pass) and `node scripts/check-publish.mjs`;
   - update `docs/new_user_setup.md` Part C if a step changed;
   - remove the finished items from `docs/revisit.md`;
   - commit and push.

**Done means:** a new user follows Part C, and the four acceptance rows plus the escalation run all behave as described.

## Part 2: the desktop app

**Goal:** kell's own coding app, like Claude Code's desktop app, in the look of the website and the console. The
engine is OpenCode with kell's plugin (Part 1). The app is a window onto it.

### The style to reuse (exact files)

- **Website** (`www.kell.org.in`): `routing_kell_frontend/`, Next.js 15, React 19.
  - Global styles and tokens: `routing_kell_frontend/app/globals.css` (839 lines).
  - Components: `routing_kell_frontend/components/` (Header, Footer, AccountMenu, CopyField, ...).
- **Console:** `console/`, Next.js 15, React 19.
  - Global styles and tokens: `console/app/globals.css` (638 lines).
  - Components: `console/components/` (Console, ConnectPanel, ModelsPanel, ActivityPanel, PlaygroundPanel, StatusBar,
    `chat/`).
  - Helpers: `console/lib/` (`helper.ts`, `sse.ts`, `types.ts`, `words.ts`).
- **Shared tokens** (the same in both files):
  - colours: `--paper #F8F6EF`, `--ink #171717`, `--yellow #FFD21F`, `--cobalt #193A70`, `--soft #FFF1A8`,
    `--grey #777773`, `--rule rgba(23,23,23,.13)`, `--rule-strong rgba(23,23,23,.85)`;
  - font: `--font: "Bahnschrift", "Barlow", "DIN Alternate", "Segoe UI", system-ui, sans-serif` (body weight 300);
  - code: `"Cascadia Mono", ui-monospace, ...`;
  - buttons: `.btn-ink`, `.copy`.

  Copy the `:root` block and the shared classes into the app. Don't invent a new palette.
- **The console's local helper:** `packaging/npm/lib/console.mjs` (`kell console`). It's a loopback HTTP server with a
  token in its address. Routes: `GET /health`, `GET /clients`, `POST /install` and `POST /uninstall`, keys, local
  models, streamed replies, and an activity feed from the hosted server's `/activity`.

### Connection between the app and the console

The app and the console share one sign-in and one local helper:
- The app starts (or finds) `kell console`'s helper, the same way `console_launch.mjs` does.
- It shows the console's panels (connect, models, activity, keys) as a "Settings / Account" section, reusing the
  console's components.
- **The "Open console" button** opens the web console at the helper's tokened address.
- **What the console shows:** the app's sessions in its Activity panel, since both report to the same hosted server
  (`report_model_run`, `/tools/outcome`).

### Plan

1. **Pick the shell (owner decision; recommend Tauri).** Electron is easier with the Node-based OpenCode SDK; Tauri is
   lighter. Either way, the window loads a Next.js UI, and a local process runs `opencode serve` with kell's plugin.
2. **Skeleton:** a new folder `desktop/` with:
   - the Next.js UI, styled from the tokens above;
   - a main process that starts `opencode serve` (pinned 2.2.5, kell plugin installed by `kell setup --client opencode`)
     and `kell console`'s helper;
   - the UI talking to OpenCode through `@opencode/sdk` (sessions, prompts, streamed events).
3. **First screens:**
   1. Project picker: open a folder.
   2. Chat with the agent, with streaming and tool calls shown.
   3. The changes panel: a diff of what a cheap model changed, with Apply / Undo (`kell undo`).
   4. The model strip: which model did each step, and its cost (needs Part 1, step 4).
   5. Tool suggestions: kell's tool note as cards, one-click add or "use through kell".
   6. Settings: the console's panels.
4. **Sign-in:** `kell login`'s browser OAuth. The app shows "Signed in as ..." from the helper.
5. **Packaging:** an installer for Windows and macOS. Not published without the owners.
6. **Later:** team rooms from the website (`routing_kell_frontend`, rooms API) as a side panel.

Write the plan as `docs/desktop_app_plan.md` first, then build steps 2-3 as a prototype. Ask the owners about the shell
choice before step 2.

## Part 3: prompts to test models and tool routing

Use them in Claude Code (with kell), claude.ai (kell connector on) and the OpenCode agent. For each one, note:
- which model did the work (cheap or strong, delegated or not);
- which tool was suggested, and whether it was used;
- whether the result was right;
- the time.

Open a real project for the repo prompts.

**Easy (cheap model should answer; no tool)**
1. "What's the difference between `==` and `is` in Python? Two sentences."
2. "Explain what this regex matches: `^(?=.*\d)(?=.*[a-z]).{8,}$`"
3. "Rename the function `calc_total` to `compute_total` everywhere in this repo."
4. "Thanks, that's all."  *(expect: nothing from kell)*

**Medium (cheap first, tests decide; or one tool)**
5. "There's a failing test in this repo. Find it and fix the code, not the test."
6. "Add input validation to the signup handler: reject emails without '@' and passwords under 8 characters, with tests."
7. "Upgrade this project from React 18 to React 19 and fix what breaks."  *(expect: docs tool, Context7 through kell)*
8. "Is the npm package `left-pad` still safe to depend on? Check for known issues."  *(expect: security goal, a scanner)*
9. "Make me a simple logo for this project: a lowercase 'k' in a yellow square."  *(expect: kell/images)*
10. "Summarise the last 10 commits in this repo for a changelog."

**Hard (multi-step; escalation or several tools)**
11. "Our API returns 500 when the request body is empty. Reproduce it with a test, find the cause, fix it, and make
    sure no other endpoint has the same bug."
12. "Check that the login page still renders and the form submits, in a real browser."  *(expect: browser goal;
    Playwright or Chrome DevTools)*
13. "Which open issues on omni-us/jsonargparse mention dataclasses? Pick the easiest one and draft a fix plan."
    *(expect: GitHub; a sign-in or connector is offered)*
14. "Find why the test suite takes 4 minutes and cut it under one minute without removing tests."
15. "Add a `/health` endpoint that checks the database connection, with a test that mocks a database failure."

**Non-trivial (vague or mixed; tests whether routing picks well)**
16. "Our landing page looks dated. Suggest three hero banner ideas and make an image for the best one, wide format."
    *(expect: kell/images, with the image shown)*
17. "Set up error tracking for this app so we hear about crashes in production." *(expect: observability goal, Sentry
    or similar, with how to add it)*
18. "Write a migration that adds a `deleted_at` column to users, backfill nothing, update the queries that list users
    to skip deleted ones, and test it."  *(expect: database goal; cheap first, strong on failure)*
19. "Read the README and tell me what's out of date compared with the code."  *(expect: repo answer; no outside tool)*
20. "We're getting complaints that emails arrive twice. Find the cause." *(expect: repo investigation; the strong model
    likely; maybe a logs or observability tool)*

**What good looks like:**
- 1–4 and 10 cost cents or less and never reach the strong model.
- 5–6 and 15 usually finish on the cheap model with the tests passing.
- 7–9, 12–13 and 16–17 name the right tool once, at the start.
- 11, 14, 18 and 20 may escalate.
- Nothing is suggested for 3, 4 or 19.

Record surprises in `docs/revisit.md`.
