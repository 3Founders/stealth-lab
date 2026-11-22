# The panel switch: moving Submit a Way into the desktop app

**Status:** website done and shipped; desktop in progress.
**Written:** 2026-11-18, after the desktop build failed for the third time in a row.

This note exists because the same mistake was made repeatedly, and the record of it is worth more than the code.

## What was asked

The desktop app's **Submit a Way** button switched to the Rooms tab and loaded `?panel=submit` in the website's
view. It looked like nothing happened, because the request was issued before that view existed and every later load
dropped the parameter. The ask: the panel should be the app's own, in the right sidebar, the same one the rooms use.

## What is true now

| Piece | Where | State |
|---|---|---|
| The panel itself | `routing_kell_frontend/components/submit/SubmitWay.tsx` | shipped, 705 tests |
| A step's locator (Link / Script / Tool) | `components/submit/LocatorField.tsx` | shipped |
| Step headings, step removal, live suggestions | `SubmitWay.tsx` + backend | shipped |
| Desktop: the panel, ported | `desktop/components/SubmitWayPanel.tsx` | committed |
| Desktop: the API it calls | `desktop/lib/submissions-api.ts` | **was never committed** — this is what broke the build |
| Desktop: build | `next build` | **never passed** |

## The rule this broke, and the rule to follow

**`tsc --noEmit` does not check the client/server boundary.** A Next.js client component may not import a module that
reaches `node:fs`, `node:os` or `node:path`. `tsc` resolves the types happily; webpack is what refuses, and only
`next build` runs webpack.

The failure looks like this:

```
Module build failed: UnhandledSchemeError: Reading from "node:fs" is not handled by plugins (Unhandled scheme).
Module build failed: UnhandledSchemeError: Reading from "node:os" is not handled by plugins (Unhandled scheme).
Module build failed: UnhandledSchemeError: Reading from "node:path" is not handled by plugins (Unhandled scheme).
> Build failed because of webpack errors
```

**So: for anything under `desktop/`, `next build` is the gate, not `tsc`.** A green `tsc` on the desktop app proves
almost nothing.

## The three-way split that fixes it

The desktop panel needs three different things from one "API", and they must not live in one file:

1. **`lib/submissions-shared.ts`** — types plus the two pure helpers (`STATUS_LABEL`, `runnable`). Imports nothing from
   `node:*`. Safe for the browser. Both other files import from here, so a type exists once.
2. **`lib/submissions-api.ts`** — the real calls, through `lib/mcp.ts` (which reads the sign-in from disk). **Server
   only.** Importing this from a client component is the bug.
3. **`lib/submissions-client.ts`** — the same shape, each call POSTing to `app/api/submissions`. This is what the page
   imports.

`import type { … } from "…"` is erased at build time and is safe across the boundary; importing a *value* is not. That
distinction is why `STATUS_LABEL` could not stay in `submissions-api.ts`.

## A second failure, unrelated but found the same day

`find_or_create_goal` (backend, `app/services/goals.py:489`) passed Python lists and dicts into `$5::jsonb … $8::jsonb`.
asyncpg needs JSON text for that cast, so **every** Way submission failed with

```
invalid input for query argument $5: [] (expected str, got list)
```

The same mistake was in `app/submissions/store.py` in three more places. Fixed by `json.dumps(...)` at each, with
`tests/test_goal_insert_jsonb_offline.py` written first so the test was watched failing before the fix.

The lesson is smaller and nastier: nothing offline covered `find_or_create_goal`, so the whole Submit a Way path could
be broken with every test green.

## How to build the desktop app, honestly

```powershell
cd desktop
npx tsc --noEmit          # types
npx next build --no-lint  # THE GATE: catches client/server leaks. Never skip this.
npm run dist              # ~25 min: next build, electron tsc, packaging, NSIS
```

Then run `release\kell-Setup-<version>.exe`, or `release\win-unpacked\kell.exe` to try it without installing.

**Check the version in Help before believing a fix is present.** Several hours of this session were lost to testing an
old binary: the installed copy was 0.1.31 while the fixed source sat on disk, and "Check for updates" correctly said
there was nothing new, because nothing had been published.

## Why the build takes ~25 minutes

`electron-builder` copies all of `node_modules` as loose files because `asar: false`, then compresses a ~310 MB NSIS
installer, then hashes it again for the block map. Turning `asar` on (only `vendor/**` needs unpacking) is the single
largest win available and is not yet done.
