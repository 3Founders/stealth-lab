# Delegator relay: what is built (2026-10-28)

The real relay is live on the hosted MCP server. Brief and rules: `.scratch/prompts/delegator_pull_brief.md`. Code: `backend/app/services/delegate.py`, `backend/app/mcp_server/delegate_routes.py`, tools in `backend/app/mcp_server/server.py`, table in `backend/db/155_delegate_relay.sql`, tests in `backend/tests/test_delegate_*`.

Base URL: `https://mcp.kell.org.in` (the origin of `NEXT_PUBLIC_KEL_MCP_URL`; local dev `http://127.0.0.1:8765`).

## Auth (website calls)

`Authorization: Bearer <the Supabase access token the site already holds>` (`getAccessToken()` in `lib/session.ts`). It must be a website-session token. A token issued to an MCP app (Claude, Cursor) is refused with 401, by design. Every row is scoped to the token's subject, so you only ever see your own agents and tasks.

Cross-origin: the server answers CORS preflights for `https://www.kell.org.in`, `https://kell.org.in`, `http://localhost:3000`, `http://127.0.0.1:3000` (override with `DELEGATE_ALLOWED_ORIGINS` on Railway). No cookies, no `credentials: "include"`: the bearer token is the only credential.

Errors are `{"error": "<plain sentence>"}` with 400 (bad input), 401 (sign in / bad session), 403 (inactive account), 404 (not yours or not found), 409 (duplicate / limit), 429 (too many open tasks), 503 (starting up).

## Routes

| Method and path | Body / query | Returns |
|---|---|---|
| `GET /v1/delegate/agents` | | `{agents: [Agent]}` (live ones) |
| `POST /v1/delegate/agents` | `{name, kind?}` name: `[a-z0-9][a-z0-9_.-]{0,39}`, lowercased | `201 Agent` |
| `DELETE /v1/delegate/agents/{id}` | | `{revoked, cancelled}`: revoking cancels its unfinished tasks |
| `POST /v1/delegate/messages` | `{text, room?, needs_approval?}` | `{room, tasks: [Task], unknown: [names]}`; one task per `@agent` mentioned, none when no agent is mentioned |
| `GET /v1/delegate/tasks` | `?room=#x&after=<ISO>` | `{tasks: [Task]}` newest 100, oldest first; `after` returns anything created, claimed or finished since |
| `POST /v1/delegate/tasks/{id}/approve` | | `Task` (needs_approval to queued) |
| `POST /v1/delegate/tasks/{id}/cancel` | | `Task` (any unfinished task) |
| `GET /v1/delegate/events` | `?room=#x&after=<ISO>` | Server-Sent Events stream, see below |

Mentions: `@web-chat` and `@alice/web-chat` both address the agent named `web-chat`. Rooms are only labels (`#general` by default); there is no room object to create.

### Shapes

```
Agent { id, name, kind, handle: "@name", created_at, last_seen|null, online: bool, revoked: bool }
        online = polled delegate_inbox within the last 60 s
Task  { id, agent_id, room, from, text, locators: [], depth, status, created_at, claimed_at|null, finished_at|null,
        result|null, check|null }
        status: needs_approval | queued | claimed | done | failed | cancelled
```

### Event stream

Use `fetch()` and read `response.body`; a browser `EventSource` cannot send the Authorization header.

- `event: task`, `data: Task`: the whole task each time it changes. Replace by `id`; a replay never duplicates.
- `event: agents`, `data: {agents: [Agent]}`: every ~6 s.
- `event: end`, `data: {cursor}`: the stream ends after ~50 s. Reconnect with `?after=<cursor>` to continue without a replay.
- `event: error`, `data: {error}`; then the stream closes.
- Lines starting with `:` are keepalives.

## Agent side (not the site)

MCP tools on the same server, for the person's own agent in their own session, signed in through the connector:

- `delegate_inbox(agent)`: takes the next queued task for that agent (marks the agent online), says how many are waiting and how many await the owner's approval. Found with `discover_tools(need="check my kell inbox")`.
- `delegate_result(task_id, result, ok=true, check=null)`: posts the outcome once, only for a task that was taken.

Verified live through a signed-in claude.ai connector: `delegate_inbox(agent="web-chat")` with no such agent returns "no agent named 'web-chat' on your account; add it on the Delegator page first".

## Not built yet

Pairing code and agent tokens, the local `kell agent pair` helper, agent-to-agent hops (depth is stored, always 0), a scheduled expiry job (expired rows are deleted when tasks are listed or claimed), and the whole UI. Retention default is 7 days.
