# stealthlab-mcp

Connects your coding agent to the **hosted** StealthLab MCP server
(`find_ways`, `report_discovery`, the `survey_repo` / `plan_and_run` prompts;
see `final_architecture.md`).

Nothing from StealthLab runs on your machine: no Postgres, no Python, no
backend. The installer adds one `stealthlab` entry to each agent's MCP config,
pointing at the hosted endpoint, and then exits. The only files your agent
writes locally are its own `.stealth/*.md` plan files. (The one exception is
opt-in: the local executor layer, `install --with-exec`, described below.)

## Install

| Platform | Command |
|---|---|
| macOS / Linux | `curl -fsSL https://<site>/install.sh \| bash` |
| Windows (PowerShell) | `irm https://<site>/install.ps1 \| iex` |
| Any OS with Node 18.17+ | `npx -y stealthlab-mcp install` |

It configures every supported agent it detects. To limit it to some agents:

```bash
npx -y stealthlab-mcp install --client claude-code --client cursor
curl -fsSL https://<site>/install.sh | bash -s -- --client claude-code
$env:STEALTHLAB_CLIENTS = "claude-code,cursor"; irm https://<site>/install.ps1 | iex
```

`STEALTHLAB_MCP_PACKAGE` pins what the scripts install (for example
`stealthlab-mcp@0.1.0`, or a local `.tgz` for testing before a release).

Other commands: `stealthlab-mcp doctor` checks that the endpoint answers
`initialize`. `stealthlab-mcp uninstall` removes the entries. `--dry-run`
shows what would change without changing anything.

## What each agent gets

| Agent | How it's registered | Anything running locally? |
|---|---|---|
| Claude Code | `claude mcp add --scope user --transport http stealthlab <url>` | no |
| Cursor | `~/.cursor/mcp.json` → `{ "url": … }` | no |
| VS Code | `code --add-mcp {"type":"http","url":…}` | no |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` → `{ "serverUrl": … }` | no |
| Codex CLI | `~/.codex/config.toml` → `[mcp_servers.stealthlab] url = …` | no |
| Claude Desktop | `claude_desktop_config.json` → the stdio relay (see below) | a small Node relay while Desktop runs |

Claude Desktop's config file only accepts local commands, so that one agent
launches `stealthlab-mcp` (with no arguments). That is a zero-dependency
stdio↔HTTP relay: it forwards JSON-RPC unchanged and handles the session ID,
protocol-version and auth headers. To avoid the relay there too, add the URL
under Desktop's **Settings → Connectors → Add custom connector** instead.

JSON configs are edited in place. Other servers and keys are kept, a `.bak`
copy is written first, and a file that doesn't parse is left untouched.

## Claude Code hooks (installed with Claude Code; `--no-hooks` skips them)

The hooks make knowledge flow both ways without the model having to decide to call a tool. They are added to
`~/.claude/settings.json` next to your own hooks, and `uninstall` removes only ours.

| Hook | When it runs | What it does | What leaves your machine |
|---|---|---|---|
| `hook-prompt` (UserPromptSubmit) | you send a task-like prompt (at least 6 words, not a slash command) | runs `find_ways` on the prompt and adds what Kel knows to Claude's context (at most 8,000 characters); nothing when Kel has nothing | the prompt (up to 1,500 characters) and `.stealth/claims.md`, to your StealthLab endpoint |
| `hook capture-tool` (PostToolUse, Bash) | Claude runs a test command (pytest, npm test, go test, cargo test, jest, vitest, …) | reads pass or fail from the runner's own summary line | nothing; kept locally as a verdict only |
| `hook capture-stop` (Stop) | Claude finishes a turn | if the prompt's lookup identified a Goal and a test verdict is known, reports one outcome per prompt with `report_model_run` (from a detached process, so you never wait) | model, `scaffold="claude-code"`, pass/fail, the Goal/Procedure ids, `check_kind="tests"`. Never the prompt, commands, test output, diffs or transcript |

- Capture is active only with a saved token (`stealthlab-mcp login`); without one nothing is recorded or queued.
  Reports that can't be sent wait in `~/.stealthlab/outbox/`.
- Locally, capture keeps only ids, verdicts and times (`~/.stealthlab/hooks/sessions/`, mode 0600, deleted after
  24 h). Failures go to `~/.stealthlab/hooks.log`, never to your session.
- Switches: `STEALTHLAB_HOOK=off` (no lookups), `STEALTHLAB_CAPTURE=off` (no outcome reports).
- Delivery mode: `STEALTHLAB_HOOK_MODE` = `full` (default: the exact way, near misses and related examples),
  `lean` (the exact way only) or `off`. `STEALTHLAB_HOOK_MODE_STRONG` overrides it for models matching
  `STEALTHLAB_HOOK_STRONG_MODELS` (default `opus|sonnet|fable`), for example `off` to skip the lookup for frontier
  models. The model is read from the session transcript, `$ANTHROPIC_MODEL` or `settings.json`. Only `full` has
  been shown to help (open-model agents on DS-1000); the other modes exist for testing and cost control.

## Endpoint and token

- **URL.** Resolved in this order: `--url`, then `$STEALTHLAB_MCP_URL`, then
  `~/.stealthlab/config.json`, then the built-in default
  (`package.json` → `stealthlab.defaultMcpUrl`). Plain `http://` is only
  allowed for loopback addresses.
- **Token.** Optional. Reads are anonymous; only `report_discovery` needs a
  signed-in user. `--token` (or `stealthlab-mcp login --token …`) saves it to
  `~/.stealthlab/config.json` (mode 0600). For the HTTP clients it is also
  written into that client's config as an `Authorization` header, because
  that's the only place they read one from.

## Opt-in: the local executor layer (`--with-exec`)

Everything above leaves nothing running on your machine. The executor layer is
the one exception, and it is **off unless you ask for it**:

```bash
npx -y stealthlab-mcp install --with-exec      # plus the usual --client / --url options
```

It adds a local MCP server, `stealthlab-mcp exec` (stdio), that hands one
well-specified step to a coding agent **you already have installed**
(OpenCode, Codex CLI, Gemini CLI, Claude Code, ...), runs it headlessly in a
throwaway git worktree, re-runs the step's own checks there, and returns a
compact result: `verified`, a summary, the diff stat. What the agent says about
its own success never sets `verified`. Nothing reaches your checkout until you
call `apply_run`, which refuses unverified runs and files that changed since the
run started. Each outcome is recorded with `report_model_run` so the recommender
learns which agent and model to pick next time (queued in
`~/.stealthlab/outbox/` when you are offline or not logged in).

**Check-and-escalate.** `achieve(..., escalate: n)` (0 to 3, default 0): when no attempt passes its checks,
the runtime tries the next untried rung of the ladder (the next agent/model in the recommender's or your
`default_order`), one at a time, each in a fresh worktree and verified by the same checks, until one verifies or
`n` further rungs have run. Cheap models do the work that passes; stronger ones are paid only when a check says
so. Every rung is reported, so the recommender learns from failures too. The delegator subagent passes
`escalate: 2`.

For Claude Code, `--with-exec` also writes (and `uninstall` removes):

| File | What it is |
|---|---|
| `~/.claude/agents/stealth-executor.md` | a subagent that does ONE `.stealth/run.md` node in its own worktree (`isolation: worktree`, tools `Read, Edit, Write, Bash, Grep, Glob`, `maxTurns: 40`) and ends its reply with a `STEALTH_RESULT` line |
| `~/.claude/agents/stealth-delegator.md` | a subagent that carries the executor MCP server **inline** (`mcpServers`), so your main conversation never loads those tools; it calls `achieve`, polls `run_result`, and returns `summary` + `verified` + `diff.stat`. `apply_run` is outside its tool list: it can never apply a change |
| `~/.claude/settings.json` → `hooks.SubagentStart`, `hooks.SubagentStop` | `stealthlab-mcp hook subagent-start` / `subagent-stop`: when any subagent that handled a plan node stops, the node's `check=` is re-run and the outcome is reported as `scaffold="claude-code-subagent"` |

The two agent files are user-level on purpose: Claude Code ignores
`mcpServers` and `hooks` in plugin agents. `$CLAUDE_CONFIG_DIR` replaces
`~/.claude` when it is set.

The hooks never slow you down: `subagent-stop` hands the check to a detached
background process and exits 0 at once (the background check has a hard 60 s
limit). Failures go to `~/.stealthlab/hooks.log`, never to your session. A
subagent that did not handle a plan node is ignored.

Without Claude Code, point any MCP client at the executor as a stdio command:
`stealthlab-mcp exec`.

### Your agents, your logins -- never shared

The executor only drives agents that **you** installed on **this** machine, with
**your own** logins and API keys, exactly as if you ran them in a terminal. It
never proxies, shares, pools or relays an agent session, login or key across
users or machines, never sends one to the hosted server, and never reads an
agent's credential files. Using it this way keeps you inside each agent's terms
of service; using it any other way is not supported.

### Security notes

- **Opt-in, reversible.** Plain `install` behaves exactly as before. `uninstall`
  removes the two agent files (only if they carry the `stealthlab-mcp:managed`
  marker -- an agent file of the same name that you wrote is never touched) and
  exactly our two hook entries; every other key and hook in `settings.json`,
  including your own `SubagentStart`/`SubagentStop`/`UserPromptSubmit` hooks,
  is left as it was. A `.bak` copy is written before each change, and a
  `settings.json` that doesn't parse is left untouched (install stops before
  writing anything).
- **Checks run verbatim.** A node's `check=` (or `achieve`'s `checks`) is a
  shell command and runs as written, in the run's worktree -- the same trust you
  give the plan that wrote it. The subagent hook runs it in the subagent's
  worktree only when that directory is a checkout of the same repository
  (`git rev-parse --git-common-dir`), otherwise in the session's directory.
  `STEALTHLAB_*` variables (your token) are removed from the check's
  environment.
- **Auto-approve only in a worktree.** Agents run with their auto-approval flags
  only inside the run's worktree, never in your checkout.
- **What leaves the machine.** Only `report_model_run` / `recommend_models`
  fields (model, scaffold, accepted, ids, step order, timings), redacted, to the
  same endpoint as everything else (no hardcoded host; plain `http://` only for
  loopback). Never task text, transcripts, diffs, check output or keys. The
  hooks keep only the node id and the session/agent ids on disk
  (`~/.stealthlab/hooks/`, mode 0600) -- never the prompt or the reply.

## Releasing

1. Set the hosted URL in **three** places: `package.json`
   (`stealthlab.defaultMcpUrl`), `install/install.sh` (`DEFAULT_URL`) and
   `install/install.ps1` (`$DefaultUrl`). `prepublishOnly` refuses to publish
   while they're empty or don't match.
2. Bump `version`, then push the tag `stealthlab-mcp-v<version>`.
   `.github/workflows/publish-mcp-client.yml` publishes to npm (this needs
   the `NPM_TOKEN` secret).
3. The site serves the installers at `/install.sh` and `/install.ps1`. The
   `prod_frontend` pre-build step copies them from `install/` into
   `prod_frontend/public/` (`scripts/sync-installers.mjs`), and writes
   `NEXT_PUBLIC_KEL_MCP_URL` into them as the default endpoint when it is set.

## Tests

`npm test` runs offline with no dependencies. The relay test runs against an
in-process HTTP server. CI runs it on Linux, macOS and Windows with Node 18
and 22.
