# To revisit

The one list of things we chose to leave for later. Each line says what, why it waits, and where the detail is. Add new
items here (not in other docs); remove one when it is done, with the commit that did it.

## Decisions

### Open (need the owners)

- **Where a pilot user's model key comes from:** (a) their own General Compute key, or (b) a kell-run organisation they
  join. Detail: [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md) section 3.
- **npm:** the first version number, and whether `"license": "UNLICENSED"` stays (Chaitanya publishes).
- **Neon storage:** confirm the plan's limit (the local copy of the catalog is about 0.9 GB), and whether to check it
  through Neon's account API.
- **Gateway terms review:** who reads each allowlisted server's terms.

### Made (kept here so they aren't reopened by accident)

- **2026-11-01, the desktop app's shell: Electron** (owner). OpenCode's SDK and kell's console helper are Node. Do not
  reopen; the prototype's Next.js UI goes inside Electron. Open: Windows first, or Windows and macOS together.

- **2026-10-31, our agent's models:** cheap minimax-m2.7, strong deepseek-v3.2, both on General Compute (one key). Not
  gpt-oss (owner). An Anthropic key is a later option; never a Claude subscription sign-in inside OpenCode.
- **2026-10-31, the reviewer without Claude Code:** headless claude when installed, otherwise an exec.json profile
  (`STEALTHLAB_REVIEW_FALLBACK_MODEL`, default deepseek-v3.2), in `lib/review.mjs`.
- **2026-10-31, our agent's default mode:** "session" (the cheap model runs the session, escalation after 2 failed test
  runs); "dispatch" (the Claude Code flow) is a setting.

- **2026-10-31, the harness:** OpenCode (MIT, plugin API 2.2.5 pinned), as a plugin with no fork. DeepSeek Harness was
  not chosen while it is a preview, but its design (replaceable parts) is the pattern we follow.
- **2026-10-31, the claude.ai checker:** gemma-4-31b-it checks glm-5.3-flash for now; a stronger checker later
  (Cost and speed, below).
- **2026-10-31, the listed MCP tools' token budget:** 2600 (owner: "if no quality loss").
- **2026-10-31, the gateway, phase 1:** the allowlist is a rule (live with no sign-in, verified publisher, at most 30
  servers, 20+ stars), not a hand-made list. A one-time consent link per server. Public data and read-only tools only.
  `kell/images` is free to users, 30 a day, paid by kell.
- **2026-10-31, tool outcomes:** recorded for every user; the ranking adds them all together for now.
- **2026-10-31, the one list:** everything we leave for later goes in this file.

## Our agent (OpenCode)

- **Models and providers for our agent.** Cheap minimax-m2.7 and strong deepseek-v3.2, both on General Compute (one
  key); a second provider (DeepInfra, `--strong-key-env`, default `DEEPINFRA_KEY`) moves the strong rung to its
  DeepSeek-V3.2 with gc/deepseek-v3.2 as the fallback, and the provider-failure fallback (retry once, then the
  fallback, once per turn) is in the plugin (2026-11-02, both live-verified). Remaining:
  1. Ask General Compute about the random blocks, the "Streaming request failed" 400s, and the output limit.
  2. A GC-wide outage still kills a turn even with the fallback (both providers went down in the same window on
     2026-11-02); a third provider or queueing would soften it.
- **The Part 3 fixture is too small to observe the routing of web, browser, docs and image tools.** Prompts 7, 10, 12, 19
  and 20 ended in correct "not applicable" refusals (no React project, one commit, no login page, no README, no email
  code), so those expectations could not be checked. A richer fixture (a web page, a stale README, several commits, email
  code) would make them real. Table: [opencode_spike_2026-11.md](opencode_spike_2026-11.md) Part 3.
- **The agent's shell tool inherits PowerShell 5 on Windows.** Models write bash-style `&&` chains, which PS 5 rejects.
  Mitigated and verified 2026-11-02: telling the model in the prompt that the shell is PS 5 removed the `&&` failures
  (prompt 14 re-run); the user doc should keep that tip, and a setup option to put Git Bash first on PATH remains an
  option.
- **`test/claude_exec.test.mjs` needs real Git Bash on this machine.** When `bash` on PATH is WSL's (`C:\Windows\System32\bash.exe`, as on the 2026-11-01 machine), the test "hook commands run through bash with Windows backslash paths" fails exit 127 (WSL bash cannot run a Windows binary path); it skips only when bash is missing. Install Git Bash ahead of WSL on PATH, or make the test detect and skip a WSL bash.
- **The remaining cold-start hang.** One cause is fixed (the plugin no longer waits on the MCP list at start). Not
  reproduced on 2026-11-01 (16 runs, 0 hangs, including 6 right after a config rewrite), so the cause hunt stopped; a 60 s
  no-progress watchdog in the plugin logs it if it comes back
  ([opencode_watchdog.mjs](../packaging/npm/lib/opencode/lib/watchdog.mjs)), after which the user doc's troubleshooting
  table points at OpenCode's own log.
- **General Compute's deepseek-v3.2 refuses 8k+ output tokens** (OpenCode asks 32k by default). kell's provider config caps
  it at 4k. Ask General Compute for the real limit, or whether it's a bug.
- **General Compute's deepseek-v3.2 admits OpenCode intermittently (2026-11-01).** Runs 1–3 on minimax-m2.7 passed all
  morning, while the strong model returns, in turns: the edge's HTML "Blocked" page (HTTP 403, before auth) and
  `400 "Streaming request failed"`; identical bodies and headers replayed from node fetch pass, min max up to 18 tools and
  32k `max_completion_tokens`; `Options.headers` in opencode.json does not reach the wire (OpenCode still sends its own
  `user-agent: opencode/latest/2.2.5/desktop`). Looks like its edge's bot/WAF rules or capacity throttling, not kell's.
  Owners: ask General Compute to allow OpenCode/agent UA sonsign their deepseek route, or whether admission is throttled.
  Meanwhile the plan doc's `Options.headers` field is a dead end; a local undici forwarder is a workaround, not shipped.
- **Not done from the spike plan:** the timing and cost table against Claude Code (the OpenCode leg's tables are now in
  [opencode_spike_2026-11.md](opencode_spike_2026-11.md); Part 3's OpenCode leg ran 2026-11-02, the Claude Code/claude.ai
  legs need the user's own accounts); the Anthropic-key option; renaming opencode_spike_2026-11.md to a product doc. The
  clean-profile acceptance and the live escalation with the weak-provider fixture ran on 2026-11-01 (rows in the spike doc).
- **The plugin needs npm at setup** (`@opencode/plugin` 2.2.5 is installed into `~/.stealthlab/opencode/plugin`); kell-mcp
  itself keeps no dependencies. Publishing the plugin as its own npm package would remove that step.

## Cost and speed

- **Hard tasks save money but not time.** When no cheap model solves a hard multi-step fix, the cheap tries cost cents
  but the developer waits (17 minutes in one kell-try run). To do: skip or shorten the tries when a task looks hard and
  the outcome records say cheap models fail on that kind; start the session's model after a few minutes instead of
  after every try. Detail: [cheap_models_notes.md](cheap_models_notes.md), "To revisit" 7.
- **A check that can't run fails every cheap try.** In the OpenCode spike a leaked `STEALTHLAB_DISPATCH_CHECK` pointed at a
  test that didn't exist in the checkout, so correct cheap fixes were all judged failed and the session's model did the
  work. When the check can't run (command not found, no tests collected), say so and skip the cheap tries, rather than
  failing each one. Detail: [opencode_spike_2026-11.md](opencode_spike_2026-11.md).
- **Cheap answers are slower than the session's model** (about 215 s vs about 100 s for a repository question). Lever: a
  faster inference provider for the step agents (Cerebras, Groq). Same place.
- **A stronger checker for claude.ai's `delegate`.** gemma-4-31b checks glm-5.3-flash today; switch `WEB_CHECK_MODEL`
  to a Qwen3-235B or DeepSeek class model once one is connected to the hosted call_model. Detail: cheap_models_notes.md 8.
- The other model items (spreading models across providers, adding Qwen and MiMo, per-model step budgets, minimax's place,
  gpt-oss) are in cheap_models_notes.md, "To revisit" 1-6.

## Tool routing

- **Tools in the middle of a task.** claude.ai and the OpenCode agent can call kell's tools at any point (in OpenCode the
  cheap model too). Gaps:
  1. Claude Code's default install is hooks only, so Claude can't call kell's tools (`discover_tools`, `use_tool`)
     mid-task unless it was installed with `--keep-mcp`.
  2. The cheap step agents in Claude Code's dispatch can't call MCP tools at all (tool routing P4, not built).
  3. Everywhere, the tool goal is chosen once, from the first message. A need that appears halfway through relies on the
     model calling `discover_tools` itself. A re-triage on tool failure, or a short reminder after N tool calls, would
     cover it.
- **Per-person or per-team outcome ranking.** Every tool outcome row keeps who reported it, but the ranking adds all users
  together. A team's own record could weigh more for that team. Detail: [tool_routing_plan.md](tool_routing_plan.md).
- **Official vendors with few stars rank low.** ElevenLabs' own server links a 6-star repository, so smaller tools came
  first for a voiceover. Needs a "the vendor's own server" signal. Same place.
- **Brave, Exa and Tavily do not surface for web search** (general scrapers do). Same place.

## The tool gateway (use a tool through kell without installing it)

Phase 1 is built (remote servers that need no sign-in, consent link, read-only tools, public data; plus kell's own image
generation). Waiting:

- **Phase 2: servers that need a sign-in.** kell signs in to the third-party server for the user once (OAuth, dynamic
  client registration where offered) and keeps the token, encrypted and revocable. Most image, voice and SaaS tools need
  it. Needs a security review first. Detail: [tool_gateway_design.md](tool_gateway_design.md), "Later phases".
- **Phase 3: Claude Code auto-install.** For allowlisted servers, with a one-time opt-in, kell runs `claude mcp add ...`
  itself. Package servers run third-party code on the user's machine, so they stay opt-in per server; Claude Code usually
  sees a new server in the next session.
- **Paid tools through the gateway**, passing the cost through the existing credits and caps.
- **Tools that act for the user** (send, post, pay, delete) through the gateway, with a confirmation step.
- **Terms review** of each allowlisted server's terms (owner decision: who does it).
- **More of kell's own capabilities** like `kell/images`: voice (text to speech), video, if users ask for them.
- **kell/images has no credits on its image provider (OpenRouter).** Both image prompts in Part 3 named the right tool
  and tried it, and both got back "the account has no credits available" — so no user-visible image has ever been
  produced end to end. Owner: top the account up or switch the provider (DeepInfra is already used for images elsewhere).

## Product

- **Live progress in kell's surfaces (owner, 2026-11-02): tokens, "working" state and intermediate tool calls streamed
  while the agent works.** The desktop app streams already (SSE from the engine: text deltas, tool and model chips
  mid-turn). Missing: Claude Code through kell (hooks are batch processes with no mid-turn channel — kell's triage,
  dispatch and records are invisible while they happen) and the console (records outcomes only after a turn). To look
  at after the current work order: a visible side-channel for Claude Code, and a live tail in the console.
- **Your own agent on an open harness** (OpenCode, Codex CLI, Qwen Code, ...), with kell built in instead of hooked in.
  Research in (Gemini and Perplexity, 2026-10-31): both rank OpenCode first (MIT, client/server with an SDK, plugins
  with hooks before and after tools, 75+ providers, reads AGENTS.md). Next: a small spike, a kell plugin for OpenCode,
  before deciding to fork; check its current plugin hooks in the repo first, since the reports disagree on details.
- **Our own design app**, in the same look as the website and the console (owner, 2026-10-31). Not started.
- **The one-line installers run kell through npx** (`install.sh` / `install.ps1` call `npx kell-mcp install`), so
  Claude Code's hooks become `npx -y kell-mcp@latest hook-prompt`: an npx start on every prompt. Make them
  `npm install -g` first. Until then [new_user_setup.md](new_user_setup.md) uses `npm install -g kell-mcp`.
- **npm publish and the first real users** (Chaitanya): [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md).

## Needs the owners

Steps that need a person (money, a sign-in, an account, a decision outside the repo). Nothing else blocks on them; each
is skipped in the current work and recorded here.

- **kell/images has no credits on its image provider (OpenRouter)** — top the account up or switch providers
  (DeepInfra already serves images elsewhere). Detail: "The tool gateway" above. Until then no user-visible image has
  come out of kell/images end to end.
- **The npm publish** (Chaitanya): the first version number, whether `"license": "UNLICENSED"` stays, and the push.
- **Where a pilot user's model key comes from:** their own General Compute key or a kell-run organisation. Detail:
  [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md) section 3.
- **Claude Code and claude.ai runs of the 20 Part 3 prompts** — needs those accounts; the OpenCode leg is done
  ([opencode_spike_2026-11.md](opencode_spike_2026-11.md)).
- **Code signing for the Windows installer** — without a certificate, SmartScreen warns on first install.
- **macOS build of the desktop app** — only if it needs no extra work beyond the Windows build; else it waits.
- **Asking General Compute about deepseek** — the random 403/400 blocks, the "Streaming request failed" 400s, and the
  8k+ output-token refusal (kell caps it at 4k today).
