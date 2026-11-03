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

## Update (same day): the desktop app's chat, first slice ported

Reading the closure changed the target. `routing_kell_frontend`'s `RoomView` imports 88 files (Supabase, billing, BYOK,
device photos), so it cannot be dropped into `console/`; the chat a user of the Windows installer sees is
`desktop/app/page.tsx`, which was plain. Ported from `routing_kell_frontend`, byte-identical copies:
`lib/chat/spring.ts`, `lib/chat/smooth.ts`, `components/chat/{useSpringScroll.ts, Markdown.tsx, SmoothText.tsx, brand.tsx}`,
and the chat's motion/markdown/mark rules extracted into `desktop/app/chat-core.css` (the file says to re-extract, not
hand-edit). In the desktop chat that gives: spring scroll that follows new text only while you are at the bottom and
rubber-bands at the edges; the reply streams at a steady pace with each new word fading in; markdown (code, lists, bold);
and the same thinking indicator (kell mark, wave dots, breathing label) before the first words.

Verified: `tsc --noEmit` and `next build` pass; a temporary page rendering the real components, captured with headless
Edge, showed the mark, animated dots and rendered markdown. NOT verified: the mid-stream word fade on screen (virtual time
does not advance the stream timer) and a live engine turn.

Second slice (same day): message headers (name, agent badge, time) and the hover actions row (`MessageActions`: copy with the
tick feedback, "Ask again" / "Send this again"), copied byte-identical with `icons.tsx`; their rules appended to
`chat-core.css`. Checked by screenshot of the same markup with the hover row forced visible (headless cannot hover).

Third slice (same day): the glass composer - the base `.dg-composer` rules with the `.dg-page` glass layer folded in
(blurred recessed field, lift shadow, focus lift, press-scale send button, entrance animation, reduced-motion and
reduced-transparency fallbacks), same tokens and values. `tsc --noEmit` passes; Looked at on screen: the real desktop page under `next dev` in headless Edge shows the recessed glass field, lift shadow and a disabled Send (the installer
rebuild that was to show it installed was stopped by the harness for low memory), and the desktop composer has no attach/mention/expand
parts.

Fourth slice (same day): dark mode - the same dark tokens as `routing_kell_frontend/app/globals.css`, chosen by the device
(`prefers-color-scheme`) or by `data-theme`; the desktop's hard-coded white panels now use `--surface`; the user bubble uses
`--on-yellow`; diff and copy-tick colours have dark twins. `tsc --noEmit` passes, and the real desktop page under `next dev` was screenshot in dark
(headless Edge with `--force-dark-mode`; `--blink-settings=preferredColorScheme=1` did NOT switch it): dark paper, light text,
dark surfaces, dark soft banner, dark glass composer. Light-vs-dark side by side on an installed build is still unchecked. The desktop has no theme toggle (the website's is in Settings).

Still to port for "exactly the same" in the desktop chat: popovers and panels (`Popover`, `PanelShell`); a theme toggle; the
canvas sheet (`CanvasPanel`) once the desktop has something to put in it. `console/` is unchanged.

## Backend memory review, completed scan (same day)

AST scan of every `.fetch(` / `.fetchall(` in `backend/app`: 299 SELECTs analysed, 57 built dynamically (not analysed).
Only 12 have no WHERE, no LIMIT and no aggregate; nearly all are small registry tables (shards, plans, schema_migrations,
alert state). The two that grow with data, `routing_evidence_items` and `routing_aggregate_results`, are read only by
the offline fit (`routing/fit.py`), which needs the whole table. Per-request routing queries are all id-scoped. Fixed
earlier: the per-request draws-blob download (`f896f89e`) and the embedding cache's real size (`313ac96a`). Not covered:
the 57 dynamic queries and any list that grows in Python after the fetch.

**Dynamic queries, finished (same day).** The 57 queries built at runtime (147 call sites with a non-literal first argument, resolved through their enclosing function and module constants): 61 have a `LIMIT` in the function, 87 are scoped by `WHERE`/`ANY` without one, 1 is a wrapper (`publication_deps._safe_fetch`, text comes from callers). The two that read embedding columns without a `LIMIT` and looked heavy (`tool_routing/store._embed_missing`, `dedup.find_duplicate_clusters`) are batched or do the similarity in SQL. No memory-unsafe pattern found; the remaining risk is a `WHERE`-scoped list that is large for one tenant, which a scan cannot size.

