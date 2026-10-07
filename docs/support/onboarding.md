# Onboarding and support

**Not yet usable by customers.** The service is not deployed, so there is no hosted URL. Below, `<url>` is where it will be.
The steps marked *tested* were run against a local server on 2026-10-06; the rest follow each tool's documentation and have
not been run end to end.

## Before you connect

1. Your organisation's admin sets the organisation's **policy** (allowed providers, models, tools and data classes, budgets)
   in the admin console. Until a policy exists, `call_model` is refused; `find_ways` does not need one.
2. Sign in when your tool opens the browser. The consent page names the app and the address it returns to; allow it only
   if you just started the connection.

## Connect your tool

| Tool | How |
|---|---|
| Claude Code | `claude mcp add --transport http stealthlab <url>/mcp`, then `/mcp` to sign in. Optional: `npx -y stealthlab-mcp install --client claude-code` also adds the knowledge hook. |
| Cursor | `~/.cursor/mcp.json`: `{"mcpServers":{"stealthlab":{"url":"<url>/mcp"}}}`. In settings, auto-run `find_ways` and `report_result`; leave `call_model` on "ask" because it sends your prompt to another model and can spend money. |
| Codex CLI | `~/.codex/config.toml`: `[mcp_servers.stealthlab]` with `url = "<url>/mcp"`. |
| opencode | `opencode.json`: `"mcp": {"stealthlab": {"type": "remote", "url": "<url>/mcp", "enabled": true}}`. |

Then, in each repository, tell the agent when to use it: `npx -y stealthlab-mcp instructions` (writes a marked block into
`AGENTS.md`, and `.cursor/rules/stealthlab.mdc` for Cursor repos; `--remove` takes it out).

## Local single-user trial (tested)

From `backend/`: `DEPLOYMENT_MODE=single_user python -m uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765 --workers 1`.
Use the token from `backend/.env` (`STEALTHLAB_MCP_TOKEN`) as a bearer header. Do not override that variable with a different value: the
server refuses to start if the environment and `.env` disagree. Note that `DEPLOYMENT_MODE=shared` turns the shared token off.

## When something fails

| You see | Meaning | Do |
|---|---|---|
| `401 invalid_token` | wrong or missing token, or the server is in `shared` mode and wants a real sign-in | sign in through the browser flow; for a local trial use `single_user` |
| `REFUSED: this organization has no policy configured` | an admin has not set one | ask an admin |
| `REFUSED: ... is not on this organization's allowlist` | the provider, model, tool or data class is not allowed | ask an admin to add it, or use another model |
| `REFUSED: ... budget` | the call could take you or the organisation past a budget | lower `max_tokens`, wait for the period to reset, or ask an admin |
| `REFUSED: this organization is stopped by its admins: ...` | the kill switch is on | wait for the admin to resume |
| `REFUSED: no connection available to you offers ...` | no connected provider offers that model for you | check the model name; ask an admin to connect one |
| `no_match` from `find_ways` | nothing known for that task, or search is degraded (look for `degraded_reasons`) | proceed without it; report what worked with `report_discovery` |
| a long delay on the first request | server warm-up | retry once |

When you report a problem, send: the tool and its version, the time (with timezone), the exact message, and the
`instance_key` if there was one. Do not send prompts, code or keys.

## Support process (to be filled in)

Contact address, hours, response times per severity, and the escalation path need business decisions and are intentionally
left empty rather than invented. Incident handling for security issues follows `docs/security/vulnerability_policy.md`.
