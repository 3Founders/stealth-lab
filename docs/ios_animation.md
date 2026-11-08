# iOS-style animation in kell's chat (reference, 2026-11-08)

What the "iOS feel" in the chat is made of, where each piece lives, the numbers behind it, and the rules that keep it cheap and accessible. Everything here was read from the source files named; nothing was run or measured for this note, and where I could not tell something it says so.

"iOS animation" in this repo means four things working together: **spring physics** (motion that keeps its speed when interrupted), **the Apple ease** (`cubic-bezier(.32, .72, 0, 1)`, a quick start and a long soft landing), **sheet motion** (panels and dialogs that slide in like iOS sheets), and **glass** (blurred translucent surfaces). The reference article for the spring model is cited in `lib/chat/spring.ts`: `blog.justinbuilds.mov/ios`.

Related research notes: `docs/UIeffects.md` (glass menus and their cost), `docs/blureffects.md` (blur techniques and rules). Parity work between chat copies: `docs/chat_parity_console_vs_routing_kell_frontend.md`.

## 1. Where it lives

Two apps carry the same chat motion, as copies, not a shared package.

| App | Files |
|---|---|
| Website `routing_kell_frontend/` (the reference) | `lib/chat/spring.ts`, `lib/chat/smooth.ts`, `components/chat/useSpringScroll.ts`, `components/chat/useSlideHighlight.ts`, `components/chat/SmoothText.tsx`, `components/chat/PanelShell.tsx`, `components/chat/SettingsDialog.tsx`, `components/PhotoPrompt.tsx`, `app/chat/chat.css`, `app/globals.css`, `app/chat/page.tsx` |
| Desktop `desktop/` | `lib/chat/spring.ts`, `lib/chat/smooth.ts`, `components/chat/useSpringScroll.ts`, `components/chat/SmoothText.tsx`, `app/chat.css`, `app/desktop.css`, `app/page.tsx`, `app/api/files/route.ts` (the file tree's data) |

Compared with line endings ignored: `spring.ts` and `SmoothText.tsx` are identical in both apps. `useSpringScroll.ts` differs only in comments and in how the wheel handler is written (the desktop copy was brought in line with the website's on 2026-11-08). The desktop has **no** `useSlideHighlight.ts`, `PanelShell.tsx` or dialogs, so the sliding menu highlight and the sheet panel exist only on the website. The desktop instead has its own sidebars, file tree and file view (section 10). `console/app/chat` is an older, smaller chat without most of this (see the parity doc).

## 2. Spring physics (`lib/chat/spring.ts`)

A spring is a damped harmonic oscillator: `m x'' = -k (x - target) - b x'`, with mass 1, `k = (2π / response)²` and `b = 2 · dampingRatio · √k`. Why springs: a timed ease restarts from zero speed when interrupted; a spring keeps its current velocity and bends toward the new target, so motion never stops dead.

### Presets (`SPRINGS`)

| Name | response (s) | damping | Use |
|---|---|---|---|
| `micro` | 0.2 | 0.85 | hovers and presses |
| `smooth` | 0.5 | 1 (critically damped, no overshoot) | entrances, scrolling |
| `snappy` | 0.5 | 0.85 | quick settle with slight overshoot |
| `bouncy` | 0.5 | 0.7 | pop-ins |

### Functions

| Function | Job |
|---|---|
| `coefficients(spec)` | `{k, b}` from response and damping. |
| `stepSpring(state, target, spec, dt)` | Advances `{x, v}` by `dt` in fixed 1/240 s substeps (semi-implicit Euler, stable at any frame rate). `dt` is capped at 0.1 s so a backgrounded tab cannot fire one giant step. |
| `settleTime(spec)` | Seconds until within 0.001 of range and nearly still, from rest at distance 1 (max 4 s). |
| `springCss(spec, points = 40)` | The spring as a CSS `linear()` timing function plus its duration in ms. A bouncy spring overshoots 1. |
| `springVars()` | CSS custom properties for every preset: `--dg-<name>` (timing function) and `--dg-<name>-d` (duration). |
| `rubberBand(x, d, c = 0.55)` | Overscroll curve `c·x·d / (d + c·x)`: slope `c` at the start, grows more slowly, never passes `d`. |
| `SpringValue` | One number chasing a target with spring physics. `retarget(to)` keeps velocity; `to` may be a function for a moving target; `cancel()` stops and drops velocity (the user took over); `set(x)` jumps; `tick(dt)` returns true at rest (within 0.4 px and 8 px/s). Runs on `requestAnimationFrame`. |

### How the CSS gets the springs

`app/chat/page.tsx` renders `<section className="frame dg-page" style={springVars()}>` (the desktop does the same on `<section className="dg-page dk-page">`). So the real spring curves are generated in JavaScript and handed to CSS as variables. `chat.css` line 3 defines **fallback** cubic-bezier values for the same names (`--dg-micro`, `--dg-smooth`, `--dg-bouncy`, each with `-d`), and an `@supports not (transition-timing-function: linear(0, 1))` block forces plain cubic-bezier eases in browsers without `linear()` (before 2023).

## 3. The Apple ease

`--dg-apple: cubic-bezier(.32, .72, 0, 1)` (chat.css, `.dg-page`). Called "the iOS sheet curve" in `app/globals.css`: quick start, long soft landing. `globals.css` also has `--ease: cubic-bezier(0.22, 1, 0.36, 1)`, used by the marketing pages (reveals, links, buttons); that one is a softer ease-out, not the sheet curve. Every use below falls back to the literal curve if the variable is missing (`var(--dg-apple, cubic-bezier(.32, .72, 0, 1))`).

## 4. Each effect, where it is, how it works

### 4.1 Spring scrolling (`components/chat/useSpringScroll.ts`)
Replaces `scroll-behavior: smooth` for the message viewport. Used in `RoomView.tsx` (`useSpringScroll(viewportRef, contentRef, room)`) and in the desktop `page.tsx` (`logRef`, `logContentRef`, `sessionID`).
- `scrollToEnd` uses a `SpringValue` on `SPRINGS.smooth`; calling it again mid-flight keeps velocity; the target is a function (`scrollHeight - clientHeight`) so it follows content that grows while it runs.
- `follow()` scrolls only if the reader was already at the bottom (`atEnd`, within 120 px).
- The reader taking over: wheel, touch, keys cancel the spring at once. Scrolling up by more than 1 px sets `atEnd` false even inside the 120 px zone, so the next streamed token does not pull the view back down (added 2026-11-08).
- The top and bottom ends are left to the browser (`overscroll-behavior: contain`). A custom wheel rubber-band used to be there and was removed because it fought trackpad momentum and made both ends jitter. `rubberBand` still exists in `spring.ts` and is tested, but this hook does not use it.
- Reduced motion: instant.

### 4.2 Sliding menu highlight (`components/chat/useSlideHighlight.ts`, website only)
One soft yellow shape slides from the old row to the new one instead of jumping, as menus on iOS and macOS do. The hook only measures: it sets `--hl-x`, `--hl-y`, `--hl-w`, `--hl-h`, `--hl-on` on the menu. CSS draws it (`.dg-slide::before` in chat.css) and the move is a `transform` transition of 240 ms on the Apple ease, plus a 140 ms opacity fade.
- First appearance snaps into place (`data-snap` for two frames) so it does not slide in from elsewhere.
- Follows the pointer (`pointerover`), focus (`focusin`), and the keyboard's row (`data-active="true"`, watched with a `MutationObserver`); leaving returns it to the keyboard's row or hides it.
- Usage: `<ul ref={slide('li[role="option"]')}>`. The same selector pair returns the same ref function each render so a re-render does not re-wire the menu.
- Used by: `Composer.tsx` (slash commands, @mentions), `HamburgerMenu.tsx`, `OnboardButton.tsx`, `SearchPalette.tsx`, `SettingsDialog.tsx`.

### 4.3 Streaming text (`lib/chat/smooth.ts`, `components/chat/SmoothText.tsx`)
Providers send text in bursts; showing each burst on arrival looks jerky. A "shown" position chases the received text at a steady reading pace.
- `MIN_CPS = 40` characters per second when little is waiting; `CATCH_UP = 6` (backlog cleared about six times a second, a ~170 ms time constant); `MAX_FRAME_MS = 100` so a stalled tab does not reveal everything in one frame.
- `revealStep(shown, target, dtMs)` never passes `target`. `visiblePrefix` never cuts an emoji in half.
- The newest words (`FADE_WINDOW = 28`) are their own spans that fade in once with `dg-tok-in` (340 ms, Apple ease, 3 px rise); older words fold into one plain string. With `markdown`, revealed words are drawn as formatted text so a reply looks the same while streaming and after it is posted.
- `Thinking` shows before any text arrives: the kell mark, three pulsing dots (`dg-wave`, 1.3 s) and a shimmering label. Entrance is `dg-glass-in` 420 ms.
- This is presentation only: the posted message is always the full text. With reduced motion, or in a background tab, it shows what has been received as is.
- Detail: chat.css defines `dg-tok-in` twice. The first (line ~648) animates `filter: blur(2px)` to 0; a later redefinition (line ~845) drops the blur and is the one that wins. So the shipped token fade is opacity plus a 3 px rise only, which also follows the "never animate blur" rule in section 6. I read the cascade order, I did not inspect it in a browser.

### 4.4 Sheet panel (`components/chat/PanelShell.tsx` plus chat.css, website only)
The right-hand canvas and docs panel slides in like an iOS sheet.
- The panel mounts first off to the right (`transform: translate3d(100%, 0, 0)`), then moves in on the next frame (two nested `requestAnimationFrame`s set `shown`). Transition: `transform .46s` and `box-shadow .46s`, both on the Apple ease. Open state adds a shadow `-18px 0 48px rgba(0,0,0,.16)`.
- The chat makes room by easing its own right edge with the same curve: `.dg-shell { transition: margin-right .46s ... }`, driven by the CSS variable `--dg-canvas-w` that PanelShell sets on `<html>`.
- On close it slides out and unmounts after 460 ms.
- Resizing: a hairline divider with drag (pointer capture) and arrow keys; minimum 300 px, leaves at least 360 px for the chat, default 460 px, width remembered in `localStorage` key `kell:canvas-width`. While dragging the width is written straight to the DOM in an animation frame (no React render), transitions are off (`data-dragging`, `.dg-resizing`), and a transparent shield covers the content so an embedded frame cannot swallow the pointer.
- `will-change: transform` is set on the panel.

### 4.5 Dialogs (settings, photo prompt)
- `.dg-settings` (chat.css) and `.photo-ask` (globals.css) open with `photo-in` (0.5 s, Apple ease: from `translateY(14px) scale(.94)` and opacity 0) and close with `photo-out` (0.24 to 0.26 s, `cubic-bezier(.4, 0, 1, 1)`). Closing sets `data-closing` first so it can animate out, then the dialog is really closed (`SettingsDialog.tsx`, `PhotoPrompt.tsx`).
- The backdrop fades in (`photo-fade` 0.35 s) with `backdrop-filter: blur(6px)` (settings: later raised to 10px with saturate 1.15).
- On a phone (max-width 640px) `.photo-ask` becomes a **bottom sheet**: it rises from the bottom edge (`photo-sheet-in`, `translateY(100%)` to 0, 0.55 s), has rounded top corners (20 px) and pads for the safe area (`env(safe-area-inset-bottom)`).

### 4.6 Menus, popovers and entrance keyframes
Small entrances, all transform plus opacity: `dg-pop` (scale .94 to 1, or translateY -6 and scale .96 for menus), `dg-rise` (8 px up), `dg-drop`, `dg-in`, `dg-fade`, `dg-glass-in` (8 px up, scale .98), `dg-head-in`, `dg-side-in`, `dg-pick-in`, `dg-doc-rise`, `dg-doc-slide`, `dg-doc-pop`, `dg-canvas-in`, `dg-signin-in`. Plus the "found message" highlight (`dg-found`, `dg-flash`), the kell mark glow (`dg-glow`), and `dg-breathe`.

### 4.7 Press and hover feedback
Buttons and chips use the `micro` spring for `transform`, `box-shadow` and background (`transition: transform var(--dg-micro-d) var(--dg-micro), ...`), a 1 px lift on hover, back to rest on `:active`. Elevation tokens: `--dg-lift` and `--dg-lift-hover`.

### 4.8 Icon morphs
The copy button's icon becomes a tick (success colour) or alert (critical colour) in place: three icons stacked, one visible, cross-faded with opacity and a small scale (`.7` to 1, 160 and 200 ms). The button never changes size (`.dg-act-copy`).

### 4.9 Toggle switch
`.dg-switch i` slides with a `.25s` transform transition (chat.css ~line 1101).

### 4.10 Layout transitions
On wide screens `.dg-shell` and `.dg-side` animate `grid-template-columns`, padding and border width with the `smooth` spring variables when the room list opens or closes; the `.dg-browser` panel animates width `.3s` on the Apple ease. On phones the room list is a drawer that slides over the chat, inputs are 16 px so iOS Safari does not zoom, and `layout.tsx` notes the Chat itself handles the iOS keyboard (Android resizes the page).

## 5. Glass (blurred translucent surfaces)

Menus, header, pop-overs, composer, mention lists, bell panel, search and dialogs share a recipe: translucent background (`--dg-glass` or `--dg-glass-strong`), `backdrop-filter: blur(12px to 16px) saturate(1.2 to 1.4)` (both `-webkit-` and standard), a hairline border (`--dg-edge`), and the lift shadow. The surfaces are small, which matters because backdrop blur costs about area times radius.

Fallbacks, all in chat.css:
- `@supports not (backdrop-filter ...)`: solid tint and stronger border; layout never depends on the blur existing.
- `@media (prefers-reduced-transparency: reduce)`: every glass surface becomes opaque (`background-color: var(--surface) !important`, backdrop-filter none); scrims go darker instead of blurred.

## 6. Rules (from the code comments and the research notes)

1. **Animate only `transform` and `opacity`** while anything moves. Anything else repaints every frame. (Exception in the code: panel `box-shadow` and `margin-right` transitions, which are deliberate and short.)
2. **Never animate a blur radius.** Fade pre-blurred layers instead (`docs/blureffects.md`). Scrim fade-ins animate opacity.
3. **Keep glass surfaces small**; cost is per sampled area, not per effect.
4. **Springs for things the user can interrupt** (scroll, panels while dragging); a plain ease is fine for fire-and-forget entrances.
5. **Drag writes straight to the DOM** in an animation frame, with transitions switched off for the duration.
6. **Reduced motion means none.** Repeated across chat.css: `@media (prefers-reduced-motion: reduce)` removes animations and transitions on `.dg-page *` (and `scroll-behavior: auto` on the viewport); scroll and streaming code checks `matchMedia("(prefers-reduced-motion: reduce)")` and goes instant. `globals.css` has the same blanket rule for the marketing pages.
7. **Idle costs nothing.** No loops run when nothing is visible; the scroll spring stops its frame loop at rest.

## 7. Tests

`routing_kell_frontend/tests/chat.test.ts` (spring motion): a critically damped spring never overshoots and a bouncy one does; results are frame-rate independent (same position at different frame rates); `SpringValue` retargeting; `springCss` output; `rubberBand` values (`rubberBand(0, 844) = 0`, slope about 0.55 at 1, never passes the view size, monotonic). `tests/chat-smooth.test.ts`: calm pace when little is waiting and never passes the received text; faster with a bigger backlog; a pause or stalled tab does not make the text jump; a backlog drains in under a second; never cuts an emoji in half. No tests were found for `useSpringScroll`, `useSlideHighlight` or `PanelShell`, and the desktop app has none for its copies. I did not run the suite.

## 8. Gaps and things to check

- **No visual check done.** None of the timings above was watched in a browser or the packaged app; they come from the source.
- **Two copies drift.** The website and desktop copies are manual; the desktop already lagged the website once (the rubber-band). Parity doc option 3 ("one source") would remove this.
- **Desktop lacks the sliding highlight and sheet panel**, since it has no menus or dialogs of that kind yet; it has its own sidebars, file tree and file view instead (section 10).
- **`SPRINGS.snappy`** is defined and has CSS variables but I found no use of `--dg-snappy` beyond the fallback block; check before relying on it.
- **Doc-only decisions still open** in `docs/blureffects.md`: whether to build a progressive-blur sticky header and BlurHash placeholders for attachments. Neither is built per the files I read.
- **`rubberBand`** is exported and tested but unused by the scroll hook; either use it again (carefully, trackpad momentum was the problem) or delete it.

## 9. Adding a new animated surface (checklist)

1. Pick the motion: interruptible or draggable means a `SpringValue`; a one-shot entrance means a keyframe on `--dg-apple`.
2. Transform and opacity only. Set `will-change: transform` only on a long-lived moving panel.
3. Use the existing variables (`--dg-micro`, `--dg-smooth`, `--dg-apple`) so the `linear()` fallback applies.
4. Add the `prefers-reduced-motion` and, if it blurs, `prefers-reduced-transparency` rules next to it.
5. Give a menu the sliding highlight with `useSlideHighlight` rather than a hover background.
6. If it is a copy used by the desktop app, change both `routing_kell_frontend` and `desktop` (or move to a shared package).

## 10. The desktop app's own surfaces (2026-11-08)

`desktop/app/desktop.css` carries what the website chat does not have: the two collapsible sidebars, the sidebar's file tree, the file view, the changes panel, the model steps, the tool cards and the activity list. Every motion there uses the same four tools as the chat — the `--dg-*` spring variables (set inline on `.dg-page` by `springVars()`), transform and opacity only, the `--dg-smooth` / `--dg-micro` / `--dg-bouncy` curves from `lib/chat/spring.ts`, and a reduced-motion switch.

| Surface | Effect | Motion |
|---|---|---|
| Sidebar collapse (left or right) | the column narrows to 0 and its contents fade; the divider and padding go at once | `grid-template-columns` transition on `--dg-smooth` (the spring's ~500 ms), `opacity` on the same curve, `visibility` flips at the end so the panel leaves the tab order |
| The buttons that bring a closed panel back (chat header) | pop in | `dg-pop` on `--dg-bouncy` (360 ms) |
| File view (right panel, opens from the tree) | slides in like the website's canvas panel | `dg-canvas-in` (translateX 22px) on `--dg-smooth` |
| File tree row / chevron | hover tint; the folder chevron turns 90° | `background-color` and `transform` on `--dg-micro` (160 ms) |
| Folds (`Changed by kell`, `Other changes`, `Model steps`, `Tools`, a file's diff, more sessions) | the disclosure opens and closes instead of jumping | `block-size` on `::details-content` with `interpolate-size: allow-keywords`, `--dg-smooth`; `content-visibility` flips with `allow-discrete` |
| Activity rows (what kell is doing) | each new call rises in, older ones dim by age | `dk-act-in` (8 px rise) on `--dg-smooth` with `backwards` fill, so the finished keyframe does not override the age `opacity`; the fade itself is an `opacity` transition on `--dg-smooth` |
| Activity mark | spinner while running, dot when done, red when failed | `dk-spin` .7 s linear while running; colours and `transform` on `--dg-micro` |
| Model step chips | rise in | `dg-rise` on `--dg-smooth` |
| Icon buttons (account, refresh, undo, close, help, window buttons, tree refresh) | hover tint, press scale | background, border, colour and `transform: scale(.94)` on `--dg-micro` |
| Help menu | drops in from the button | `dg-drop` on `--dg-micro`, `transform-origin: top right` |
| Account card | pops in | `dg-pop` on `--dg-bouncy`, `transform-origin: bottom left` |
| Sidebar drag handles | hairline appears on hover | `background-color` .15 s (the only non-spring timing left, and it is a 1 px line) |

Two bugs found while writing this (both fixed 2026-11-08):

- **The collapse used `display: none`.** A `display: none` grid item leaves the flow, so the chat auto-placed into the now-empty 0-width first column and the whole window collapsed. Each column is now placed explicitly (`grid-column`), and collapsing narrows the column instead of removing the element. This is the same reason the website's own room list uses `visibility`/width rather than `display` in `.dg-shell[data-panel="closed"]`.
- **`animation-fill-mode: both` overrode the age fade.** The activity row's entrance keyframe ends at `opacity: 1`, and with `both` that final value kept winning over `opacity: calc(1 - var(--age) * .22)`, so older calls never dimmed. `backwards` keeps the entrance and hands the property back afterwards.

Reduced motion: one block at the end of `desktop.css` removes every `dk-*` animation and transition, the `grid-template-columns` collapse, and the `::details-content` transition. `chat.css`'s own blanket rule covers the chat's selectors; it does not reach `dk-*`, which is why the desktop needs its own.

