# The desktop app: kell's own coding app (plan for approval, 2026-11-01)

Status: plan, with steps 1–2 built as a prototype (`desktop/`, commit `4504eeb3`, verified end to end 2026-11-02). The
shell choice (Tauri or Electron) still needs the owners before steps 3–6. Verified facts: "Prototype built" below.

## Goal

kell's own coding app, like Claude Code's desktop app, in the look of the website and the console. The engine is OpenCode
2.2.5 with kell's plugin (part 1); the app is a window onto it.

## What exists to reuse

- **Website** (`routing_kell_frontend/`, Next.js 15, React 19): `app/globals.css` holds the shared design tokens.
- **Console** (`console/`, Next.js 15, React 19): `app/globals.css`, re-usable components
  (Header, Footer, AccountMenu, CopyField; Console, ConnectPanel, ModelsPanel, ActivityPanel, PlaygroundPanel, StatusBar,
  `chat/`), and helpers (`helper.ts`, `sse.ts`, `types.ts`, `words.ts`).
- **Shared tokens** (identical in both; copy them, do not invent a palette):
  - colours: `--paper #F8F6EF`, `--ink #171717`, `--yellow #FFD21F`, `--cobalt #193A70`, `--soft #FFF1A8`,
    `--grey #777773`, `--rule rgba(23,23,23,.13)`, `--rule-strong rgba(23,23,23,.85)`;
  - font: `"Bahnschrift", "Barlow", "DIN Alternate", "Segoe UI", system-ui` (body weight 300);
  - code: `"Cascadia Mono", ui-monospace, ...`; buttons: `.btn-ink`, `.copy`.
- **The connection**: `packaging/npm/lib/console.mjs` (`kell console`) — a loopback HTTP server with a token in its
  address, routes `GET /health`, `GET /clients`, `POST /install`, `POST /uninstall`, plus keys, local models, streamed
  replies and an activity feed from the hosted server's `/activity`. Started the way `console_launch.mjs` does.

## Architecture

```
+------------------------------------------+
| desktop app window                       |
|   Next.js UI (tokens above, keln visual) |
+------------------------------------------+
        | @opencode/sdk              | HTTP (tokened loopback)
        v                            v
+-------------------+      +----------------------+
| opencode serve    |      | kell console helper  |
| (pinned 2.2.5,    |      | (sign-in, keys,      |
|  kell plugin)     |      |  activity feed)      |
+-------------------+      +----------------------+
        |                            |
        v                            v
   min ≤ strong models        https://mcp.kell.org.in (same reporting)
```

- The app starts (or finds) `opencode serve` as a child process, pinned to 2.2.5, with the plugin installed by
  `kell setup --client opencode` into the profile the app owns (the isolated-profile pattern).
- The app also starts (or finds) the console helper, the same way `console_launch.mjs` does, and shares one sign-in
  with it.
- The UI talks to OpenCode through `@opencode/client` (the generated typed HTTP client; the SDK's in-process Effect host
  is not used) for sessions, prompts and streamed events, and to the helper for keys, models and the activity feed.
- What the console shows: the app's sessions appear in its Activity panel, because both report to the same hosted
  server (`report_model_run`, `/tools/outcome`).

## The screens (first build)

1. **Project picker:** open a folder; engine boots inside it.
2. **Chat:** prompts and streamed answers; tool calls rendered inline (the console's `chat/` components re-used).
3. **Changes panel:** a diff of what the cheap model changed, with Apply / Undo (`kell undo`).
4. **Model strip:** which model did each step and its cost — fed by the plugin's `kell_usage` (part 1 step 4, already
   in the plugin).
5. **Tool suggestions:** kell's tool note as cards, one click to add, or "use through kell" (the gateway).
6. **Settings / Account:** the console's panels (connect, models, activity, keys) rendered inside the app;
   an "Open console" button opens the web console at the helper's tokened address.

## Shell choice (owner decision needed)

- **Tauri (recommended):** small binaries (single-digit MB plus the system webview), Rust side only for the window and
  process spawning; the Next.js UI and the process management stay ordinary Node work run through the helper or a small
  sidecar script. Cost: one more toolchain (Rust) at build time.
- **Electron:** everything is Node; ships a browser; bigger installer. Cost: size and memory.
- Either way the window loads the same Next.js UI, so the decision can wait until the UI prototype exists — build the UI
  with `next dev` first, and bolt the shell on after.

## Build order

1. **Skeleton:** `desktop/` with the Next.js UI (token CSS copied into `globals.css`) and a `main` process (or sidecar)
   that spawns `opencode serve` and the helper, both health-checked before the window opens the UI.
2. **Chat with streaming:** sessions list + chat view through `@opencode/client`; show tool calls as they happen.
3. **Changes panel:** per session, diff of the session's edits (OpenCode's file-changed events), Apply / Undo via
   `kell undo`; dry-run first with the fixture.
4. **Model strip and tool cards:**(simplicity) model + cost per step from `kell_usage` events; kell's tool suggestions
   from the plugin's existing note path.
5. **Sign-in:** the app shows "Signed in as ..." from the helper; a "Sign in" button runs `kell login` (browser OAuth).
6. **Packaging:** installers for Windows and macOS. **Not published without the owners.**

## Prototype built (2026-11-02, `desktop/`)

Steps 1–2 of the build order exist as a Next.js 15 app (`desktop/`): `POST /api/engine` boots the engine, the page is the
chat with streamed events, `POST /api/undo` runs `kell undo`. `npm run build` is green; the flow boot → prompt → undo ran
live against the `esc` fixture (19 events, 13.2 s, clean `done`). Verified facts, for whoever builds steps 3–6:

- **Engine process:** one `opencode serve --hostname 127.0.0.1 --port 0` child per Next server, spawned by
  `lib/engine.ts` with the isolated profile's env; the singleton lives on `globalThis.__kellEngineState` because Next
  bundles each route with its own module instance.
- **Auth is Basic on `/api/*`:** the engine answers `WWW-Authenticate: Basic realm="Secure Area"` there and wants
  `base64("opencode:<password>")`; the printed password as a bearer token works on the other routes (`/agent/list`,
  `/session`). `clientFor` bakes the Basic header into the `@opencode/client` instance.
- **Event payloads put everything under `data.*`** (`session.text.delta` → `data.delta`; `session.step.ended` →
  `data.cost`, `data.tokens`, `data.model`), not under `properties`.
- **Subscribe before prompting** in the SSE route, and filter events to the turn's `data.sessionID` — otherwise the turn
  can finish before the stream starts, or another session's events leak in.
- **The turn's terminator is `session.execution.succeeded` / `session.execution.failed`** (`session.idle` may not fire in
  serve mode); the route forwards that as `{"type":"done","outcome":...}`.
- **Model strip feed:** `session.step.ended` carries cost and tokens per step; the page sums it into a spend total (the
  plugin's `kell_usage` line remains the kell-side copy).
- **Environment:** the OpenCode desktop app already installed here owns managed-service port 28894, so a second setup
  needs `opencode service set port <other>` (the profile uses 28941; row in new_user_setup.md). SSE through
  PowerShell's `Invoke-WebRequest` looks like zero events — test with `curl.exe -N`.

## Later

- Team rooms from the website (`routing_kell_frontend`, rooms API) as a side panel.
- Session history search; notifications when a long run finishes.

## Opens (for the owners)

- **Shell:** Tauri or Electron (recommended: Tauri). Can be deferred: the UI is the same either way.
- **Name and window chrome:** "kell" plus own icon; anything else wanted in the title bar (model strip as a status bar
  is assumed).
- **Windows first or both at once?** (the fixtures are Windows-tested so far).

## What must not happen

- No keys in the UI, the logs or the installer: the engine runs on the user's own saved key from
  `~/.stealthlab/config.json`, read by the helper; the app only shows "Signed in as ...".
- The app never edits `prod_frontend/` and never publishes anything by itself.
