# To revisit

The one list of things we chose to leave for later. Each line says what, why it waits, and where the detail is. Add new
items here (not in other docs); remove one when it is done, with the commit that did it.

## Decisions

### Open (need the owners)

- **Our agent's strong model.** Recommended: a strong open model (largest GLM / Qwen / DeepSeek) on the same General
  Compute key, so a user needs one key. An Anthropic API key is optional, and so is Claude Code's headless claude when
  installed. Never a Claude subscription sign-in inside OpenCode.
- **Our agent's reviewer** (today Sonnet through headless claude): the same choice as the strong model, configurable.
- **Our agent's default mode.** Recommended: "session" (the cheap model runs the session, with escalation on failing
  tests), with "dispatch" (the Claude Code flow) as a setting.
- **Where a pilot user's model key comes from:** (a) their own General Compute key, or (b) a kell-run organisation they
  join. Detail: [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md) section 3.
- **npm:** the first version number, and whether `"license": "UNLICENSED"` stays (Chaitanya publishes).
- **The app** (our own coding app like Claude Code): desktop (Electron/Tauri over a local OpenCode server), web, or both;
  the first screens; the website's and console's design system.
- **Neon storage:** confirm the plan's limit (the local copy of the catalog is about 0.9 GB), and whether to check it
  through Neon's account API.
- **Gateway terms review:** who reads each allowlisted server's terms.

### Made (kept here so they aren't reopened by accident)

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

## Product

- **Your own agent on an open harness** (OpenCode, Codex CLI, Qwen Code, ...), with kell built in instead of hooked in.
  Research in (Gemini and Perplexity, 2026-10-31): both rank OpenCode first (MIT, client/server with an SDK, plugins
  with hooks before and after tools, 75+ providers, reads AGENTS.md). Next: a small spike, a kell plugin for OpenCode,
  before deciding to fork; check its current plugin hooks in the repo first, since the reports disagree on details.
- **Our own design app**, in the same look as the website and the console (owner, 2026-10-31). Not started.
- **The one-line installers run kell through npx** (`install.sh` / `install.ps1` call `npx kell-mcp install`), so
  Claude Code's hooks become `npx -y kell-mcp@latest hook-prompt`: an npx start on every prompt. Make them
  `npm install -g` first. Until then [new_user_setup.md](new_user_setup.md) uses `npm install -g kell-mcp`.
- **npm publish and the first real users** (Chaitanya): [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md).
