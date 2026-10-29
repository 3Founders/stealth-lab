# The MCP console: how it opens, and how to open and close it

The console is a local web page for the kell MCP: connect clients, keep provider keys and local models, try a model's reply, and watch what the router handled. It runs on your machine, on `127.0.0.1` only.

## What starts it

Two parts, both from the `kell-mcp` package (`packaging/npm`):

- **The helper** (`kell console`, `lib/console.mjs`): a loopback HTTP server that does what a browser tab cannot (register kell with Claude Code, Cursor and Codex, keep keys, stream a model's reply). Every request needs the per-run token in the address it prints, a loopback `Host`, and an allowed `Origin`.
- **The page** (`console/`, built to `console/out`): served by the helper. A plain npm install does not include it.

## It opens by itself with Claude Code

`kell install` registers a SessionStart hook (`kell hook ensure-server`). When a Claude Code session starts it:

1. Starts a **local** MCP if the configured URL is on this machine and not answering (Windows, needs the backend checkout).
2. Checks the MCP answers. If it does not, nothing more happens.
3. Starts the console helper if one is not already running, and **opens it in your browser**.

Details worth knowing:

- It opens only on a fresh start of Claude Code, not on resume, clear or compact, so it does not pile up tabs.
- A running console is reused. The helper publishes its address (with its token) in `~/.stealthlab/console.json` while it runs; this is how a later session finds it. The file is private to you.
- The helper keeps running after the session ends. The MCP and the console are independent: closing one does not close the other.
- It needs the built page: `console/out` in the repo checkout, or the folder named in `STEALTHLAB_CONSOLE_UI`. Without it the hook writes one line to `~/.stealthlab/hooks.log` and does nothing. Build it once with `npm run build` in `console/`.

## Open, close, status

From any terminal:

```
kell console open      # start it if needed and open it in the browser
kell console close     # stop the console (the MCP keeps running)
kell console status    # say whether it is running, and its address
```

Inside Claude Code the same thing is a slash command, written by `kell install` to `~/.claude/commands/kell-console.md`:

```
/kell-console open
/kell-console close
/kell-console status
```

With no argument it shows status. It is a command file and not an MCP tool on purpose: a tool's description is in the context of every turn, while a command file costs nothing until you use it. `kell uninstall` removes it.

## Run the MCP and the console together by hand

From `console/`: `npm run mcp`. Options: `--mcp-port <n>` (default 8765, an MCP already listening there is left alone), `--console-port <n>`, `--no-mcp`, `--mcp-cmd "<command>"`, `--no-build`. Ctrl+C stops what it started.

## Switches

| Setting | Effect |
|---|---|
| `STEALTHLAB_CONSOLE_AUTOOPEN=off` | Do not start or open the console when Claude Code starts. The commands above still work. |
| `STEALTHLAB_CONSOLE_UI=<folder>` | Serve the page from this built folder instead of the repo's `console/out`. |
| `STEALTHLAB_SERVER_AUTOSTART=off` | Do not start a local MCP that is down. |
| `STEALTHLAB_HOME=<folder>` | Where `console.json`, `hooks.log` and the config live (default `~/.stealthlab`). |

## Troubleshooting

- **Nothing opened.** Read `~/.stealthlab/hooks.log`. "the MCP is not answering" means the MCP URL did not reply; "no built console page" means `console/out` is missing; "the console helper did not start" means the helper exited early (run `kell console` in a terminal to see why).
- **The address needs a token.** Use the address from `kell console status` or the one the page opened with; the token changes each time the helper starts.
- **It shows the MCP as closed.** The page is live: it reports the MCP closing and coming back. Start the MCP and it turns green.
- **Port in use.** The helper picks a free port itself; only the MCP uses a fixed one (8765).

## Limits

- Local only. It does not open a remote or hosted console.
- The opening on session start is a convenience: any failure is logged and never blocks Claude Code from starting.
- Streaming the console's replies into Chat rooms is not built yet.
