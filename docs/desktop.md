# kell desktop: Rooms sign-in handoff (2026-11-06)

Short pickup note. Plans and release steps are in `desktop_app_plan.md` and `desktop_release.md`.

## What it is

The desktop app (`desktop/`, Electron + Next) has a **Code** tab (OpenCode engine, signed in with `kell login`) and a
**Rooms** tab. Rooms is the website's own `/chat` in a `WebContentsView` (`desktop/electron/rooms.ts`), so people, agents and
canvas are exactly the site's.

## Which file does what

**Electron main process** (`desktop/electron/`, compiled to `electron/dist/`, built with `npm run electron:build`)

| File | Job |
|---|---|
| `main.ts` | Starts `next start` on a free port as a child process, opens the one window, registers every IPC handler (`kell:rooms-*`, `kell:account-open`, `kell:open-signin`), runs the auto-updater, kills the child tree on exit. |
| `preload.ts` | The only bridge to the page: exposes `window.kellDesktop` (`openAccount`, `signOutRooms`, `rooms.{show,reload,theme,account,signIn,emailSignIn}`, tool sign-in). Nothing else is exposed. |
| `rooms.ts` | Everything about the Rooms view: creates the `WebContentsView` on `<site>/chat` (partition `persist:kell-web`), positions it over the page's placeholder, `signInRooms` (PKCE for Google/GitHub), `openRoomsEmailSignIn`, `roomsAccount` (reads who the rooms are signed in as), `signOutRooms`, `openAccountSignIn` (opens `kell login`'s authorize link in a window on the same partition, so the consent page already knows the person). |
| `signin.ts` | The app's own small window for a *tool's* sign-in (GitHub, Notion, etc.), not the account sign-in. |

**Page and routes** (`desktop/app/`, the Next UI that the window shows)

| File | Job |
|---|---|
| `page.tsx` | The whole UI: Code/Rooms tabs, the rooms placeholder, the "Connect" banner and its three buttons, `signIn` / `signOut` / `connectRooms`, the account-mismatch banner. Calls `window.kellDesktop`. |
| `api/signin/route.ts` | Runs `kell login --no-browser`, returns its authorize link; `DELETE` runs `kell logout`. Only checks that a token exists in `config.json`, never reads or returns it. |
| `api/settings/route.ts` | Reports `signedIn`, the account and the model lane from `~/.stealthlab/config.json`; the page polls it after sign-in. |

**Website** (`routing_kell_frontend/`, deployed on Vercel)

| File | Job |
|---|---|
| `app/api/desktop-config/route.ts` | Serves the public Supabase project URL and anon key so the app can run PKCE without baking them in. |
| `app/chat/page.tsx` | The rooms themselves (what the Rooms view shows). |
| `app/sign-in/page.tsx` | Email/password and Google/GitHub sign-in; the email button in the app loads this inside the rooms view. |
| `app/oauth/consent/page.tsx`, `lib/oauth-consent.ts` | The "Allow" page for `kell login` (Supabase OAuth server). This is the coding token's path, separate from the rooms session. |
| `lib/supabase.ts` | The one Supabase browser client; stores the session under `sb-<ref>-auth-token`, the key `signInRooms` writes. |

**Backend** (`backend/app/`, Railway; unchanged by this work)

| File | Job |
|---|---|
| `services/authn.py::supabase_token_allowed` | Rejects an MCP token on the website API and a website session on MCP. This is why there are two tokens. |
| `mcp_server/oauth_resource.py` | The MCP side of the OAuth flow `kell login` uses. |

## /goal (0.1.17)

`/goal <condition>` sets one completion condition for the session. After every turn `app/api/goal` asks the cheap model
on the same kell gateway to judge the transcript: **met**, **not_yet**, or **impossible**. `not_yet` starts another turn
with the evaluator's reason; met/impossible clears the goal. `/goal` shows status, `/goal pause`/`resume`/`clear` manage
it, and a condition may say `stop after N turns` (default 20). It stops on three turns with no tool use, and a failed
turn pauses it. This mirrors Claude Code's `/goal` (`code.claude.com/docs/en/goal`), with kell's own engine and cheap
model instead of Claude Code's Stop hook. The goal is session-scoped and restored with the session.

## /btw (0.1.18)

`/btw <question>` asks a side question about the chat on screen and shows the answer in a dismissible overlay (Esc, or
the ×), never in the conversation. It mirrors Claude Code's `/btw` (`code.claude.com/docs/en/commands`): the question
sees everything already said in the session, has **no tools** (it cannot read files, run commands or search), gets one
response, and is not added to the history or sent with the next prompt. `app/api/btw` sends the recent transcript plus
the question through the engine's one-shot `experimental.generate.text`, so it creates no session and leaves a running
turn alone — `send()` intercepts `/btw` before the busy check, which is what makes it work mid-turn. Bare `/btw` brings
back the most recent side answer. It costs one small model call on the same lane as the session.

## Slash menu (0.1.18)

The composer has the website chat's slash menu (`SLASH_COMMANDS` in `app/page.tsx`, rendered with the `.dg-mentions
.dg-commands` styles already in `chat.css`): type `/` and it lists what this app actually understands — `/btw` and
`/goal`. Up/Down move, Enter/Tab pick (filling the command and a space), Esc dismisses it for that draft, and a command
typed in full closes the menu so Enter runs it instead of picking it again. Commands are dispatched in `send()`, before
anything reaches the engine; the same two are in the Ctrl/Cmd-K palette.

## Where chats live (0.1.18)

Two SQLite files, and the app owns only one of them.

| File | Owner | Holds | Read by |
|---|---|---|---|
| `~/.stealthlab/chats.db` | the app (`lib/localdb.ts`) | `sessions`, `messages`, `attachments`, `summaries`, `projects`, `meta`, plus an FTS index | the sidebar (`/api/local/sessions`), a chat's messages, search, the project export |
| `~/.local/share/opencode/opencode.db` | the engine (`opencode serve`), shared with the `opencode` CLI | `session`/`session_v2`, `message`, `part` | the engine itself; the app uses it to execute turns and as a one-time backfill |

A turn: `/api/prompt` writes the user row before the engine runs and the assistant row when the turn ends; the engine
keeps its own copy for execution. The sidebar reads the app's store, not the engine (`/api/local/sessions`), and a chat
with no local messages is copied from the engine once, lazily, on first open (`/api/local/sessions?id=`). That is why
the engine database can be deleted, upgraded or replaced without taking the chats with it.

Why the app opens where it left off: the page's `localStorage` is wiped on every start (`electron/memory.ts`, because
the origin changes each run), so the folder and the chat to reopen live in `chats.db`'s `meta` table (`app/api/last`).
That wipe is also what made "my chats are gone" look true — the chats were in `chats.db` all along, but the app started
on nothing and never asked for the list. Fixed in 0.1.18: the project is reopened from `meta`, the sidebar lists its
local chats even when the engine is down, and a chat opens from the local copy.

Import (`app/api/import`) writes the whole conversation in one transaction; a message that fails no longer leaves an
empty session row behind (the row used to be inserted before `BEGIN`, so `ROLLBACK` could not take it back).

## Right panel resize (0.1.17)

The changes panel uses the same recipe as the website's docs panel (`docs/ios_animation.md` §4.4): drag the hairline or
use the arrow keys (Shift for 40 px), the width is written to the DOM in an animation frame with transitions off while
dragging, a shield covers the content, and the width is remembered. It can grow to the window edge (leaving 360 px for
the chat) instead of stopping at 640 px, and the handle also works in the narrow drawer layout.

## Typeface (View > Font, 0.1.16)

`View > Font` offers **Default** (the existing Bahnschrift/Barlow stack), **Inter** and **Poppins**. The fonts are
self-hosted with Fontsource (`@fontsource-variable/inter`, `@fontsource/poppins`); the CSS is loaded once and the browser
downloads a face only when text uses that family, so the family that is not chosen costs no font bytes. The choice is one
localStorage key (`kell.font`), restored before paint by `public/font-init.js`, mirrored to `desktop-prefs.json` like
every other setting, and shown as a radio mark in the native menu.

## Two tokens, on purpose

| Token | Made by | Used for |
|---|---|---|
| MCP OAuth token (has `client_id`) | `kell login`, saved in `~/.stealthlab/config.json` | coding lane, gateway |
| Website session (no `client_id`) | Supabase sign-in on the site | rooms API |

The backend rejects each on the other's surface (`backend/app/services/authn.py::supabase_token_allowed`). Do not
"fix" this by sharing one token. Nobody pastes a key anywhere, and no token is copied between programs.

## How the Rooms session is obtained (shipped, `b192d462`)

- **Google / GitHub:** `signInRooms()` in `rooms.ts` is its own PKCE client (RFC 8252). It makes a verifier, listens once on
  `127.0.0.1:<random>/callback/<nonce>`, opens Supabase `/auth/v1/authorize` in the default browser, gets back only a one-time
  code, swaps it at `/auth/v1/token?grant_type=pkce`, and writes `sb-<ref>-auth-token` into the rooms view's localStorage. It
  is the app's own refresh-token family, so the browser session is untouched. Project URL and anon key (public) come from the
  site: `routing_kell_frontend/app/api/desktop-config/route.ts`.
- **Email and password:** `openRoomsEmailSignIn()` loads the site's `/sign-in?redirect=/chat` inside the rooms view (works
  there, with Turnstile). Only Google refuses an embedded window.
- UI: the Rooms placeholder in `desktop/app/page.tsx` (three buttons) shows when the rooms are not signed in; IPC names are
  `kell:rooms-signin` and `kell:rooms-email` (`main.ts`, `preload.ts`). The view is placed when the **website session**
  exists, not when the coding token does, so the website sign-in is the first step.

## One sign-in (shipped 2026-11-11, 0.1.15)

1. The person signs in once for the rooms (Google/GitHub PKCE in the default browser, or email/password inside the view).
2. Once the website session is readable, `connectRooms` (or the email path's poll) calls `provisionCoding`: `POST /api/signin`
   starts `kell login --no-browser`, and `openAccountSignIn` opens its authorize link in a window on the **rooms' partition**.
   Supabase redirects to `www.kell.org.in/oauth/consent`, where `lib/oauth-consent.ts` reads the website session from
   localStorage. The person clicks **Allow** once; the CLI's `127.0.0.1` callback writes `~/.stealthlab/config.json`.
3. No second credential is asked for. The two tokens stay separate (see below); the consent page is a consent, not a login.

Rejected on purpose: copying the browser's session into the app (refresh-token reuse gets the whole session revoked; was built
and removed), and an in-app email one-time code (needs the Magic Link template edited and Turnstile blocks it).

## Open items (nobody has done these)

1. **Supabase → Auth → URL Configuration → Redirect URLs:** add `http://127.0.0.1:**`. Without it Google/GitHub fail at Supabase.
2. **Confirm the Vercel deploy** serves `GET /api/desktop-config` (503 means the `NEXT_PUBLIC_SUPABASE_*` env is missing there).
3. **Rebuild the installer** (`npm run dist` in `desktop/`); the installed app predates this change.
4. **One live check, never run:** Google, GitHub and email each through the real app. Checked so far: `tsc` only (app, Electron,
   site). There is no automated test for `signInRooms`.
5. Still untracked/other lane: `/download` page, `desktop.ps1`, `electron-updater` repo name placeholder
   (`3Founders/kell-desktop`). See `desktop_release.md`.

## Sharp edges

- ~~Two browser trips on first sign-in (coding login, then Rooms).~~ Fixed in 0.1.15: the coding consent runs in a window
  on the rooms' partition, so one sign-in plus one **Allow** covers both lanes.
- `roomsAccount()` reads the Supabase key from the view's localStorage; if the site changes its auth storage, the
  same-account check and the connect banner break.
- A `*/` inside a doc comment ended it early once (TS errors far from the cause); keep example URLs with `/**` out of comments.
