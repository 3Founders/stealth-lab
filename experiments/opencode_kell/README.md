# kell inside OpenCode, as a plugin (spike)

Status: a spike. Layer 1 (the Claude Code hooks bridge) and Layer 2 ran live in OpenCode 2.2.5; the six acceptance prompts ran
once each (answer and dispatch paths did not fire, see the doc); escalation is unit-tested only; the Claude Code arm of the
comparison was not run. See [docs/opencode_spike_2026-11.md](../../docs/opencode_spike_2026-11.md).

Pinned to `@opencode/cli`, `@opencode/plugin`, `@opencode/sdk` **2.2.5** (the `latest` on npm, 2026-10-31). This is
OpenCode's V2 plugin API (`Plugin.define`); plugins written for the older `@opencode-ai/plugin` 1.x do not apply.

## What is here

| Path | What |
|---|---|
| `plugin/index.mjs` | Layer 1. Reads the `hooks` of Claude Code settings files and runs them with the same JSON on stdin and the same stdout / exit-code rules. `UserPromptSubmit` and `SessionStart` -> `session.prompt` (context goes to the system text); `PreToolUse` -> `tool.execute.before` (deny throws, `updatedInput` rewrites); `PostToolUse` -> `tool.execute.after`; `Stop` -> `experimental.idle`. |
| `plugin/lib/` | `settings.mjs` (read and merge settings files, matchers), `run_hook.mjs` (spawn, parse, merge), `tool_names.mjs` (shell -> Bash, `path` -> `file_path`, ...). |
| `plugin/native.mjs` | Layer 2, on with `KELL_OC_NATIVE=on`: triage picks the model, escalation after two failed test runs, tool-router suggestions as remote MCP servers, a per-turn record to `/tools/outcome`. |
| `test/` | `node --test test/*.test.mjs`: 11 offline tests (no network, no OpenCode). |
| `sandbox/` | A project for `run-sandbox.sh`: General Compute as an OpenAI-compatible provider, plus the Step 0 probes (`plugins/switch-test`, `plugins/probe`) and a stand-in hook (`fake-hook.mjs`). |
| `fixtures/` | `run_prompt.sh <name> "<prompt>"` runs one prompt in `fixtures/run/<name>` (under 5 minutes). `make_fixture.sh <dir>` builds the small repo (a `clamp()` bug, one failing test); `with_opencode.mjs <dir>` writes its `opencode.json`. |
| `run-sandbox.sh` | Runs the pinned OpenCode in an isolated profile (XDG directories under `sandbox/.xdg`), so your own OpenCode is untouched. |

## Run it

```bash
cd experiments/opencode_kell
npm install                                  # installs the pinned 2.2.5 packages
node --test test/*.test.mjs                  # offline tests

# a clean fixture, with the plugin configured by absolute path
fixtures/make_fixture.sh /tmp/fx && node fixtures/with_opencode.mjs /tmp/fx

# one prompt through OpenCode with the plugin (needs GENERAL_COMPUTE_API_KEY in the environment or in backend/.env)
export KELL_OC_DEBUG=1 KELL_OC_STATE_DIR=$PWD/sandbox/.state
OC_CWD=/tmp/fx ./run-sandbox.sh run --standalone "what does clamp in mathx.py do?"
```

The key is read from `backend/.env` into that process only and never printed. Nothing here contains a key.

## Settings

| Variable | Meaning |
|---|---|
| `KELL_OC_SETTINGS` | Path list of settings files to read instead of `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`. |
| `KELL_OC_MIN_TIMEOUT_S` | Raise every hook's own timeout to at least this many seconds. kell's answer path takes 20 s or more; the installed hook says 9. |
| `KELL_OC_DEBUG=1`, `KELL_OC_STATE_DIR` | Write `plugin.log` and `native.log` there. |
| `KELL_OC_NATIVE=on` | Turn on Layer 2. |
| `KELL_OC_CHEAP_MODEL`, `KELL_OC_STRONG_MODEL` | `provider/model` for the cheap and the strong rung (defaults `gc/minimax-m2.7` and the session's own). |
| `KELL_OC_ADD_REMOTE=on` | Let Layer 2 add the router's remote MCP servers that need no sign-in. |
| `STEALTHLAB_HOME`, `STEALTHLAB_URL`, `STEALTHLAB_TOKEN` | Which kell server Layer 2 talks to (default: `~/.stealthlab/config.json`). |

## Known limits

- A blocked prompt cannot be refused (OpenCode's prompt hook has no failure channel). The bridge rewrites the prompt to
  "reply with exactly this text", which spends one model call.
- MCP tool names are matched best-effort. In this version MCP tools appear to be reached through a code-mode `execute`
  tool, so `mcp__server__tool` matchers may not fire.
- Some first starts after a config change hang for minutes; a retry then takes 10 to 40 s. The cause is not found.
