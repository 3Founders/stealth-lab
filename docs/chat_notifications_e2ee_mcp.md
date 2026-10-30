# Chat: notifications, end-to-end encryption, and connecting other MCPs

Status 2026-10-30. Part 1 is built for an open tab (and a tab in the background). Parts 2 and 3 are designs; nothing in them is built. Where a statement comes from research it says so; where the research was thin it says that too.

## 1. Notifications

### What the research found

The searches were limited. Authoritative pages on Slack's and Signal's notification rules did not come up, so the rules below are the common ones that several apps' help pages agree on, not a copy of any one product.

- **Per-conversation levels with a mention exception.** Apps let a person mute a chat but still be alerted when they are mentioned, or mute everything except personal mentions ([example](https://www.trykondo.com/docs/desktop/notifications), [another](https://status.app/help/profile/customize-group-chat-notifications)).
- **Quiet hours silence alerts, not counts.** Alerts pause on a schedule while the badge keeps counting ([example](https://help.chatdaddy.tech/article/mobile-app-how-to-mute-a-channel-set-quiet-hours)). Teams has the same idea ([guide](https://helpdesk.fau.edu/TDClient/2061/Portal/KB/PrintArticle?ID=157275)).
- **Muting is per device in some apps.** Worth deciding for multi-device (see "Not built").
- **Web Push on iPhone** ([overview](https://edana.ch/en/2026/03/19/push-notifications-on-web-applications-pwa-is-it-really-reliable-on-ios-and-android/), [limits](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)): needs iOS 16.4 or later, and only for a site added to the Home Screen; permission must be asked from a tap; the service worker must show a notification for every push, or Safari revokes the permission; delivery on iOS is reported as less reliable than on Android. One source says push is unavailable in the EU on iOS 17 and later, another does not mention it, so treat the EU as unconfirmed.
- **Chrome, Edge and Firefox need a service worker** to receive push. Safari 18.4 adds declarative push that needs none, but a service worker still works everywhere.
- Not found in the searches: VAPID key handling and the Badging API details. Those are standard (VAPID is the identification scheme of Web Push; the app badge is `navigator.setAppBadge`) but are stated here from general knowledge, not from a source.

### What is built (routing_kell_frontend, `lib/chat/notify.ts`, `components/chat/NotificationBell.tsx`)

A bell ("Alerts") in the room header with a list of recent notifications and the settings. A notification is raised for:

| Kind | When |
|---|---|
| Mention | A message addressed to you (`@you`). |
| Message | A new message from another person in a room you are not looking at, or while the tab is in the background. |
| Agent | A new message from an agent in the same situations. |
| Task | An agent you sent a task to finished it, or could not. |
| Approval | A task is waiting for your approval. |
| Room | You were added to a room (not on the first sync, which lists rooms you already had). |

Delivery, in order of strength: the bell and its count; the tab title `(3) Chat`; the app badge where the browser has one (`setAppBadge`); a short chime made in the browser; and an operating-system alert when the tab is hidden. OS alerts are one per room (a new one replaces the old), and clicking one opens the room.

Rules (all tested, `tests/chat-notify.test.ts`): never your own messages; never the room you are looking at in a visible tab; each room has a level (Everything, Mentions and results, Mute which still lets a direct mention through); approvals and mentions outrank the room level; quiet hours (including across midnight) silence sound and OS alerts but not the bell; previews can be switched off so an alert says only "New message".

Privacy: the notification list is kept in memory only and is never written to disk, because it holds message text and the saved history is encrypted. Only the switches and room levels are saved (`localStorage`, no message text).

### Not built, and what it needs

- **Alerts with the browser closed (Web Push).** Everything above works only while keळ is open in some tab. A closed browser needs: a service worker (`/sw.js`) that shows a notification for every push; a `push_subscriptions` table (subject, endpoint, keys, device, created); `POST/DELETE /v1/me/push` and a public VAPID key endpoint; a sender (a `pywebpush`-style library) called when a room event is appended, for members who are not currently reading; VAPID keys as server secrets; and a "Home Screen" prompt on iPhone. The push payload should carry the room and a count only, not message text, by default, because it passes through Apple's and Google's push services. Size: about three to four days plus a deploy and a test on real phones. Needs a migration and a redeploy.
- **Per-device versus per-account mute.** Today levels are saved in this browser. Syncing them to the account is a small server addition.
- **Mentions of you by other people.** The server gives a mention as a name. The bell recognises `@you`; matching a person's real account handle needs the server to tell the page what handle you have.
- **Email for things that happen while you are away** (a daily digest) and notifications for billing, invites and system events. Not built.
- **Reaction, edit and delete events** are not shown in rooms yet, so they raise nothing.

## 2. End-to-end encryption, where keळ can read it

Taken literally those two are opposites: if the server can read it, it is not end-to-end. What can be built is **end-to-end encryption in which keळ is a member of the room**, named in the member list, holding the room's keys because a person added it. Then the storage and the network see only ciphertext; keळ reads because it is a participant, the same as a person who was added.

### Design

- Use MLS (RFC 9420), as the design in `docs/rooms_design.md` already intends for private rooms, with the `ts-mls` library. Every member (person, agent, or keळ itself) is an MLS client with its own keys. The server only stores and forwards ciphertext and public key material ([overview of this architecture](https://margelo.com/blog/building-e2e-encrypted-group-messaging-with-openmls)).
- **"Let keळ read this room" is an explicit switch per room.** Turning it on adds a keळ client to the group; turning it off removes it, which starts a new key epoch so later messages are unreadable to it. History it already read, it has read. This is the honest way to get "private, but keळ can help".
- A model assistant in an encrypted room works the same way: a client that decrypts and then sends the plaintext to a model provider. So an encrypted room does not keep content from the model provider the person chose. The UI has to say that.
- **Claude and ChatGPT connectors cannot be members of an encrypted room.** They reach keळ over the MCP server with OAuth, so any decryption for them would happen on keळ's side, which is again "keळ reads". That is allowed only through the explicit switch above. A private room with no keळ client is invisible to them.
- Research note: the search turned up the general MLS architecture and examples of servers that refuse to read content; it did **not** confirm how Matrix bots join encrypted rooms, so nothing here relies on that.

### What it costs and the honest limits

- Needs: key packages and welcome messages (server tables for the delivery service), per-device keys, multi-device (each device is a member), key backup or loss handling, member add and remove with epoch changes, and a decision on search and unread counts (the server can no longer compute previews).
- `ts-mls` has not been independently audited. Until it has been reviewed, the product may say "built on MLS", not "secure" ([rooms_design.md](rooms_design.md), Honest limits).
- Realistic size: several weeks of work plus an outside review. Not started. Open rooms (readable by keळ, redacted at the edge) stay the default until then, and say so on the room.

## 3. Connecting other MCPs

Two different things are meant by this; they are separate pieces of work.

### A. Other agents and connectors join rooms (rooms as an MCP server)

This is phase P5 in `docs/rooms_design.md`. Add MCP tools to the keळ MCP server: `room_list`, `room_read(room, after)`, `room_post(room, text)`, and a way to be told about new messages. The MCP specification supports servers telling clients that a resource changed (`notifications/resources/updated`, with `resources/subscribe`); the newest draft replaces the subscribe pair with one long-lived `subscriptions/listen` stream ([draft resources page](https://modelcontextprotocol.io/specification/draft/server/resources.md), [changelog](https://modelcontextprotocol.net/specification/2026-07-28/changelog.md)). That draft may not be final, so build on the stable 2025-11-25 behaviour and keep the transport behind one function. Rules to build in:

- Auth is the same OAuth as the rest of the MCP server, scoped to rooms the person belongs to; an agent only sees rooms it was added to.
- The hop limit already used in-tab (agents hand off at most four times before a person steps in) applies to MCP posts too, and each post is marked with the agent that made it.
- Tools cost context on every turn, so keep the tool set small and let `discover_tools` expose the rest, as the kell server already does.
- Messages from rooms are untrusted text to an agent. A room message must never be treated as an instruction to the connector's user.

Size: about a week for the tools, auth scoping and tests; needs a redeploy.

### B. Rooms use other MCP servers as tools (keळ as an MCP client)

For an agent in a room to call, say, a person's own MCP server, the agent needs an MCP client. The search did not turn up anything specific on connecting several MCP servers to one chat agent, so this is a design, not a survey of what others do.

- A **Connections** list per person: server URL, how to authenticate, and which rooms may use it. Stored server-side with the secret encrypted, like the org provider keys.
- Calls go through the same policy as `call_model`: allow-lists, spend caps, the kill switch, and the ledger. Tools that change things reuse the existing `needs_approval` status, so a person approves before they run.
- The server connecting out must use the same URL guard as provider endpoints (`backend/app/providers/url_guard.py`) so a room cannot be used to reach internal addresses.
- Only the tool names and short descriptions are loaded up front; the full schema is fetched when a tool is chosen, to keep context small.

Size: about two weeks including the connections UI, the guard and approval flow. Needs a migration and a redeploy.

### Suggested order

1. Web Push (closed-browser alerts), because notifications are not "done" without it.
2. `room_*` MCP tools, so Claude and ChatGPT can join the open rooms that exist today.
3. Connections to other MCP servers.
4. Encrypted rooms with the "let keळ read" switch, after an outside review plan is agreed.
