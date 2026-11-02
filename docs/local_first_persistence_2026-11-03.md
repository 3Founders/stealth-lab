# kell chat: local-first persistence, encryption, and a copy on the user's PC

Decision document, 2026-11-03. Status: recommendation, not yet ruled on.

## Problem

Messages live in the browser's IndexedDB, encrypted with a non-extractable per-browser key. Rooms sync to the server, but the server copy is **not** end-to-end encrypted. Consequences:

- Clearing site data or losing a device loses anything not yet synced, and the per-browser key can never be moved to another browser, by design.
- The operator can read every room.
- Users want encryption, a copy on their own PC, and Kleppmann-style local-first behaviour.

## Research method and honesty notes

- Exa was not available. I used WebSearch plus the OpenAlex API through WebFetch.
- OpenAlex gave metadata only (title, year, citations, DOI, venue). I did **not** read the papers. Where I cite one below, I am relying on its title and venue.
- The fetching tool summarises pages with a small model. Treat paraphrases as secondary.
- The Ink & Switch essay page returned empty to my fetch. The seven ideals below come from Wikipedia (fetched) plus my memory of the essay's section titles. Re-verify against the essay before quoting.
- The recovery-key survey was thin. I fetched Signal's SVR post and Bitwarden's white paper. Standard Notes' help page did not describe key derivation or recovery. Notesnook's recovery key is known only from a search snippet.

### OpenAlex results actually fetched

| Query | Items worth citing (metadata only) |
|---|---|
| local-first software ownership collaboration | Kleppmann et al., "Local-first software: you own your data, in spite of the cloud", Onward! 2019, 114 citations, https://doi.org/10.1145/3359591.3359737 · MyWebstrates, UIST 2024, https://doi.org/10.1145/3654777.3676445 · LoRe, TOPLAS 2023, https://doi.org/10.1145/3633769 |
| end-to-end encrypted sync local-first | Nothing relevant. Top hits were BitTorrent Sync forensics and unrelated papers. This is a gap in the literature index, not proof that nothing exists. |
| passphrase derived key backup recovery key web | "SoK: Web Authentication and Recovery in the Age of End-to-End Encryption", PoPETs 2025, https://doi.org/10.56553/popets-2025-0113 · "SafetyPin: Encrypted Backups with Human-Memorable Secrets", 2020, https://doi.org/10.48550/arxiv.2010.06712 · "Keys to the Cloud: Formal Analysis and Concrete Attacks on Encrypted Web Storage", 2013, https://doi.org/10.1007/978-3-642-36830-1_7 |
| CRDT end-to-end encrypted collaboration | "Secure Conflict-free Replicated Data Types", 2021, https://doi.org/10.1145/3427796.3427831 · "Algebraic Replicated Data Types: Programming Secure Local-First Software", ECOOP 2023, https://doi.org/10.4230/lipics.ecoop.2023.14 · "Merkle-CRDTs", 2020, https://doi.org/10.48550/arxiv.2004.00107 |
| File System Access API OPFS persistent storage durability | Irrelevant hits. Only vaguely related: "Zeta-Lite: In-Browser SQL Database for Agentic Memory", 2026, https://doi.org/10.48550/arxiv.2609.01818 (not read). |
| Origin Private File System | Irrelevant (no usable academic results). Use the MDN pages below. |

### Web pages fetched

- MDN, storage quotas and eviction: https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- MDN, OPFS: https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system
- Signal, Secure Value Recovery: https://signal.org/blog/secure-value-recovery/
- Bitwarden security white paper: https://bitwarden.com/help/bitwarden-security-white-paper/
- Standard Notes, local encryption help: https://standardnotes.com/help/79
- Automerge intro: https://automerge.org/docs/hello/
- Wikipedia, local-first software: https://en.wikipedia.org/wiki/Local-first_software
- Search-only, not fetched: Ink & Switch Keyhive notebook (https://www.inkandswitch.com/keyhive/notebook/00/), Notesnook recovery (https://notesnook.com/help/recovering-your-account), Kleppmann's 2019 Onward! post (https://martin.kleppmann.com/2019/10/23/local-first-at-onward.html).

## Facts that drive the design

1. **IndexedDB and OPFS die together.** Both share the origin's quota and eviction rules. Everything is deleted together when the user clears site data (MDN). OPFS is private and invisible to the user, so it is **not** "a copy on their PC" in any sense they would recognise.
2. **`navigator.storage.persist()` helps, but only against eviction.** Firefox prompts. Chromium and Safari decide silently from engagement. Persistence does not survive the user choosing "clear data" or losing the device. Safari can also delete script-created data after 7 days without interaction when tracking prevention is on.
3. **A real folder needs the File System Access API.** `showDirectoryPicker()` gives a user-visible, permission-gated folder. From my memory, not MDN, it is Chromium-only. Firefox and Safari need a download/upload fallback.
4. **A non-extractable key cannot be backed up.** That is its purpose. Recovery therefore needs a second, exportable key path.
5. **Prior art on keys.**
   - Bitwarden: password stretched with PBKDF2 (600k iterations) or Argon2id into a master key. The master key wraps a random 512-bit user key. The server stores only the wrapped key. Lost password means lost vault, unless emergency access or account recovery was set up beforehand.
   - Signal SVR (technology preview at the time of the post): passphrase goes through Argon2. The server (SGX enclave) holds only a random half, released after limited guesses.
   - Both follow the same shape: **wrap a random data key under a user-held secret**.
6. **CRDTs and encryption combine.** Automerge is a CRDT library, network-agnostic and designed for local-first (automerge.org). Ink & Switch Keyhive/Beelay targets exactly this: a relay that sees only encrypted chunks, with access control carried by capabilities and group key agreement (BeeKEM). It is **pre-alpha** (search snippets only). Do not build on it now.

## The seven ideals against kell today

Names are from my memory of the essay, with Wikipedia's paraphrases. Re-verify.

| Ideal | kell today | Gap |
|---|---|---|
| 1. Fast ("no spinners") | Yes. Reads come from IndexedDB, so the UI is immediate. | None material. |
| 2. Not trapped on one device (multi-device) | Partial. Rooms sync through the server, but anything unsynced and the encryption key are stranded in one browser. | Local-only state and the key must be portable. |
| 3. Network optional (offline) | Mostly. Local reads work, though writes depend on the server being reachable. | Verify queued writes survive reload. Keep this if not already. |
| 4. Seamless collaboration | Yes, server-ordered edits with rebasing. This is server-authoritative OT-style, not a CRDT. | Acceptable. A CRDT is not required to meet the ideal. |
| 5. The Long Now (longevity) | No. If kell shuts down or the browser data is cleared, nothing durable remains in a format the user holds. | **Biggest gap.** Needs a user-held export in an open format. |
| 6. Security and privacy by default | Half. The local copy is encrypted. The server copy is plaintext to the operator. | Server-side E2EE. |
| 7. User ownership and control | No. The user cannot hold, move or delete their data independently. | Local folder copy plus E2EE keys. |

Kleppmann himself said at Local-First Conf 2024 that the seven ideals are not a good definition, and proposed: another computer's availability should never prevent you from working (search snippet, unverified). Note that criterion 5 above is the weakest one on his own looser test, too, because kell's server is still required for collaboration.

## Three designs, ranked

### A. Recovery-key encrypted backup plus a folder on the user's PC (recommended first)

**Idea.**
- Generate a random 256-bit **vault key** per account. Wrap it two ways. First, under a key derived from a user passphrase (Argon2id). Second, under a one-time printed **recovery key** (random, shown once, grouped for transcription).
- Store only the wrapped copies on the server.
- Replace the non-extractable per-browser key with this vault key. A browser may still cache it non-extractably after unlock.
- Encrypt every message and room blob client-side with the vault key before it leaves the browser.
- Offer "Back up to a folder": a user-chosen directory (File System Access API) receives an append-only, encrypted, open-format export (for example, one JSON-lines file per room, with a manifest). Fallback is a downloaded `.kellbackup` file.

**Threat model.**
- Protects against: the operator, a server breach, browser-data loss, device loss.
- Does not protect against: malware on an unlocked device, a user who loses both passphrase and recovery key, and traffic metadata (room membership, sizes, timing).

**Server can read.** Ciphertext, wrapped keys, room IDs, member lists, timestamps. **Cannot read:** message content.
- Caveat: server-side features that need plaintext break (search, agent features run by the server, link previews, moderation). The agent must then run client-side or receive content the client chooses to send. This is a real product cost to decide on explicitly.
- Shared rooms need a shared key. In A, keep it simple: the room key is the creator's key wrapped to each member (see B). If that is too much for step one, scope A to **personal and private rooms** first.

**Recovery.**
- Passphrase forgotten: use the recovery key.
- Both lost: data is unrecoverable, and the UI must say so at setup (as Bitwarden does).
- Offer to rotate the wrapped copies after recovery.

**Cost.** About 2 to 3 weeks for one engineer. Mostly client-side crypto (WebCrypto plus an Argon2id WASM build, since WebCrypto has no Argon2), a server table for wrapped keys, and the folder writer. No new infrastructure.

**First step.** In the chat client, add a key-hierarchy module: random vault key, Argon2id passphrase wrap, recovery-key wrap, unit-tested round-trip. Wire it to encrypt **one** table (private-room messages) behind a flag. Ship the manual "Export encrypted backup" file before the folder sync, since it needs no permissions and works in every browser.

### B. E2EE rooms with per-room keys wrapped by the recovery key

**Idea.**
- Each room gets a random room key. Each member gets it wrapped to their public key (an X25519 or RSA keypair, itself encrypted with the vault key from A).
- Adding a member wraps the key to them.
- Removing a member rotates the key for new messages. History already shared stays readable to the removed member, which is the same limitation as every E2EE messenger without forward-secrecy ratchets.
- Optionally move to Keyhive/BeeKEM later when it is stable.

**Threat model.** Same as A, plus protection from other members' accounts being compromised at the server. It still does not hide membership metadata.

**Server can read.** Same as A. It also sees who is wrapped to which room.

**Recovery.** Keypair recovery comes from A's recovery key. Lost recovery key means that user loses access to every room whose key was wrapped only to them. Mitigation: any other member can re-wrap the room key to a re-registered key after a human check. Bitwarden's emergency-access and account-recovery flows show the pattern, using public-key wrapping with an approval or wait step.

**Cost.** About 4 to 6 weeks on top of A. Key-distribution UX, member add/remove edge cases, the server-side feature loss described above, and a security review. It makes the shared-room story honest.

**First step.** Write a one-page protocol note (who holds which key, what happens on add, remove, recovery). Prototype two browsers sharing one room with wrapped room keys in a throwaway branch.

### C. Desktop app with a local SQLite file

**Idea.** A Tauri or Electron shell holds the authoritative SQLite database on disk, in a normal folder the user can copy, back up and open. It syncs encrypted deltas to the server (and encrypts the file with SQLCipher or an envelope key).

**Threat model.** Best durability and ownership (ideals 5 and 7). Operator exposure is the same as A or B depending on which sync encryption you add.

**Server can read.** Depends on the sync layer. This design alone changes nothing about server visibility.

**Recovery.** The file is the backup. Device loss is covered only if the user also backs up the file or syncs.

**Cost.** Highest: 8 or more weeks, plus code signing, auto-update, and two shells to maintain. The web app remains the main product, so this fragments effort. It also depends on A or B to fix the server-visibility problem, so it is not an alternative to them.

**First step.** Do not start. Revisit after A ships, and only if users ask for a desktop app. A Chromium-only folder sync in A gets most of the benefit at far lower cost.

## Ranking rationale

1. **A** closes the loss problem, the "encryption" ask and the "copy on my PC" ask for the least work, with no new infrastructure.
2. **B** is the honest way to make shared rooms private. It needs A's key hierarchy first.
3. **C** adds durability most users can get from A's folder export.

## Open decisions for the founder

1. Is losing server-side plaintext features (search, server-run agents) acceptable for E2EE rooms? If not, scope E2EE to personal rooms only.
2. Passphrase plus a printed recovery key, or a server-assisted PIN design like Signal SVR (needs hardware enclaves, much more complex)? I recommend the former.
3. Support the folder sync only where `showDirectoryPicker()` exists (Chromium), with a download fallback elsewhere?
4. Verify before committing any numbers or claims in public copy: the Safari 7-day rule and Chromium-only folder access should be re-checked against current browser data.
