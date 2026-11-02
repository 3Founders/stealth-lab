# Rooms: persistent, local-first chat for people and agents (design, rev 2, 2026-10-28)

Status: P1 (history on the device), P3 (rooms API), P4 (sync) and P5 (the room MCP tools) are built; see the phase table. The rest is the contract for the next phases. The frontend session owns the web side; the backend lane owns `backend/`.

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

A message may carry fenced ` ```html ` blocks (a slide deck or a small page). The site turns each into an artifact card and renders it only on a click, in a sandboxed iframe (`lib/chat/artifacts.ts`, `components/chat/MessageText.tsx`); the block is part of `body`, so it is capped at 8,000 characters like any message.

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
| P3 | Open-room API and tables (migration 157, `/v1/rooms`) | built, 13 e2e tests on a real Postgres |
| P4 | Sync engine (`lib/delegator/sync.ts`): local outbox, poll-based pull, multi-device, invite links, member list, unread counts | built, verified with two browser contexts against a local server. Not built: SSE (it polls every 2.5-5 s), edits/deletes/reactions in the UI, typing and presence, push notifications |
| P5 | MCP room tools so connectors are real participants; hop rules | built (`room_list`, `room_read`, `room_post`, `room_members` in `server.py`, store in `services/rooms.py`; visible in the progressive core so Claude/ChatGPT can join without a discovery step) |
| P6 | keळ-hosted agents that call MCP tools (open rooms only; call_model under org policy and budgets; tool calls that need approval reuse `needs_approval`) | first slice built: the built-in **@kell** room agent (`POST /v1/rooms/{id}/agent`, `services/room_agent.py`) - server-side, hosted models via the caller's own `/v1/chat/completions`, router-chosen tools through the same gateway/consent/audit as `use_tool`, reply posted into the room as an agent message. `KELL_API_BASE` (when the API app is not the MCP origin) and `KELL_ROOM_AGENT_MODEL` (default `glm-5.3-flash`). Remaining: multiple hosted agents, org budgets, per-agent approval. |
| P7 | Private rooms on ts-mls, with an independent review before they are called secure | later |

## Next session (scoped 2026-11-02): E2EE rooms, parity addressing, encrypted store-and-forward

Founder direction: rooms end-to-end encrypted by default; **the same addressing model for people and agents** - `@alice` (person),
`@alice/claude-code` (Alice's agent), `@bob/opencode` (Bob's agent) are equal members of the same room; and a **reliable encrypted
store-and-forward** so an intermittently connected local agent is never a 504: a message waits in an encrypted inbox and is taken the
moment the agent wakes. Storage (retention, quota, export, deletion) is part of it. None of this is built yet; the plan below is the
next session's work.

**Decision to settle first (the hard one).** claude.ai and ChatGPT cannot hold MLS keys; their model is a server-side connector. Two
honest shapes:

- **A (recommended): E2EE rooms exclude hosted connectors.** Claude Code and other local agents join through a key-holding bridge
  (`kell` CLI / MCP bridge, keys never leave the machine). Hosted connectors stay in open rooms. The room label states which kind it
  is, and the current label ("kell can read this room") remains true for open rooms.
- **B: per-room "connector gateway" member** that decrypts for claude.ai/ChatGPT. Useful, but that room is not end-to-end encrypted
  in the strict sense and must be labelled exactly so (a gateway is inside the trust boundary). Only if A blocks a real use case.

**Work order (each item lands with its exit test).**

1. **Addressing/identity (migration after 167).** `room_members` gains `address` (`@name` or `@owner/agent`), `kind` (person|agent),
   `owner_sub`; unique `(room_id, address)`; events carry `sender_address` alongside the existing sender fields; backfill people as
   `@<their handle>`. Exit: a person and two agents are members of one room with the same row shape and can be addressed and mentioned
   identically; existing rooms and history still read.
2. **MLS group layer (ts-mls; not home-grown).** Per-member key packages; join via Welcome; add/remove via Commit; the server stores
   only opaque blobs (key packages, Welcome, Commit, application data) and never a room key; a removed member cannot read later events.
   Exit: e2e with 2 people + 2 agents on a real Postgres; removal test; the security-review checklist is written before any "secure"
   wording ships (ts-mls is not audited - say so).
3. **Room mode + truthful labels.** New rooms default to E2EE; an explicit open-room option (needed for hosted connectors); the badge,
   the docs and the API say which one a room is; existing rooms stay open until converted.
4. **Encrypted store-and-forward.** Per-recipient (per-room, per-member) ciphertext inbox with `seq`, idempotent envelope ids, ack/claim,
   TTL and quota; sent while the agent is down, taken on its next `room_read`/inbox call (cursor), never dependent on a live socket or a
   webhook; SSE stays a latency hint only. Exit: send while the laptop is closed; agent wakes after 20+ minutes and receives each message
   exactly once; no 504 on either side; the server cannot read the payload.
5. **Storage and retention.** Per-room retention (open and E2EE separately), per-account storage quota, deletion and export on the
   device and on the server; purge on membership removal; an audit trail of key/membership changes (times, no content).
6. **Artifacts in E2EE rooms.** The fenced ` ```html ` artifact travels inside ciphertext and renders exactly as today
   (`lib/chat/artifacts.ts`); no change to `MessageText`.
7. **Delivery/verification.** Docs (this file, `agent_joiner_kit.md`, `/docs#agents`, `/connect`), the joiner prompt, and a threat-model
   note; e2e: two browsers + a local agent, offline window, member removal, export/import.

**Out of scope next session:** server-side search of E2EE content, cross-organization connector federation, agents with several owners,
and any re-encryption guarantee beyond what MLS provides.

## Honest limits

- An open room is readable by keळ's server. That is the price of connectors working by default, and the UI says so on the room itself.
- Private rooms will not work with claude.ai or ChatGPT connectors. They can work with a local key-holding agent.
- ts-mls has not been audited. Until it has been reviewed, "end-to-end encrypted" in the product must be worded as "built on MLS, not yet independently audited".
- Device history is per browser until sync (P4) lands. Losing the device without sync loses it.
- The MCP service runs one worker and its SSE polls the database; fine for a pilot, not for fan-out.
- Handles we mint must not contain Claude, Codex or GPT.
