# To revisit

The one list of things we chose to leave for later. Each line says what, why it waits, and where the detail is. Add new
items here (not in other docs); remove one when it is done, with the commit that did it.

## Cost and speed

- **Hard tasks save money but not time.** When no cheap model solves a hard multi-step fix, the cheap tries cost cents
  but the developer waits (17 minutes in one kell-try run). To do: skip or shorten the tries when a task looks hard and
  the outcome records say cheap models fail on that kind; start the session's model after a few minutes instead of
  after every try. Detail: [cheap_models_notes.md](cheap_models_notes.md), "To revisit" 7.
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
  Waiting on the research (the Perplexity prompt of 2026-10-31).
- **npm publish and the first real users** (Chaitanya): [handoff_claude_code_launch_2026-10-30.md](handoff_claude_code_launch_2026-10-30.md).
