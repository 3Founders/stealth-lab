# Desktop sessions in the rooms

Owner's ask (2026-11-14): from a room, call your desktop kell and get things done there. In a group room, call a
teammate's kell if they allowed you. `@kell …` is enough: kell finds the session the message is meant for. A
"Desktop sessions" button in the rooms lists your sessions, like a remote control.

## Pieces

| Where | File | What |
|---|---|---|
| tables | `backend/db/208_desktop_remote.sql` | devices, their sessions, grants, asks |
| service | `backend/app/services/desktop_remote.py` | heartbeat, grants, asks, approvals, permission replies, `route()` |
| HTTP | `backend/app/mcp_server/desktop_routes.py` | `/v1/desktop/*` (registered from `room_routes.register`) |
| @kell hand-off | `backend/app/mcp_server/room_agent_routes.py` `_hand_to_desktop` | runs before @kell answers |
| desktop worker | `desktop/lib/remote.ts` (started in `desktop/instrumentation.ts`) | reports sessions, long-polls asks, runs them |
| desktop switch | `desktop/app/api/remote/route.ts`, Account popover | "Let my rooms call this desktop" (on by default) |
| website | `routing_kell_frontend/components/chat/DesktopPanel.tsx`, `lib/chat/desktop-api.ts` | the panel and its API |

The desktop app's Rooms pane loads the website's `/chat`, so the panel and the hand-off work there too.

## Flow

1. While someone is signed in to the rooms, the desktop sends a heartbeat every 30 s: each session's title, its
   project **folder name** (never the full path), and a short summary of the latest answer. It never sends the
   conversation. A device is online while it has been heard from in the last 90 s.
2. `@kell <message>` in a room: `desktop_remote.route()` gathers the sessions the sender may reach (their own, plus
   those of teammates who granted them access *and are in this room*). With none, no model is called. Otherwise one
   cheap model call decides whether the message is meant for one of them, and it is handed over at confidence ≥ 0.6.
   `@chaitanya/kell …` weighs only Chaitanya's sessions.
3. Handed over: kell posts "Handed to …" in the room and creates an ask. The desktop claims it, runs it, streams
   the answer to the ask, and kell posts the finished answer in the room as `@<owner>/desktop`.
4. The matching desktop is offline: kell answers the message itself and says so first.

## Levels (per person, covering all the owner's sessions)

| level | runs in | may |
|---|---|---|
| `owner` (your own ask) | the session itself | everything; permission prompts come to you in the panel |
| `answer` | a copy of the session (fork) | read only: everything else is denied and every permission ask is refused |
| `approve` | a copy, after you approve it in the panel | full; its permission prompts come to you |
| `full` | a copy | full; its permission prompts come to you |

A teammate's ask always runs in a copy, so your own session and its context never change because of someone
else. The copy is titled "From <name>: …" and shows in your sidebar. Removing a grant cancels that teammate's
asks that haven't started yet.

## Not done / limits

- One ask runs at a time on a desktop, with a 20-minute limit. A permission prompt nobody answers within 10 minutes
  is refused.
- The read-only rules for `answer` (`READ_ONLY_RULES` in `lib/remote.ts`) assume the engine takes the last
  matching rule. They are backed up by refusing every permission ask, but haven't been checked against a live
  model turn.
- Every @kell message from someone with reachable desktop sessions costs one extra cheap model call (the routing
  decision).
