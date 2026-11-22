# Remote control: run the PC's chats from a phone or another browser

Owner's ask, 2026-11-12: chats run on my PC; when I lock the PC I should still be able to continue them from another
browser or my phone, and I should be able to turn notifications to my other devices on or off.

This note is the design, grounded in what the desktop app already has. Sources are at the end (Exa search, 2026-11-12).

## 1. What "lock mode" has to mean

Locking Windows does **not** suspend programs: the session locks, the display can sleep, and processes keep running. Only
**sleep / hibernate** stops them. So "lock mode" is two separate things:

1. **Keep serving while locked.** Nothing special is needed to keep the process alive — only to keep the machine from
   sleeping, and to reconnect after a resume or a network change.
2. **Keep the screen off.** We want the display to sleep; we do *not* want `ES_DISPLAY_REQUIRED`. Electron's
   `powerSaveBlocker.start("prevent-app-suspension")` (or `SetThreadExecutionState(ES_SYSTEM_REQUIRED | ES_CONTINUOUS)`
   on Windows) resets the system idle timer and lets the display go dark — exactly the pair we want.

Wiring: on `powerMonitor` `lock-screen` / `unlock-screen` / `suspend` / `resume`, mark the remote state offline/online and
re-register with the relay; while remote mode is on, hold the power-save blocker; optionally auto-start at login (a locked
PC that reboots must come back without a person at the keyboard). The window can minimise to a tray icon; the engine and
the Next server stay children of the Electron process, as today.

## 2. What the app already has (and what that saves us)

| Piece | Today | What it means for remote |
|---|---|---|
| Next server | Spawned by `electron/main.ts` on `127.0.0.1:<random>`, loaded in the app window | It is already a normal HTTP app with an SSE API; a second browser can use the same routes |
| Engine | `opencode serve` child on `127.0.0.1:<random>`, password printed to the parent, Basic auth | Never expose it; the remote client only talks to the Next server, which keeps the password |
| Sessions | `desktop/lib/localdb.ts` (SQLite) + the engine's own store | One source of truth for both surfaces |
| Model lane | `lib/pooled.ts` → kell gateway (personal or org) | No provider key is on the machine; remote access does not change billing |
| Sign-in | `lib/websession.ts` (website session for models), `electron/rooms.ts` (rooms) | A paired device can be tied to the same kell account |
| Notifications | Website chat has `routing_kell_frontend/lib/chat/notify.ts` (desktop/sound/previews/quiet/rooms) and a bell; Web Push is designed, not built (`docs/chat_notifications_e2ee_mcp.md`) | Reuse that preference shape for per-device toggles |

The gap is not "make it reachable". It is **auth, pairing, multi-client sync, and push** — all four are missing today.

## 3. Transport: three options, one recommendation

| Option | How it works | Opens ports? | Needs backend work? | Best for |
|---|---|---|---|---|
| **A. Private mesh (Tailscale)** | Bind the Next server to the tailnet address; phone joins the same tailnet | No | No | Daily use, now |
| **B. Outbound relay** (Claude Code's model) | The app dials out over HTTPS/WSS to kell's gateway; messages are relayed; no inbound port | No | Yes (gateway + relay) | "Works with nothing installed", later |
| **C. Tunnel** (Cloudflare Tunnel / OpenTunnel) | `cloudflared` dials out and exposes a stable HTTPS URL | No | No (just a config + auth) | A public URL on your own domain |

Claude Code's Remote Control is **B**: outbound HTTPS only, the CLI registers with the Anthropic API and polls; the phone
is a pure UI; execution stays local; short-lived credentials; the transcript is stored server-side so devices sync and
reconnect. OpenCode's own web mode is closer to **A/C**: `opencode pair` prints a one-time link (5 minutes) that signs a
browser in for 30 days, `--remote` exposes it through OpenTunnel, and the server password plus CORS allowlist are the gate.

**Recommendation: build A first.** It is a day of work, needs no server, and keeps the blast radius to a private
WireGuard network. Design pairing/auth so B can reuse it later; add C as an "advanced" option (a stable URL without
installing Tailscale).

## 4. Pairing and auth (the part that must not be skipped)

The remote surface is **full code execution on the PC**. Treat it like an SSH key, not like a web page.

1. **Never bind the engine.** The Next server is the only listener. The engine stays on `127.0.0.1` with its printed
   password; no route ever returns that password to a client.
2. **Pairing code.** Settings → Remote shows a 6-digit code + QR (and a one-time link). The code expires in 5 minutes,
   is single-use, and is rate-limited (e.g. 5 attempts per 10 minutes per IP).
3. **Device token.** The phone POSTs `{ code, name }`; the app creates a row in localdb: device id, name, token hash
   (SHA-256 of 32 random bytes), created, last seen, revoked. The token is returned **once** and stored as an HttpOnly,
   `SameSite=Lax`, `Secure` cookie scoped to the remote origin. Sessions last 30 days; changing the server password
   revokes every session (OpenCode's rule).
4. **Account tie (optional but cheap).** Require the person to be signed in to the same kell account on both sides
   before a device may pair. The app already knows the account (`/api/settings`), so this is one check, and it stops a
   stranger on the tailnet from pairing even if they see the code.
5. **Every request is checked** by middleware (`middleware.ts`): valid session cookie or bearer → device row → not
   revoked/expired. CSRF: SameSite + `Origin` check on mutating routes. CORS: only origins explicitly paired.
6. **Audit + kill switches.** Settings lists devices with last-seen and a Revoke button; "Remote off" stops listening
   immediately; "Sign out all devices" deletes every row. Log pair/revoke/prompt with device + time in localdb.
7. **What the phone may do.** Start with the whole app (it is the owner's own PC), but keep a scope column on the device
   row so approvals or file writes can later require the local surface.

## 5. Multi-client sync (the invisible half)

Today `/api/prompt` subscribes to the engine **for the duration of one request**. If a phone sends a prompt, the desktop
window does not see the turn; if the phone reconnects, the stream is gone. Fix before exposing anything:

- **One event hub per engine.** A module singleton on `globalThis` (the same trick `lib/engine.ts` uses for the engine)
  subscribes once to `client.event.subscribe()` and fans out to N SSE clients, while appending assistant text to localdb
  as it streams.
- **`GET /api/events?sessionID=`** for both surfaces (local and remote) on mount; `POST /api/prompt` only sends and lets
  the hub broadcast. A reconnecting client gets the in-flight text replayed from the hub's buffer.
- **Presence**: the hub knows how many clients are watching a session; use that for "don't notify the device that is
  looking at it" (the same rule as `decide()` in `lib/chat/notify.ts`).
- Keep `/api/prompt`'s current behaviour for the local surface until the hub lands, then switch both.

## 6. Notifications to other devices

Two layers, one preference model:

- **Local** (the PC): Electron `Notification` while the window is hidden; already possible.
- **Remote** (paired phones/browsers): **Web Push** — the standard way to wake a closed browser. A service worker
  (`/sw.js`) + `pushManager.subscribe({ applicationServerKey })` on the phone; the app stores the subscription
  (`endpoint`, `p256dh`, `auth`) on the **device row**, which is exactly what makes "notifications to my other devices
  on or off" a per-device switch.
- **Sender**: `web-push` in the Next server (RFC 8030 + VAPID + RFC 8291 encryption). Generate the VAPID pair once per
  install and keep it in the app's prefs file; delete a subscription on 404/410.
- **Events worth a push**: turn finished, approval waiting, goal met, turn failed. Not every token.
- **Privacy**: by default the push says "kell finished in <project>" and nothing else; if previews are on, the first
  line. The full text never leaves the PC in a push payload.
- **Toggles** (per device, reusing the website's `Prefs` shape): notifications on/off, sound, previews, quiet hours,
  per-event kinds. A device that is currently watching the session gets no push.
- **Platform notes**: Web Push needs a secure context — the mesh/tunnel gives HTTPS, `localhost` is exempt. iOS supports
  Web Push only for a site added to the Home Screen (16.4+); Android/desktop Chrome/Edge/Firefox are fine. Safari 18.4+
  can show a declarative push without a service worker.
- **Zero-backend alternative**: ntfy / Pushover / a webhook. Works today, but a third party sees the metadata and the
  body; keep it as an option, not the default.

## 7. Build order

| Phase | Work | Effort | Ships |
|---|---|---|---|
| **P0** | Remote mode + bind to tailnet/LAN + pairing + auth middleware + Settings → Remote (code, QR, devices, revoke, off) + power-save blocker + lock/resume handling | 0.5–1 day | "Lock the PC, open the tailnet URL on the phone, continue" |
| **P1** | Engine event hub + `GET /api/events` + reconnect/replay + presence | 1–2 days | Both surfaces see the same turn |
| **P2** | PWA manifest + service worker + Web Push + per-device toggles + quiet hours + push on approval/finish | 1–2 days | Notifications on the phone, on or off per device |
| **P3** | Tray icon, auto-start at login, one-time link + QR polish | 0.5 day | Set-and-forget |
| **P4** | Outbound relay on kell's gateway (Claude Code's model) so nothing needs installing | backend sprint | Works from anywhere, no Tailscale |

## 8. File map for this repo

- `desktop/electron/main.ts` — bind host/port for remote mode, `powerSaveBlocker`, `powerMonitor`, tray, auto-start.
- `desktop/app/api/remote/*` — `state` (on/off, code, devices), `pair`, `revoke`, `events` (SSE).
- `desktop/lib/remote.ts` — device rows + token hashing, code generation/expiry, rate limits.
- `desktop/middleware.ts` — session check for every route when remote mode is on.
- `desktop/lib/localdb.ts` — `devices`, `push_subscriptions`, `remote_audit` tables (additive, as today).
- `desktop/app/api/push/*` — VAPID public key, subscribe/unsubscribe, send on turn events.
- `desktop/public/sw.js`, `desktop/public/manifest.webmanifest` — the PWA + push handler.
- `desktop/app/page.tsx` — Settings → Remote (pairing, devices, notifications per device, quiet hours).
- `docs/chat_notifications_e2ee_mcp.md` — the notification rules we reuse; extend with the push delivery that doc
  already designs.

## 9. Security checklist (do not skip)

- Do not bind `0.0.0.0` without the auth middleware in front; do not port-forward the raw port; do not expose the
  engine.
- The pairing code is a capability: short-lived, single-use, rate-limited, and shown only on the local surface.
- Revocation must be immediate: middleware reads the device row every request.
- Treat the phone as the owner: it can run commands on the PC. Say so in the UI before enabling remote mode.
- Push payloads are encrypted in transit to the browser's push service, but that service still sees timing and size;
  keep the body minimal by default.
- Log every pair, revoke and remote prompt; make "Remote off" and "Sign out all devices" one click.

## 10. Sources (Exa, 2026-11-12)

- Claude Code Remote Control — https://code.claude.com/docs/en/remote-control (outbound-only, relay, short-lived
  credentials, transcript stored server-side, one session per process)
- Datacamp / Upskill / Lynx analyses of the same — polling + SSE relay, no inbound ports, phone is a pure UI
- OpenCode web — https://opencode.ai/docs/cli/web/ (`opencode pair`, 5-minute one-time link, 30-day sessions,
  `--remote` via OpenTunnel, password + CORS, `serve --hostname`)
- OpenCode remote guides — Tailscale / Cloudflare Tunnel / ngrok; do not port-forward; always set a password
- VS Code Server — https://code.visualstudio.com/docs/remote/vscode-server (CLI tunnel architecture)
- Web Push — web.dev push server codelab; self-hosted relays (bilalhasson/push-relay, mpizenberg/go-notify-server):
  VAPID, service worker, per-device subscriptions, 410 cleanup, iOS PWA requirement
- Windows — SetThreadExecutionState (ES_SYSTEM_REQUIRED vs ES_DISPLAY_REQUIRED):
  https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreerexecutionstate
