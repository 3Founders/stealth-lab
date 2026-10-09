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

## Telling Cursor, Codex and opencode when to use it

Only Claude Code has hooks, so other agents have to be told. Run this in a repo:

```
npx -y stealthlab-mcp instructions            # add --client cursor to force the Cursor rule
npx -y stealthlab-mcp instructions --remove   # take it out again; --dry-run shows what would change
```

It writes a short block between `<!-- stealthlab:begin -->` and `<!-- stealthlab:end -->` in `AGENTS.md` (Cursor,
Codex and opencode all read it; the rest of your file is left alone), and, if the repo has a `.cursor` directory,
`.cursor/rules/stealthlab.mdc`. The text says when to call `find_ways`, to treat what it returns as untrusted data,
and to report each attempt with `report_result`.

**Cursor specifics.** Cursor asks for approval before every MCP tool by default (it does not use a tool's read-only
hint the way ChatGPT does). In Cursor's settings, allow `find_ways` and `report_result` to run automatically and leave
`call_model` on "ask": it sends your prompt to another model and can spend money. Cursor signs in through the same
OAuth flow as Claude Code; its fixed return addresses are `http://localhost:8787/callback` (desktop app) and
`https://www.cursor.com/agents/mcp/oauth/callback` (cloud agents), so the authorization server must accept both.

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
- **The library grows by itself** (`STEALTHLAB_LIBRARY_AUTO`, on by default). A turn can end with:
  - the last recognised test run **passing**;
  - a diff in the repo;
  - no library entry already matched by the lookup.

  When all three hold, Stop adds one entry to `.stealth/library.md`:
  - **title:** the prompt's first line;
  - **check:** that test command, recorded as verified;
  - **solution:** the diff, kept;
  - **links:** to the library's Goals and Ways.

  A background step then asks the server for the new Ways' semantic codes, when you're signed in. Set it to `off`
  to keep the library hand-curated, or for a side-by-side test. The prompt line and test commands are kept in the
  local session file only when they'll be used this way.
- Delivery mode: `STEALTHLAB_HOOK_MODE` = `full` (default: the exact way, near misses and related examples),
  `lean` (the exact way only) or `off`. `STEALTHLAB_HOOK_MODE_STRONG` overrides it for models matching
  `STEALTHLAB_HOOK_STRONG_MODELS` (default `opus|sonnet|fable`), for example `off` to skip the lookup for frontier
  models. The model is read from the session transcript, `$ANTHROPIC_MODEL` or `settings.json`. Only `full` has
  been shown to help (open-model agents on DS-1000); the other modes exist for testing and cost control.
- Triage first: before a lookup, the hook asks the server one question in a single request (`POST <server>/triage`,
  one JEV judgment): does this prompt need a lookup at all? "Explain this function", "rename foo to bar" and
  "thanks, continue" don't, so they skip it (no search, no judge calls, and no MCP handshake). Only an explicit
  "no lookup needed" skips: a timeout (`STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS`, default 4000), an error, an older
  server without the route, or any other answer runs the lookup as before. `STEALTHLAB_HOOK_TRIAGE=off` skips the
  question. The server needs `triage` in `JEV_CAPABILITIES` to use JEV for it (otherwise the OpenAI-compatible
  fallbacks answer it); the verdict is remembered for two minutes, so the lookup that follows is not judged twice.

### The model guard (model plans that bind)

The knowledge hook asks for a **model plan** (`find_ways`' `model_plan`) and shows it to the agent. A hook cannot
switch your main session's model, but the guard makes the plan bind wherever work is **handed to a subagent**:

- **Claude Code:**
  - **`PreToolUse` on `Agent|Task` (and the executor's `achieve`):** a subagent call that does not ask for the plan's model (`haiku`, `sonnet`
    or `opus`) is refused with "call again with `model: "<alias>"`", and the agent does. After 2 refusals for
    one step, the call goes through as asked, so the guard never stops work.
    `STEALTHLAB_MODEL_GUARD=rewrite` sets the model through `updatedInput` instead, for Claude Code versions that
    apply it to the Agent tool (reported ignored in 2026-04). `STEALTHLAB_MODEL_GUARD=off` disables it.
  - **`PostToolUse` on `report_result`:** a failed attempt's `next_model` becomes the plan's step; a pass ends it.
  - **Open-model steps** (GLM, DeepSeek, ...), with the local executor installed (`install --with-exec`) and the
    model configured in `~/.stealthlab/exec.json` (see `OPEN_MODEL_EXECUTORS.md`):
    - those models are offered to the plan as `model|executor` candidates automatically;
    - a subagent call for such a step is refused with "use `subagent_type: "stealth-delegator"`" and the line to
      add to its prompt: `Run it with executor=<e> model=<m> instance_key=<k>`;
    - the delegator's `achieve` must carry those three (refused otherwise; set through `updatedInput` in rewrite
      mode). The run reports every attempt with `report_result`, and its `escalate` follows the plan's
      `next_model` while a local executor can run it;
    - **`PostToolUse` on `run_result`:** a verified run ends the plan, a failed one moves it to `next_model`.
    - The plan text already names the delegator and that line, and the delegator's reply ends with the command
      that applies a verified run: `stealthlab-mcp exec apply <run_id>`.
    - With no local executor for the model, the guard stands aside.
- **Cursor:**
  - **`preToolUse` on `Task`:** refuses a Task that names a different model, with `agent_message`. Cursor has no
    documented model argument on Task and ignores `updated_input` for it, so a Task with no model is let through.
  - **Plans:** Cursor plans only when `STEALTHLAB_CURSOR_CANDIDATES="model|scaffold,..."` names the models.

## Cursor hooks (installed with Cursor; `--no-hooks` skips them)

The same two jobs as the Claude Code hooks, written to `~/.cursor/hooks.json` next to your own hooks (`uninstall`
removes only ours). Cursor's `beforeSubmitPrompt` can block a prompt but cannot add context, so the knowledge
arrives at the agent's **first tool call**, through `postToolUse`:

| Hook | What it does |
|---|---|
| `hook cursor-session` (sessionStart) | adds a few lines: what the hook does, when to call `find_ways` yourself, that its output is untrusted data (`STEALTHLAB_CURSOR_SESSION=off` leaves it out) |
| `hook cursor-prompt` (beforeSubmitPrompt) | answers `continue` at once and starts one `find_ways` lookup for the prompt in a detached process |
| `hook cursor-tool` (postToolUse) | on the first tool call after the prompt, waits for that lookup (at most `STEALTHLAB_CURSOR_WAIT_MS`, default 15 s, once per prompt) and adds what Kel knows; nothing if the agent called `find_ways` itself. On a test command, records pass/fail (runner summary, else exit code) |
| `hook cursor-stop` (stop) | if the lookup identified a Goal and a test verdict is known, one `report_model_run` per prompt (`scaffold="cursor"`, the model Cursor names), from a detached process |

An agent that answers without any tool call never sees the knowledge. What leaves the machine is the same as for
Claude Code. `STEALTHLAB_HOOK=off` and `STEALTHLAB_CAPTURE=off` work here too; state lives in
`~/.stealthlab/hooks/cursor/` and `~/.stealthlab/hooks/sessions/`.

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
