# stealthlab-mcp

Connects your coding agent to the **hosted** StealthLab MCP server
(`find_ways`, `report_discovery`, the `survey_repo` / `plan_and_run` prompts;
see `final_architecture.md`).

Nothing from StealthLab runs on your machine: no Postgres, no Python, no
backend. The installer adds one `stealthlab` entry to each agent's MCP config,
pointing at the hosted endpoint, and then exits. The only files your agent
writes locally are its own `.stealth/*.md` plan files.

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
   `prod_frontend/public/` (`scripts/sync-installers.mjs`).

## Tests

`npm test` runs offline with no dependencies. The relay test runs against an
in-process HTTP server. CI runs it on Linux, macOS and Windows with Node 18
and 22.
