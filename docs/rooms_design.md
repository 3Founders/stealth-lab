# Rooms: persistent, local-first, end-to-end encrypted chat for people and agents (design, 2026-10-28)

Status: P1 (history kept on the device) is built in `routing_kell_frontend/lib/delegator/store.ts`. Everything else here is the contract for the next phases. Frontend session owns the web side; the backend lane owns `backend/` (board request below).

## What it is

A room is an append-only log of encrypted events shared by people and agents. The browser keeps the full decrypted history (IndexedDB, encrypted at rest) and works offline; the server relays ciphertext between devices and agents. You can export everything and delete it. This is the local-first model (Kleppmann et al., "Local-first software: you own your data, in spite of the cloud") with Signal-style encryption.

## Two kinds of room

| | Private (default) | Agent-readable (explicit opt-in per room) |
|---|---|---|
| Server stores | ciphertext only | ciphertext, with the room key also sealed to a server key |
| Who can join | people; agents that hold a key (local `kell agent` bridge) | the same, plus claude.ai / ChatGPT remote connectors and keळ-hosted agents |
| Label in the UI | "End-to-end encrypted" | "Not end-to-end encrypted: keळ can read this room" |
| History on switching | starts a new key epoch: earlier private history stays private | |

Organization rooms are agent-readable by default and say so (org audit, retention and erasure in `org_governance.py` assume the org can read its data). **Needs the founder's confirmation.**

## Crypto (WebCrypto only)

- Identity: per person, ECDH P-256 (wrap keys) and ECDSA P-256 (sign events). Public keys are registered on the server; the private key is wrapped with a passphrase-derived key (Argon2id, `lib/sync-crypto.ts`) and the wrapped blob is stored server-side for new devices. Losing the passphrase loses history on a new device.
- Room key: AES-GCM-256 per epoch, wrapped separately to every member's ECDH public key (ECDH + HKDF + AES-GCM key wrap).
- Events: AES-GCM with AAD `roomId|epoch|eventId`; the sender signs the ciphertext envelope (ECDSA) so members can verify who sent it.
- Removing a member, revoking an invite link, or switching mode starts a new epoch. Not MLS: no per-message forward secrecy.
- Invite link: `/room/join?id=<inviteId>#<key material>`. The fragment is never sent to a server. Treat the link as a bearer key until revoked.

## Event envelope (plaintext inside the ciphertext)

```
{ v: 1, kind: "msg" | "edit" | "delete" | "react" | "rename" | "task" | "task_update" | "note",
  id, ts, lamport, text?, reply_to?, target?, data? }
```
Edits, deletes, reactions, renames and task updates reference a `target` id; last writer wins by (lamport, sender). Nothing is edited in place, so merge is a union by `id`.

## Server-visible metadata (state it in the UI)

room id, member ids, sender id and kind (`person` | `agent`), epoch, time, size, `mentions` (agent ids, so an offline agent can be woken), `depth` (agent hops). Not the text.

## API contract for the backend lane (`/v1/rooms`, website-session token, same CORS list as chat; agents via MCP)

```
PUT    /v1/rooms/keys                      {device_id, ecdh_public_jwk, ecdsa_public_jwk, wrapped_private?, kdf?}
GET    /v1/rooms/keys/{sub}                public keys of someone who shares a room or org with the caller
POST   /v1/rooms                           {id, title_ct, mode, org_id?, epoch:1, wrapped_keys:[{member, wrapped}]}
GET    /v1/rooms                           rooms the caller is in, with their wrapped key per epoch
DELETE /v1/rooms/{id}                      owner only
POST   /v1/rooms/{id}/members              {member | agent_id, role, epoch, wrapped}
DELETE /v1/rooms/{id}/members/{who}        body carries new-epoch wrapped keys for everyone who remains
POST   /v1/rooms/{id}/invites              {max_uses, expires_in}  -> {invite_id}   (the key never reaches the server)
POST   /v1/rooms/join                      {invite_id}  -> membership
POST   /v1/rooms/{id}/events               {events:[{id, epoch, kind, nonce, ciphertext, sig, lamport, sender, mentions, depth}]}
                                           -> {accepted:[{id, seq}], rejected:[{id, reason}]}   (idempotent by id)
GET    /v1/rooms/{id}/events?after=seq     -> {events, next}
GET    /v1/rooms/{id}/stream?after=seq     SSE (end with a cursor like /v1/delegate/events)
```
MCP tools (agent-readable rooms return plaintext; private rooms return the envelope only): `room_list`, `room_read(room, after)`, `room_post(room, text, mentions)`, `room_members(room)`. Hop rule: an agent-sent event carries `depth`; the server refuses depth > 4 and a person's event resets it to 0 (`.scratch/prompts/delegator_pull_brief.md`). `delegate_inbox` / `delegate_result` keep working for existing rooms.

Tables (migration 157; 156 is taken by the backend wallet migration): `rooms`, `room_members` (person or agent, role, wrapped key per epoch), `room_events` (per-room `seq`, unique `id`), `user_keys`, `room_invites` (hashed secret, expiry, uses, revoked). RLS on, only the backend touches them (same pattern as `155_delegate_relay.sql`). Retention is the room owner's setting, default none for private rooms.

## Phases

P1 local history (done, frontend) · P2 crypto library (frontend) · P3 rooms API (backend lane) · P4 sync engine, people, invites (frontend) · P5 MCP room tools, agent-readable mode, `kell agent` key-holding bridge (backend + packaging) · P6 hosted agent runner that calls MCP tools (backend).

## Open issues and honest limits

- Private rooms are unreadable to hosted Claude/ChatGPT connectors. That is the cost of end-to-end encryption.
- Repo rule: new free-text columns on a trace path go through `redact_event`. Ciphertext cannot be redacted server-side; agent-readable rooms can. Needs a board note.
- Keys live in the browser; an XSS bug on the site would expose them. A strict CSP review is part of P4.
- The MCP service runs one worker and its SSE polls the database; fine for a pilot, not for fan-out.
- Handles we mint must not contain Claude, Codex or GPT.
