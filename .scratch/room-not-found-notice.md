## The "was not found on your account" message — what is actually true

Investigated 2026-11-26 against the real rooms, after the message appeared again.

### The rooms that exist for this Way

| room | title | length | way | deleted |
|---|---|---|---|---|
| `01a208c2` | `#create-an-audited-academic-research-talk-with-slides-and` | 57 | **yes** | no |
| `01a2097a` | `...-slides-and-2` | 59 | no | yes (hidden by me) |
| `01a2097e` | `...-slides-and` | 57 | no | yes (hidden by me) |
| `01a21683` | `...-slides-and-2` | 59 | no | no |

### What the message means

`onRooms` (RoomView.tsx:677) fires it for every label the device holds that the server no longer lists:

    for (const r of c.removed)
      push(room, `${r} was not found on your account. Your copy here is kept and will sync again.`)

`removed` is filled in `reconcile` (sync.ts:264) when a label has a mapped server id and that id is gone from the
server's list. The copy is deliberately never erased, so history survives a redeploy.

### Why it fires here

The device holds a local room whose label is not among the server's live rooms. The server lists
`...-slides-and` (the Way's room) but not `...-slides-and-2`, and the device still has a `-2` room from the
earlier restore/hide sequence. So the notice is **correct**: that local copy has no server room behind it.

It is not a sync defect. It is the residue of rooms that were created and hidden:

1. `01a208c2` was soft-deleted on 23 Nov.
2. Two rooms were made in its place (`01a2097e`, `01a2097a`), both `way=None`, both empty.
3. I restored `01a208c2` and hid those two.
4. `01a21683` (`...-slides-and-2`, `way=None`) exists live and was not made by my restore.

### Checked and ruled out

- `reconcile` DOES adopt a server room whose title matches a local label that has no id (sync.ts:273); a test with
  and without the change confirmed the existing code already re-links the ordinary delete-then-restore case.
  An edit I made to sync.ts for this was reverted: it could not be shown to change any behaviour, so shipping it
  would have been a guess.
- `create_room` returns the existing live room for a title (rooms.py:151), so it does not duplicate on a retry.

### Still unexplained, and honestly so

`01a21683` was not created by me, and I cannot see the browser's local store from here to know which label it holds.
The next step that would settle it: look at the device's `roomMap` in IndexedDB on a signed-in browser.

### What this means for the user now

The restored execution room is live and named correctly, and the notice is about a stale local copy, not the Way
room. Clearing it means removing the stale local room once (the notice itself is not destructive).
