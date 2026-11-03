# Chat tab parity: `console/` vs `routing_kell_frontend/` (inventory, 2026-11-03)

Goal as given: the chat tab should be "exactly the same, with animations and all". The only second chat tab in the repo is
`console/app/chat` (titled "Chat (dev sandbox)", last changed 2026-10-31); `prod_frontend/` and `admin/` have none. This
file is the measured gap, so the port can be done in known steps. Read as: bring `console/` up to `routing_kell_frontend/`.
If the intended reference is something else, this inventory is still the baseline to compare against.

## Size of the gap (tracked files under app/chat, components/chat, lib/chat)

| | routing_kell_frontend | console |
|---|---|---|
| files | 72 | 16 |
| `RoomView.tsx` lines | 1236 | 266 |
| `chat.css` lines | 1338 | 115 (+ `rooms.css`, 11.5 KB, console only) |
| `@keyframes` / `transition` rules | 26 / 79 | 7 / 19 |

## Already identical (no work)

`useSpringScroll.ts`, `lib/chat/spring.ts`, `adapters/mock.ts`, `relay/channel.ts`. The spring physics is shared, so the
scroll feel already matches; what differs is everything built on top of it.

## Same file, console is an older, smaller version (port by replacing)

`app/chat/chat.css`, `app/chat/page.tsx`, `Composer.tsx`, `HamburgerMenu.tsx`, `ManageRooms.tsx`, `RoomView.tsx`,
`adapters/mcp.ts`, `relay/broker.ts`, `rooms.ts`, `types.ts`, `ui.ts`.

## Missing from console entirely (57 files)

- 27 components, including every motion piece: `SmoothText` (streamed text), `useSlideHighlight` (sliding selection),
  `CursorLayer` (other people's carets), `Popover`, `PanelShell`, `CanvasPanel` / `CanvasCard` (the iOS-style sheet),
  plus `Markdown`, `Latex`, `SearchPalette`, `SettingsDialog`, `MemoryPanel`, `NotificationBell`, `ProfileWidget`,
  `MessageActions`, `FileCard`, `HtmlViewer`, `DocPanel`.
- 28 files under `lib/chat/` (canvas, live editing, backups, notifications) and 2 more adapters
  (`assistant.ts`, `resolve.ts`).
- Runtime dependencies console does not declare: `marked`, `katex` (+ `@types/katex`). `@shadcn/react` is already there.

## The real decision (not a copy job)

`routing_kell_frontend`'s `RoomView` needs `subject` (a signed-in session) and the hosted rooms API, plus `lib/session`,
`lib/byok`, `Wordmark`, `BackButton`. Console's chat runs on a local mock / MCP relay with no sign-in. Pick one:

1. **Hosted data in console.** Port the 57 files and the session/BYOK modules; console then needs a sign-in. Exact
   visual parity; most work.
2. **Presentation only.** Port components + CSS, keep console's local adapters behind the same `resolve.ts` seam.
   Parity of look and motion; local data stays local.
3. **One source.** Make console import the chat from `routing_kell_frontend` (`experimental.externalDir`), so the two
   can never drift again. Least code, but console then builds against the other app's dependency tree.

## Checks that prove parity once done

`console`: `npm run typecheck`, `npm test`, `npm run build`; then a side-by-side screenshot of the same room in both apps
at 1240x880 (light and dark) and the same interaction recorded in both: send a message, open the canvas sheet, open
search. Honest limit: nothing here has been run on the console side yet.
