# Rooms: persistent, local-first chat for people and agents (design, rev 2, 2026-10-28)

Status: P1 (history kept on the device) is built in `routing_kell_frontend/lib/delegator/store.ts`. The rest is the contract for the next phases. The frontend session owns the web side; the backend lane owns `backend/`.

**Rev 2 changes the default.** The founder's rule: people who use rooms assume that Claude, ChatGPT and other connectors work in them. So the default room is an **open room**: connectors can read and post, and keळ's server can read it. End-to-end encryption is an opt-in **private room**, built on a vetted standard (MLS) and not on code we wrote. Rev 1 had this the other way round.

## What it is

A room is an append-only log of events shared by people and agents. The browser keeps the full history (IndexedDB, encrypted at rest with a non-extractable device key) and works offline; the server stores the log and relays it between devices and agents. You can export everything and delete it. This is the local-first model (Kleppmann et al., "Local-first software: you own your data, in spite of the cloud"): the device is a full copy, the server is a sync peer.

## Two kinds of room

| | **Open room (default)** | **Private room (opt-in, later)** |
|---|---|---|
| Who can join | people; Claude, ChatGPT, Cursor and other MCP connectors; keळ-hosted agents | people; agents that hold a key (a local `kell agent` bridge) |
| Server can read it | yes (text is redacted like relay tasks, see `delegate.py`) | no, only ciphertext |
| Encryption | TLS in transit, encrypted at rest on the server; encrypted at rest on each device | end-to-end, MLS (RFC 9420) |
| Label the UI must show | "Claude, ChatGPT and other connectors can join. keळ can read this room. It is not end-to-end encrypted." | "End-to-end encrypted. Connectors that run on someone else's server can't read it." |

The default room must say plainly that it is not end-to-end encrypted, because the word "private" or a lock icon would be false there. Organization rooms are open rooms.

## Libraries checked (2026-10-28, from each project's own repository)

| Library | Licence | Verdict |
|---|---|---|
| **libsignal** (Signal) | **AGPL-3.0**. README: "Use outside of Signal is unsupported." Targets: Java, Swift and a Node add-on (`@signalapp/libsignal-client`). No browser or WebAssembly build is documented. | **Don't use.** It is Node-only, unsupported for outside use, and AGPL would pull our own code into its terms. Also, Signal's pairwise Double Ratchet isn't the right shape for large rooms with agents. |
| **ts-mls** (MLS, RFC 9420, TypeScript) | MIT. Runs in browsers and Node 20+. Cipher suites X25519 / P-256 / P-384 / P-521, optional post-quantum. 111 stars, 647 commits. README: **"has not undergone a formal security audit"** and "aims to be a full implementation" (remove proposals and external joiners not confirmed on the README). | **Use for private rooms**, as the group key agreement, instead of our own. MLS is the IETF standard for group messaging and handles member add and remove with forward secrecy. Needs an independent review before we call a room secure, and a check that remove and join paths work. |
| **Automerge** (Kleppmann's CRDT library) | MIT. Rust core compiled to WebAssembly with a JavaScript API; `automerge-repo` adds IndexedDB storage and sync. README mentions **no encryption**. | **Optional, not needed for messages.** A chat is an append-only log, which merges by union without a CRDT. Consider Automerge only for shared editable documents inside a room (notes, a shared task list), where it earns its WebAssembly weight. |
| openmls (Rust MLS) | MIT | Fallback if ts-mls proves too immature; would need a WebAssembly build. |
| Our own `lib/rooms/crypto.ts` (built in P2, rev 1) | ours | **Retired from the plan.** It is tested, but hand-written group key handling is exactly what should not be shipped without review. It stays in the repo marked not for use until it is deleted or replaced by ts-mls. |

Kleppmann's own secure group messaging work (with Weidner) is research with no maintained production library that we found; MLS is the practical choice.

## Event envelope

```
{ v: 1, kind: "msg" | "edit" | "delete" | "react" | "rename" | "task" | "task_update" | "note",
  id, ts, lamport, text?, reply_to?, target?, data? }
```
Edits, deletes, reactions, renames and task updates reference a `target` id; last writer wins by (lamport, sender). Nothing is edited in place, so merge is a union by `id`.

In an open room the envelope is stored as JSON in `body` (`enc: "none"`). In a private room it is MLS application data in `ciphertext` (`enc: "mls"`). The row shape is the same.

## Server-visible metadata

Always: room id, member ids, sender id and kind (`person` | `agent`), time, size, `mentions` (agent ids, so an offline agent can be woken), `depth` (agent hops). In an open room also the text.

## API contract for the backend lane (`/v1/rooms`, website-session token, same CORS list as chat; agents via MCP)

Version 1 is open rooms only, which is the smaller piece: there is no key handling on the server.

```
POST   /v1/rooms                           {id, title, mode: "open", org_id?}
GET    /v1/rooms                           rooms the caller is in
PATCH  /v1/rooms/{id}                      {title}  (owner)
DELETE /v1/rooms/{id}                      owner only
POST   /v1/rooms/{id}/members              {member | agent_id, role}
DELETE /v1/rooms/{id}/members/{who}
POST   /v1/rooms/{id}/invites              {max_uses, expires_in} -> {invite_id}
POST   /v1/rooms/join                      {invite_id} -> membership
POST   /v1/rooms/{id}/events               {events:[{id, kind, body, lamport, mentions, depth}]}
                                           -> {accepted:[{id, seq}], rejected:[{id, reason}]}   (idempotent by id)
GET    /v1/rooms/{id}/events?after=seq     -> {events, next}
GET    /v1/rooms/{id}/stream?after=seq     SSE (end with a cursor, like /v1/delegate/events)
```
MCP tools on the existing connector (same OAuth as Claude and ChatGPT use today): `room_list`, `room_read(room, after)`, `room_post(room, text, mentions)`, `room_members(room)`. Hop rule: an agent-sent event carries `depth`; the server refuses depth > 4 and a person's event resets it to 0 (`.scratch/prompts/delegator_pull_brief.md`). `delegate_inbox` / `delegate_result` keep working for existing rooms.

Tables (migration **157**; 156 is the backend wallet migration; run `ls backend/db` after fetching before committing): `rooms`, `room_members` (person or agent, role), `room_events` (per-room `seq`, unique `id`, `body`, and a nullable `ciphertext` column that is excluded from the trace and redaction path), `room_invites` (hashed secret, expiry, uses, revoked). RLS on, backend-only, same pattern as `155_delegate_relay.sql`. Retention is the owner's setting.

Private rooms (a later version) add `mode: "private"`, `user_keys` and per-member key packages for MLS (key packages, welcomes and commits are stored and relayed as opaque blobs). Free-text `body` goes through `redact_value`; `ciphertext` must not (it would corrupt it).

## Phases

| # | What | Status |
|---|---|---|
| P1 | History kept on the device: encrypted IndexedDB, export, import, delete | built |
| P2 | (rev 1 crypto library) | retired; see above |
| P3 | Open-room API and tables (backend lane, migration 157) | requested |
| P4 | Sync engine against it (outbox, merge, SSE with poll fallback, multi-device), people, invite links, member list | next, frontend |
| P5 | MCP room tools so connectors are real participants; hop rules | backend |
| P6 | keळ-hosted agents that call MCP tools (open rooms only; call_model under org policy and budgets; tool calls that need approval reuse `needs_approval`) | backend |
| P7 | Private rooms on ts-mls, with an independent review before they are called secure | later |

## Honest limits

- An open room is readable by keळ's server. That is the price of connectors working by default, and the UI says so on the room itself.
- Private rooms will not work with claude.ai or ChatGPT connectors. They can work with a local key-holding agent.
- ts-mls has not been audited. Until it has been reviewed, "end-to-end encrypted" in the product must be worded as "built on MLS, not yet independently audited".
- Device history is per browser until sync (P4) lands. Losing the device without sync loses it.
- The MCP service runs one worker and its SSE polls the database; fine for a pilot, not for fan-out.
- Handles we mint must not contain Claude, Codex or GPT.
