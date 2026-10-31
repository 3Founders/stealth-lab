# Harness facts from source: OpenCode and DeepSeek Harness (2026-10-31)

For kell's "own agent" track ([revisit.md](revisit.md), Product). Read from shallow clones of both repos
(`.scratch/harness/`, not committed). Paths are relative to each repo; "not found" means searched, not there.
Everything here is from reading, nothing was run. Both projects move fast; re-check before building.

## OpenCode (`sst/opencode` redirects to `anomalyco/opencode`, checked with `gh api`)

| Question | Answer (file:line) |
|---|---|
| License, last commit | MIT (`LICENSE:1`, `package.json:140`). Last commit `635fb7b`, 2026-10-31. |
| Plugin registration | `Plugin.define({ id, setup(ctx) })`; setup registers hooks on `ctx.agent / mcp / model / session / tool` and may return a cleanup (`packages/plugin/src/README.md`, `src/promise/plugin.ts:31-56`). Host passes plugins in code (`packages/sdk/README.md:54-58`). |
| Hook domains | `aisdk, session, permission, shell, tool` (`packages/core/src/plugin/hooks.ts:13-19`). |
| Before a prompt | `session.prompt`: the prompt is mutable, plus `metadata` (`packages/plugin/src/effect/session.ts:12-18`). `session.context` edits `system`, `messages`, `tools` (`:35-40`). Neither can fail: only `tool.execute.before` has a failure channel (`hooks.ts:23-30`). So it can add or replace context, not block. |
| Before a tool call | `tool.execute.before`: `input` is mutable, and a `Tool.Error` rejects the call (`plugin/src/effect/tool.ts:21-27,48-52`). Block and rewrite both work. |
| After a tool call | `tool.execute.after`: `status` completed or error, `result` mutable (`tool.ts:29-45`). |
| Idle or stop | `experimental.idle`: runs when the session finishes with no pending input, not after interruption or failure; a hook may admit a synthetic message so the session keeps going (`session.ts:169-179,198`). |
| Model choice | Per agent (`packages/schema/src/agent.ts:26`), per session via `switchModel` / `switchAgent` (`session.ts:207-208`), per request via `model.request` hook that can set `baseURL` and `headers` (`session.ts:68-75`). |
| OpenAI-compatible | Per-request `baseURL` override exists (`session.ts:73`); provider packages and 75+ providers per the research note. A plain "any base URL plus key" config key: not confirmed here. |
| Sub-agents | An agent with `mode: "subagent"`, own optional `model`, `steps`, `permissions` (`schema/src/agent.ts:21-34`). Each can have its own model. |
| Headless and SDK | `OpenCode.create()`, `sessions.create`, `sessions.prompt`, streams are `AsyncIterable` (`packages/sdk/README.md:9,16,58`); server: `serve` (`packages/cli/src/commands/commands.ts:573`). |
| Tokens and cost | Per message `tokens` (input, output, reasoning, cache) and optional `cost` (`packages/schema/src/session-message.ts:236-237,252-253`; `token-usage.ts:6-14`). |
| MCP | Local and remote servers; remote has `oauth` (`packages/schema/src/mcp.ts:27,54,57`). Plugins edit servers with `mcp.transform` (`set/update/remove`) and `reload()` (`packages/plugin/src/promise/mcp.ts:6-20`). |
| Isolation | Permission rules `allow / ask / deny` per action and resource (`packages/schema/src/permission.ts:56-63`; defaults `agent.ts:44-51`); worktree provider API (`plugin/src/effect/worktree.ts`). Sandbox or container: not found. |

## DeepSeek Harness (`deepseek-ai/deepseek-harness`, command `dsh`)

| Question | Answer (file:line) |
|---|---|
| Exists, license, date, preview | Exists. MIT (`LICENSE:1`, `package.json:4`). Last commit `ce7073d5`, 2026-10-30. README calls it a "Developer preview" with "compatibility-breaking changes" (`README.md:11-13`). |
| Plugin registration | Cordis plugins ("everything is a plugin", `README.md:7`) mounted by name with config, e.g. `- name: '@deepseek-ai/dsh-hooks-claude-code'` (`packages/experimental/hooks-claude-code/README.md`, "Smallest working setup"). |
| Before a prompt | `agent/pre-step` waterfall: return `reject` or `enter(messages)` (`packages/core/agent/src/runtime-types.ts:139-146,350`). Blocks the turn or adds context messages. |
| Before a tool call | `tools/pre-execute` returns `allow / deny / cancel / ask` (`packages/core/tools/src/events.ts:18`, `contracts.ts:357-361`). Block yes; rewriting the input: not found. |
| After a tool call | `tools/post-execute` returns `accept` (can replace content), or `block` with feedback, plus `additionalContexts` (`events.ts:41`, `contracts.ts:367-370`). |
| Idle or stop | `agent/turn-stopping` (serial, at the natural stop; used for forced continuation) (`runtime-types.ts:417`, `agent-loop/src/agent.ts:396`); `agent/status` (`:309`). |
| Model choice | `agent.model.select({provider, model, effort})` before each step (`runtime-types.ts:71`, `core/agent/src/model-selection.ts:112`). The older `agent/request` override is deprecated (`runtime-types.ts:363`). |
| OpenAI-compatible | Yes: `dsh-llm-pi-ai` routes to "OpenAI-compatible gateways, or self-hosted servers" with a configurable `baseURL` (`packages/llm/llm-pi-ai/README.md` Summary; `src/adapter.ts:243`). |
| Sub-agents | In-process spawn takes an independent `agentPreset`; fork and external backends do not (`packages/subagent/subagent/README.md:65`). Backends include Claude Code, Codex and ACP (`packages/subagent/` listing). |
| Headless and SDK | `dsh headless "task"` (`docs/cli-help.md:53,100`); TypeScript and Python clients over newline-delimited JSON-RPC on stdio (`packages/sdk/README.md`). |
| Tokens and cost | `ctx.tokenMeter` measures context pressure and "prices one message" by replaying the log, with no model calls (`packages/llm/token-meter/README.md:12`): an estimate, not provider-billed cost. Billed cost: not found. |
| MCP | `stdio` and `streamable-http`, tools named `mcp__<server>__<tool>`, auto-reconnect (`packages/mcp/mcp-client/README.md:57,75,93`). OAuth: not found. Adding servers mid-session: not found (config). |
| Isolation | Sandbox modes `read-only`, `workspace-write`, `danger-full-access` (`packages/sandbox/README.md` Summary); per-call approval, unanswerable means deny (`docs/tool-execution-pipeline.md`). Worktrees: not found. |
| Notable | `dsh-hooks-claude-code` runs an existing Claude Code `hooks.json`: `UserPromptSubmit`, `PreToolUse`, `Stop` and more (`packages/experimental/hooks-claude-code/src/index.ts:222,242,275`). kell's current hooks may run there with no new code. |

## What kell can do with a plugin alone (no fork)

| | OpenCode | DeepSeek Harness |
|---|---|---|
| (a) Call our triage at prompt time and pick the model | Yes: `session.prompt` hook calls `/triage`, then `session.switchModel`. Calling `switchModel` from inside the hook is untested. | Yes: `agent/pre-step` calls triage, then `agent.model.select`. |
| (b) Escalate to a strong model when tests fail | Yes: `tool.execute.after` reads the test command's result; `switchModel` plus a synthetic message, or `experimental.idle` to keep the session going. | Yes: `tools/post-execute` sees the result; `agent.model.select` for the next step; `agent/turn-stopping` can force continuation. |
| (c) Add MCP tools for the session | Yes: `mcp.transform` + `reload()`. | Config only: mount `dsh-mcp-client` with our server. Runtime add: not found. |
| (d) Record model, tokens, cost, tools, test results per turn | Yes: tokens and cost are on each message; tool hooks give the rest. | Partly: tokens are measured, cost is an estimate (`tokenMeter`); billed cost needs a fork of the LLM adapter (`packages/llm/llm-pi-ai/src/adapter.ts`). |

Fork needed only for: DSH tool-input rewriting (`packages/core/tools/src/contracts.ts:357`, add a `rewrite` decision)
and DSH billed cost (`adapter.ts`). Nothing in OpenCode's list needed a fork.

Caveats: OpenCode's plugin API in this tree is the V2 "Promise API" (`@opencode/plugin`), so older plugin examples
online may not apply. DSH is a self-declared preview and will break compatibility.
