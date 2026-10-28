# Delegator: delegate work to a person's own agents (pull design)

Brief for the Frontend session. Written 2026-10-28. Read it whole before building; section 2 is the part that keeps us on the right side of the providers' terms.

## 1. What we want

A signed-in person opens Delegator on the site and sends work to the agents THEY already run: their Claude Code, opencode and others, on their own machine, signed in with their own login or key. The agents do the work in their own sessions and send results back to the room. The site and the kell server only carry task text and results.

Today Delegator (`routing_kell_frontend/app/delegator`, copy in `console/`) is a sandbox: an in-memory relay in the tab and mock agents that answer with a canned line. No network calls. This brief replaces the mock with a real relay and a real "connect an agent" flow.

## 1b. No fake server (decision, 2026-10-28)

The founder wants this built against a REAL server, not a fake one. So:
- The relay is a real service with real storage, built in this same push (section 5a). It is not a mock the UI waits on.
- The UI is written against that real API from day one. The in-tab Broker and mock agents are removed from the shipped path. A small in-memory test double is allowed ONLY inside unit tests, never as a dev mode, a fallback or a demo.
- Every acceptance check in sections 7 and 6b runs against a real running relay and, for BYOK, a real provider with a real key.
- If a backend piece is not ready yet, the Frontend work waits on it or is built behind a feature flag that is off. It does not ship a pretend version.

## 2. Rules that shape the design (do not break these)

Source: Anthropic's Claude Code legal page (re-read 2026-10-28) and the product model-access decision.

1. kell never collects, stores or relays a claude.ai login, session token or subscription OAuth token. Not in the browser, not on the server, not in the helper. Anthropic: developers "may not collect, store, or intermediate Claude.ai credentials or session tokens".
2. kell never runs a user's Claude for them on kell's own key or account, and never resells usage. Each user's agent authenticates with their own login, API key or cloud credential.
3. Work runs in the user's OWN session on their machine, using their unmodified agent. Do not build a path where the website starts an agent unattended on a subscription login. Unattended runs are allowed only with the user's own API key or Bedrock/Vertex credentials (a later phase, section 8).
4. Cursor: do not drive it. Its terms ban bots and scripts. Outcome metadata only.
5. Names: no "Claude", "Claude Code", "Codex" or "GPT" in the product, feature or handle names we mint. The mock seed handle `@alice/claude-code` must go; use neutral labels (`@alice/agent-1`) or the user's own chosen name. Plain-text statements like "works with Claude Code" are fine.
6. Task text is untrusted data for the receiving agent (same rule as kell's lookup text). The receiving side must show it as a task to review, not run it as instructions.

## 3. The shape

```
Site (Delegator UI)  <-->  Relay API (hosted, new)  <-->  Agent connector (user's machine)
   signed in (Supabase)       stores tasks + results         pulls tasks, returns results
```

Pull, not push: the relay never opens a connection to a user's machine. The connector asks "any tasks for me?" and answers. This is also why no inbound port or tunnel is needed.

Two ways an agent connects. Build the first now.

- A. Inside the user's own session (phase 1, no daemon): the hosted kell MCP gets two tools, `delegate_inbox` (list/claim tasks for this agent) and `delegate_result` (post the outcome). The user's Claude Code or opencode, already connected to kell, calls them when the user says "check my kell inbox". The kell prompt hook can also say "2 tasks waiting" on the next prompt. The user stays in the loop in the session they are already using.
- B. Local helper (phase 2): `kell agent pair <code>` runs a small process that polls the relay and hands tasks to an agent the user started themselves, with approval in the terminal. Still no unattended run on a subscription login.

## 4. Objects (proposal; the backend lane owns final names)

- Agent: `id`, `owner` (user id), `handle` (`@owner/name`), `kind` (free text: cli, ide, ...), `status` (online if it polled in the last 60 s, else offline), `last_seen`, `revoked_at`.
- Room: as today (`id`, `title`, `members`), stored server-side and owned by a user.
- Task: `id`, `room_id`, `from` (user or agent handle), `to` (agent handle), `text` (Markdown), `locators[]` (`path`, `lines?`), `depth`, `status`, `created_at`, `claimed_at`, `finished_at`.
  - status: `queued` -> `claimed` -> `running` -> `done` | `failed` | `cancelled`, plus `needs_approval` before `claimed` when the owner has approvals on.
- Result: `task_id`, `text`, `files_changed?`, `check?` (command and pass/fail), `by` (handle), `at`.

The existing `GroupMessage` / `DualPayload` shapes in `lib/delegator/types.ts` stay as the UI model; map tasks and results onto them.

## 5. API sketch (HTTP, bearer = Supabase access token; agent calls use a separate agent token)

User side (the site):
- `POST /v1/delegate/agents` -> creates an agent record and a one-time pairing code (valid 10 min).
- `GET /v1/delegate/agents`, `DELETE /v1/delegate/agents/{id}` (revoke).
- `POST /v1/delegate/rooms`, `GET /v1/delegate/rooms`.
- `POST /v1/delegate/rooms/{room}/messages` -> creates tasks for each mentioned agent (or none for a human-only line).
- `GET /v1/delegate/rooms/{room}/events` -> Server-Sent Events: messages, task status changes, agent online/offline.
- `POST /v1/delegate/tasks/{id}/approve`, `.../cancel`.

Agent side (connector or MCP tools):
- `POST /v1/delegate/pair` (pairing code -> agent token, scoped to inbox claim and result for that one handle only).
- `POST /v1/delegate/inbox/claim` (long poll, returns at most one task, marks it claimed).
- `POST /v1/delegate/tasks/{id}/result`.

Server rules: verify the token on every call (the page gate protects nothing); a user only sees their own rooms and agents; an agent token cannot read rooms; rate-limit sends; keep the depth limit of 4 for agent-to-agent hops and the "a human message resets to 0" rule; redact secrets in task text and results with the existing redaction chokepoint; keep an audit trail of who sent what; delete task bodies after a retention window (state it in the UI).

The backend and MCP parts are NOT the frontend lane's paths (`backend/**`, `packaging/npm/**`). Post a cross-lane request on the build board with this brief attached; do not edit them yourself.

### 5a. Where the real relay lives (proposal)

Put the relay routes on the hosted kell MCP server (`backend/app/mcp_server/server.py`, as custom routes next to `/triage`), not on the separate `app.main` API. Reasons: it is already deployed (Railway, `https://kell-mcp-production.up.railway.app`), it already verifies Supabase tokens, and the two MCP tools `delegate_inbox` and `delegate_result` live in the same process, so one store serves the site and the agents. Storage: new migration tables in the existing Neon database (agents, rooms, tasks, results), written through the repo's `tenant_transaction` / `scope_predicates` rules, with the redaction chokepoint on every free-text column. Realtime to the browser: Server-Sent Events from the same service (single worker is fine to start; the number of connections per worker is bounded).

The hosted URL for the site is `NEXT_PUBLIC_KEL_MCP_URL`'s origin; the Frontend client builds `/v1/delegate/...` from it. Local dev points at the local server (`http://127.0.0.1:8765`).

## 6. Frontend work (your lane: `routing_kell_frontend/**`, then keep `console/**` in step)

1. A `RelayClient` over the real relay API (section 5), pointed at the MCP server origin (`NEXT_PUBLIC_KEL_MCP_URL`; local dev: `http://127.0.0.1:8765`). `RoomView` talks only to this client. Remove the in-tab Broker and mock agents from the shipped path (section 1b); a test double may exist inside unit tests only.
2. "Connect an agent" panel: name your agent, get the pairing code with a copy button and the exact command to run on your machine (`kell agent pair <code>` once phase 2 exists; for phase 1, the sentence to say to your agent plus the MCP connector address from `/connect`). Show status (online/offline, last seen), a Revoke button, and an empty state.
3. Room view: task chips with status (queued, waiting for approval, working, done, failed), result cards with the check outcome, a cancel action on queued tasks, offline-agent notice ("this agent isn't connected; the task will wait until it checks in").
4. Approvals: if the owner turns on approvals, tasks wait in a "needs your OK" list before they can be claimed. Default on.
5. Replace the mock seed handles with neutral ones (rule 5). Update copy: say "your agents", never imply kell runs them.
6. Honest copy on the page: what is sent (task text, file paths and line ranges), what is not (no logins, no keys), how long it is kept, how to revoke.
7. Sign-in required for everything (already gated); real calls send the Supabase token, and handle 401 by sending the person to `/sign-in?redirect=/delegator`.
8. Tests: vitest for the client, the status mapping, the approval flow and the offline states, using `FakeRelayClient`; `tsc` and `next build` clean; keep the 17 delegator tests green or update them with reasons.

## 6b. BYOK chat: after signing in, a person adds their own keys and chats with a model right here (added 2026-10-28)

The point: a signed-in person should be able to talk to a model on the site, paid for by their own key, without installing anything. This is separate from delegating to their own agents (sections 3 to 6) and shares the room UI: each room gets a built-in "assistant" member backed by the person's key.

Rules (same legal footing as section 2):
- The only Anthropic credential accepted is an Anthropic API key (console.anthropic.com) or Bedrock/Vertex credentials. Never a claude.ai login, session token or "Sign in with Claude". Say so on the page, next to the field.
- Other providers: any OpenAI-compatible host (base URL + key: OpenAI, OpenRouter, Together, Groq, and so on). Offer a short provider list plus "other (OpenAI-compatible)".
- kell does not pay for, resell or pool anyone's usage. No fallback to a kell key.

Where the key lives (build mode 1 now, mode 2 is a cross-lane request):
1. Mode 1, browser-only (build this first, no backend needed): the key is encrypted in the browser (reuse the approach in `sync-key-cache.ts`, and `prod_frontend/lib/sync-crypto.ts` if it fits; do not store it in plain `localStorage`) and the browser calls the provider directly. The key never reaches kell servers. Check per provider that browser calls are allowed (Anthropic needs its direct-browser-access header; confirm OpenAI-compatible hosts send CORS headers; where a host blocks it, say "this provider can't be used in browser-only mode").
2. Mode 2, server-held (later): the key is sent once over TLS, stored encrypted per user through a secret resolver (`user:<subject>`; see `docs/provider_connections.md`, where `credential_owner: customer` means BYOK), and calls go through the governed `/v1/messages` and `/v1/chat/completions` path. The browser never gets the key back, only the provider name and the last 4 characters. Needs backend work: post it as a cross-lane request.

UI:
- "Models" panel under the signed-in account: add key (provider, optional base URL, key, optional label), "Test" button (one tiny call, show pass/fail and the model it can reach), list with last 4, created date and Delete. Deleting wipes it everywhere in the mode.
- Show plainly what leaves the browser and where it goes ("your key stays in this browser and is sent only to <provider>" for mode 1).
- Per-key spend guard: a client-side daily cap the person sets (soft), plus the provider's own limits. State that the provider bills them, not kell.
- Chat in a room: the assistant answers `@mentions` or any plain line when it is the only agent present; streaming reply; stop button; errors from the provider shown verbatim without the key.
- Keys must never be put in room messages, logs, analytics events or error reports. Add a test that scans a sent message and a thrown error for the key string.
- No key yet: the assistant slot shows "Add a key to chat here" and links to the Models panel.

Acceptance for 6b:
- Add a real key, Test passes against the real provider, chat streams a real reply, Delete removes it, a reload keeps it (encrypted) until deleted. (A stub provider is allowed only in unit tests.)
- A grep of `localStorage`/`sessionStorage` writes shows no plain key; the leak test above passes.
- The page copy contains no "Sign in with Claude" and no request for a claude.ai credential.

## 7. Acceptance (numbers, per the repo's rule that exits are numbers)

- Against the real relay running locally and then on the hosted server: a signed-in test account registers two agents, sends a message mentioning both, and the two tasks reach `done` with results posted by the agents through `delegate_inbox` / `delegate_result`; the 4-hop guard still stops a chain; revoking an agent removes it and its pending tasks.
- A request with no token or someone else's token gets 401/403 and sees none of the owner's rooms; a 401 in the UI redirects to sign-in; an SSE drop reconnects without duplicating messages. Show the real HTTP evidence (status codes), not a mocked run.
- The connect panel shows a pairing code that expires after 10 minutes and an agent that goes offline 60 s after its last poll.
- No string "Claude", "Codex" or "GPT" in any handle or product label we generate; grep proves it.
- vitest all green, `tsc --noEmit` clean, `next build` ok. Report the counts in the commit message.

## 8. Phases

1. Phase 1 (this brief): the real relay (tables, routes, SSE, auth) and the two MCP tools from the backend lane, built first or in parallel, plus the UI and client from the Frontend lane against it. No fake server. Agents connect through the MCP connector in their own session. BYOK mode 1 needs no relay and can start immediately.
2. Phase 2: `kell agent pair` helper with terminal approvals, in `packaging/npm`.
3. Phase 3: unattended runs, API key or cloud credentials only, with spend caps. Not for subscription logins.

## 9. Open questions (propose a default, log it on the board, keep going)

1. Retention of task text and results: default 7 days, deletable by the owner at any time.
2. Can two people share a room? Default: rooms are single-owner in phase 1; invites later.
3. Does an agent token live in the browser at all? Default: never; only in the user's terminal or MCP client.
4. Anthropic's view on the gray case (helper starting an agent unattended on a subscription): ask sales before phase 3 and before any marketing of "delegate to your Claude".
5. Codex/ChatGPT and opencode terms: check each before listing them as supported agents. opencode with the user's own API key is the safe default.
