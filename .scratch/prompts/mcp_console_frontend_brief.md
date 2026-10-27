# Brief for the frontend session: the MCP console (UI only)

Written 2026-10-26, revised 2026-10-27. This brief covers the **frontend changes only**. The local helper that does the
installing, the API below, and all changes to `packaging/npm/**` and `backend/**` are built by the setup session, not
you. You build the UI against the contract in the last section; until the helper exists, build against a mock of it.

## What the founder asked for

1. **One-click setup.** During setup the user is asked which tool they use (Claude Code, Cursor, Codex) and it installs
   automatically. No terminal commands for the user to copy.
2. **A place to add keys**, including local models.
3. **A UI where responses from other models are streamed** as they are produced.
4. **Matches `prod_frontend/` and `routing_kell_frontend/` in style.**

## Screens

### 1. Connect (the one-click flow)
- Step 1: "Which tools do you use?" Claude Code, Cursor, Codex; several can be chosen. Show which are detected on this
  machine (from the API), pre-ticked.
- If Cursor is chosen, ask for the model they have selected in Cursor (a dropdown of names, with a plain note that `auto`
  is not a model and dispatch stays off without one).
- One button: "Install". Show per tool, as it happens: working, done, or failed with the fix. Before it writes, show a
  preview of what will change (the API returns the list of files and a diff). Offer "Undo" afterwards.
- End on a **readiness checklist**: each line is ok or missing, with the fix beside it (server answers, signed in, models
  listed, key found, Cursor model set). The API returns exactly this list.
- Say what each tool gets, in the UI, honestly:

| Tool | Gets |
|---|---|
| Claude Code | The MCP tools and the per-prompt hooks (lookup, cheap-model dispatch with a check, outcome capture). Restart the tool. |
| Cursor | The MCP tools and the hooks, with dispatch once a model is named. Close every window and reopen. |
| Codex | The MCP tools today. Hooks for Codex are not integrated yet (Codex has a hooks feature; kell's adapter is a later piece of work), so the agent calls kell only when it chooses to. Restart Codex. |

### 2. Keys and models
- A list of providers and models with their state. Add a provider: name, base URL, model id, the key, optional prices
  (both or neither). Presets for the two default providers.
- **Local models:** a "Local model" option that takes a loopback URL (for example Ollama at `http://127.0.0.1:11434/v1`)
  and a model name, with no key field. A "Test" button that sends one small request and shows the latency or the error in
  words.
- A key is entered once. After saving, the page shows only the last four characters, a "Replace" and a "Remove". It is
  never put in browser storage, never shown again, never written into an error message or the URL. The same pattern as
  `admin/app/start`.
- Empty state, saved state, error state and "removed" state for each row.

### 3. Playground: streamed responses
- A model picker (from the API), a prompt box, and the reply rendered token by token with a Stop button.
- **Compare mode:** the same prompt to two or three models at once, side by side, each streaming on its own.
- Per response: model name, time to first token, total time, tokens in and out, and cost when prices are known (otherwise
  say "price not set").
- Provider errors (rate limited, key refused, timeout) are shown in words with what to do. Never raw response bodies.
- Label it as a playground. A streamed answer here is not "verified"; the page must not imply it is.

### States every screen must handle
- **Server unreachable:** say so and what to do; do not hard-code an address (the server is not hosted yet).
- **Helper not running:** the UI needs the local helper. When it is missing, show the one command to start it, with a
  copy button and a "Check again" button.
- **No test command found in the repo:** a note that cheap-model dispatch is skipped until one is set.
- **Key missing**, **Cursor model not set**, **nothing installed yet**.

## Style

Match the existing apps; do not invent a new look.
- `routing_kell_frontend/app/globals.css` tokens: paper `#F8F6EF`, ink `#171717`, yellow `#FFD21F`, cobalt `#193A70`, soft
  `#FFF1A8`, grey `#777773`, the rule colors, `--pad`, `--max`, `--gap`, and the Bahnschrift / Barlow font stack.
- Reuse from `prod_frontend/components/`: `InstallCommand.tsx`, `StatusLabel.tsx`, `NotConnected.tsx`,
  `OnboardingGate.tsx`, `Header.tsx`, `Footer.tsx`, `DocsSidebar.tsx`, `Reveal.tsx`.
- Which app is which and file locators: `docs/repo_map/frontends.claims.md` and `frontends.procedures.md`. `CLAUDE.md` is
  explicit that `frontend/` is not a live frontend.
- Suggested home: a new section in `routing_kell_frontend` with its existing header and footer. Your call; say so on the
  board.
- Accessible: keyboard operable, visible focus, labels on every field, streaming text announced politely, responsive.

## Copy rules (from the founder's notes)

- No prices, no savings numbers and no "X% cheaper" until a real-session study exists.
- Say plainly what leaves the machine. Do not write "nothing leaves your machine": the Cursor hook sends the prompt (up
  to 1,500 characters) and `claims.md` to the kell server for the lookup, and that statement is unresolved in
  `docs/setup.md` section 7.
- Never put "Claude", "Codex" or "GPT" in the product's name or handles. Naming the tools in the picker is fine.
- The user's own Claude Code or Codex subscription stays theirs. The UI never asks for, stores or relays a Claude or Codex
  login.

## Process (from `CLAUDE.md`)

1. Claim the item on `.scratch/build-board.md` first and state your lane's granted paths.
2. Stay inside them. If you need something from the helper or the API that is not in the contract below, file a board
   request; do not edit `packaging/npm/**` or `backend/**`.
3. Exit criteria are numbers. Propose them on the board, for example: a keyboard-only user reaches the ready checklist
   for each tool in at most 3 steps; a saved key never appears in any rendered text, request URL or stored state (tested);
   the playground shows the first token as the API delivers it (measured, with the model named).
4. Tests ship in the same change. Commit prefix is your lane tag. Rebase onto `origin/main`; never force-push.

## The API the UI calls (owned by the setup session; build against a mock until it lands)

All on a loopback address the page is served from (or told about), every call carrying the helper's per-run token. JSON
unless noted. This is a draft; changes go through the board.

- `GET /clients` -> `[{id, label, detected, installed, hooks, notes}]`
- `POST /install {clients:[...], cursor_model?, dry_run?}` -> per client `{ok, changed_files:[...], diff?, error?, fix?}`
- `POST /uninstall {clients:[...]}`
- `GET /readiness` -> `[{name, ok, detail, fix?}]`
- `GET /models` -> the configured models (`name, model_id, base_url, key_env, local, key_set, key_last4, prices, tier`), with no key values; `POST /models`; `DELETE /models/{name}`
- `POST /models/{name}/test` -> `{ok, latency_ms, error?}`
- `GET /keys` -> names and last four characters only; `PUT /keys/{env_name} {value}` -> `{ok, last4}`; `DELETE /keys/{env_name}`
- `POST /chat {models:[...], messages:[...]}` (up to 4 models) -> one `text/event-stream`; every event is JSON tagged with
  `model`: `{model, delta}` while it streams, then `{model, done:true, ttft_ms, total_ms, tokens_in, tokens_out, cost_usd}`,
  or `{model, error}` in words. Models stream concurrently into the same response; close the request to stop them all.
- Auth: every call carries the token from the address the helper prints (`?t=...` on the first page load), as the
  `x-console-token` header. A page served by the helper (`--ui-dir`) is same-origin; any other origin is refused unless the
  helper was started with `--allow-origin`.
