# Local project sync — security architecture (V1 design, not yet implemented)

Status: **DESIGN GATE.** Nothing in this document is implemented. It defines the
V1 architecture for local-project sync end-to-end encryption precisely enough
that a later implementation pass can build it without inventing
security-sensitive behavior. Where a decision genuinely can't be made safely
from this repository alone, that is stated explicitly rather than guessed.

Terminology: this feature is **sync** / **local project sync** / **synced
project**, never "claim" — "claim" is an established, different keळ term
(`claims.md`, verification claims, the claim graph, `GET /v1/claims/graph`).
Nothing in this document touches that unrelated concept.

Related document: `SECURITY.md` (repo root) states the *pre-existing* v0.1
threat model — "single-user, local-first... no StealthLab-hosted service."
That document predates the multi-user REST API, Supabase Auth, and Commons
that already exist in this codebase today, and predates this feature. This
document does not attempt to reconcile that drift; it only adds the new
threat surface this feature introduces (the local bridge) on top of the
`127.0.0.1`-bind, token-gated posture `SECURITY.md` already establishes for
the MCP server.

---

## 0. What already exists (Part 1 — repository inspection)

Read directly, not from prior reports, before writing anything below.

- **`backend/app/mcp_server/server.py`** — the MCP server binds
  `127.0.0.1:8765` (constant `_MCP_PORT`), serves **only** the MCP
  Streamable-HTTP protocol via `MCPServer(...).streamable_http_app()`. It has
  **no CORS middleware, no `Origin`/`Host` validation, and no browser-facing
  REST route** except the read-only, unauthenticated `GET /` health probe and
  the `/claim-graph` viewer (a separate, pre-existing, unrelated read-only
  page). There is currently no way for a browser tab to talk to this process
  at all beyond loading that one static page. Confirmed by grep: zero matches
  for `CORSMiddleware`, `Access-Control-Allow-Origin`, `allow_origins`.
- **`backend/app/services/authn.py`** — the ONE identity mechanism: RS256/ES256
  OIDC/Supabase JWT verification against a JWKS (`OidcConfig.from_settings`,
  `validate_token_async`). Produces an `Actor(subject, issuer, email, name,
  claims)`. A contextvar (`_actor_cv`) carries it through one request.
- **`backend/app/services/auth_context.py`** — `Actor` → `AuthContext`
  (`resolve_auth_context`) via `ensure_user` (`users` table keyed by
  `(issuer, external_subject)`), returning `user_id` (a real `users.id`
  UUID) and `subject` (the raw OIDC `sub`, unchanged).
- **`backend/app/api/me.py`** — `GET /v1/me/stealth-projects[/{project_id}]`,
  gated by `require_authenticated_user`, scoped to `principal.subject`.
- **`backend/app/stealth/project_sync.py`** — `ensure_stable_project_id`
  (mints/reads a UUID4 in `.stealth/meta.json`'s `stable_project_id` key,
  survives rename/move); `preview_sync`/`sync_project` (insert-or-verify a row
  in `synced_projects`, `project_id` PK, `owner_subject` TEXT — the raw OIDC
  subject, same value space as `stealth_edit_ledger.actor`); `build_bootstrap_
  snapshot` (reads exactly `goals.md, claims.md, procedures.md, run.md,
  exploration.md, ledger.md` off disk if present, plus `stealth_edit_ledger`
  activity rows); `bootstrap_project` (JSON-serializes that snapshot and
  calls `object_storage.store_blob` — **today this snapshot is plaintext
  JSON**, this is exactly what this design replaces).
- **`backend/app/execution/workspace_init.py`** /
  **`backend/app/stealth/generator.py`** — both call `ensure_stable_project_id`
  so every workspace gets a stable id on first `init_workspace`/
  `generate_projection`, independent of whether it's ever synced.
- **`backend/app/services/object_storage.py`** — `MemoryStore` / `LocalFileStore`
  / `S3Store`, all content-addressed (`sha256(bytes)` → locator), `raw_objects`
  table (`sha256` PK, `locator`, `backend`, `size_bytes`, `content_type`).
  **Important for this design**: content-addressing assumes identical bytes
  in → identical bytes out. Random-IV ciphertext breaks that assumption on
  purpose (§ Server data model, below) — this is inherent to semantically
  secure encryption, not a bug to fix.
- **`db/107_claimed_projects.sql`** (applied to the real DB) /
  **`db/108_rename_claimed_projects_to_synced_projects.sql`** (written, **not
  yet applied** to the real DB — this design intentionally does not apply it
  either; see § Migration plan).
- **`.stealth/events.jsonl`** — `app.stealth.journal`: `SingleWriterLock` +
  `append_events`/`latest_seq`, a **purely local, append-only, monotonic**
  journal. Never uploaded today. `generate_projection` calls `append_events`
  on every regeneration (`projection_regenerated`); `record_run_update` and
  `open_exploration`/`close_exploration` append too. This is the existing
  "I know something changed" signal § Ongoing sync hooks into.
- **`prod_frontend/lib/kel-api.ts`** — `getMyStealthProjects`/
  `getMyStealthProject`, typed over the current (plaintext) response shape.
- **`prod_frontend/lib/session.ts`** / **`lib/supabase.ts`** — Supabase Auth
  is the browser's only identity mechanism; `getAccessToken()` returns the
  current Supabase access token. **No browser storage/crypto utility exists
  yet** (no IndexedDB wrapper, no Web Crypto helper) — confirmed by grep,
  and confirmed `package.json` has no `@noble/*`/`tweetnacl`/`libsodium`
  dependency today. Same on the backend: no `pynacl`/`cryptography`
  dependency beyond what's already used for JWT verification.
- **Current real-DB state** (checked directly against the live database this
  session): `synced_projects` has **0 rows**; `raw_objects` has **0** rows
  with `content_type = 'application/json'`. No plaintext synced-project
  snapshot currently exists anywhere reachable. (The one snapshot created
  during the prior real-E2E test run was deleted as part of that test's own
  cleanup.) See § Existing plaintext data for the full finding.

None of the above needs to change architecturally for this design — the
identity plane, the stable-id mechanism, the object-storage abstraction, and
the local event journal are all reused as-is. What's missing entirely is (a)
any way for a browser to reach the local machine at all, and (b) any
encryption anywhere in the pipeline.

---

## A. Identity plane

Unchanged. `owner_subject` continues to be the raw, verified OIDC/Supabase
`sub` (§0), established the SAME way on both the REST API and the local
bridge (the bridge validates the browser's bearer token with the exact same
`validate_token_async`/`OidcConfig` the REST app already uses — no second
identity mechanism). Sync consent (§ B) is what creates the
`project_id → owner_subject` relationship; there is no separate ownership
action, matching the "no separate claim step" requirement.

## B. Local project discovery

1. On `/account`, once signed in, the browser attempts a single, narrowly-
   scoped, **read-only, unauthenticated** probe:
   `GET http://127.0.0.1:8765/.well-known/stealthlab-local` → `{"service":
   "stealthlab-local-bridge","version":1}` if a local keळ MCP process is
   running, connection-refused otherwise. This reveals only "a local keळ
   process exists on this machine" — no project data, no filesystem paths,
   nothing account-specific. (Mirrors the existing, already-public `GET /`
   health route — same sensitivity level, not a new category of exposure.)
2. If detected, the UI shows **"SYNC LOCAL PROJECTS?"**. Nothing else happens
   automatically — no project list, no content, until the user acts.
3. Clicking it starts the handshake in § C. Only after the user explicitly
   selects projects and confirms does any project content move anywhere.

## C. Local bridge protocol (the security-critical part)

This is new infrastructure — none of it exists today (§0). Every step
re-validates `Origin` and `Host`; neither check is trusted alone.

**Bridge-side checks, on every request without exception:**
- `Origin` header must exact-match a **server-configured allowlist** of the
  real keळ web app origin(s) (e.g. the deployed origin plus
  `http://localhost:3100` for local dev) — checked in the request handler
  itself, not delegated to CORS headers (a browser enforces CORS on the
  *response*; nothing stops a non-browser HTTP client, or a browser doing a
  simple/no-preflight request, from sending the request in the first place —
  this is exactly why "Origin allowlist enforced server-side" is required,
  not "CORS configured").
- `Host` header must exact-match `127.0.0.1:8765` or `localhost:8765`. This
  is the DNS-rebinding defense specifically: DNS rebinding gets a browser to
  resolve `attacker.example` to `127.0.0.1`, but the `Host` header the
  browser sends is still `attacker.example` — checking `Host`, not the
  socket's peer address, catches it. `Origin` allowlisting alone does not
  catch this, because the attacker's page has a real, different origin it
  controls; the two checks catch different attacks and both are required.
- Method must be `POST` for every state-mutating step (discovery's `GET` is
  the one harmless exception, and it returns zero sensitive data).

**Protocol steps:**

1. **`POST /local-sync/start-handshake`** — body: `{supabase_access_token}`.
   Bridge validates the token exactly as the REST app's `install_actor_
   middleware` does (same `validate_token_async` call, same `OidcConfig`).
   On success, mints a random 256-bit, single-use, 60-second-TTL capability
   token, keyed in an **in-memory** dict (`{token: {subject, expires_at,
   step: "list", used: False}}` — never persisted to disk). Returns it in the
   JSON body (never a URL/query string, so it never lands in access logs or
   browser history).
2. **`POST /local-sync/list-projects`** — header `X-Sync-Capability:
   <token>`. Bridge checks: token exists, unexpired, unused, `step=="list"`,
   subject matches. Marks it used, mints the *next* chained capability
   (`step="prepare"`) in the response — single-use, chained tokens, so a
   captured token from one step is worthless for any other step or any later
   time. Returns `[{project_id, display_hint, last_local_activity_at,
   has_run}]` — `display_hint` is the folder **basename only** (never the
   full path), and this is the only place a folder name is ever visible
   outside the local machine's own filesystem; it never reaches the remote
   server (§ Metadata policy).
3. User picks projects in the browser UI; clicks confirm.
4. **`POST /local-sync/prepare-payload`** — header carries the `step=
   "prepare"` capability; body: `{project_ids: [...]}`. Bridge re-validates,
   then for **only** the selected `project_ids` calls the existing
   `build_bootstrap_snapshot` (unchanged) and returns the assembled
   **plaintext** JSON snapshot(s) directly in the HTTP response body — over
   this authenticated loopback connection only, never touching the public
   network. Mints the final chained capability (`step="register-key"`).
5. Browser generates a fresh random P-DEK (§ E) for each newly-selected
   project, encrypts its snapshot client-side (§ D), and uploads ciphertext
   to the real remote REST API (never through the bridge).
6. **`POST /local-sync/register-local-key`** — header carries the
   `step="register-key"` capability; body: `{project_id, p_dek_base64}`. This
   is the ONE place the raw P-DEK crosses process boundaries, and it happens
   exclusively over loopback, inside the same short-lived, single-use
   handshake — never over the public network, never through the remote
   server. Bridge caches it in the OS keychain (§ F/local key storage) and
   discards the in-memory capability record.

No step is replayable (each capability is single-use and step-scoped); the
whole handshake is bounded to ~60 seconds; nothing here is a generic file
endpoint — `prepare-payload` only ever returns the six allow-listed
`.stealth/*.md` files for explicitly-selected `project_id`s, the exact same
allow-list `project_sync.ALLOWED_SNAPSHOT_FILES` already enforces today.

**Design choice — who encrypts (Phase 5's A vs. B):** **Option B** — the
bridge hands plaintext to the browser over the authenticated loopback
session, and the **browser** encrypts. Rejected Option A (bridge encrypts,
sends ciphertext) because it would require the P-DEK to exist in the local
process *before* the user has entered a recovery passphrase anywhere, which
only makes sense as a browser-side interaction (§ I); centralizing
encryption in one audited, browser-native implementation (Web Crypto) is
also simpler to reason about than maintaining two encryption implementations
that must produce interoperable output.

## D. Encryption boundary

**Primitive: AES-256-GCM**, via the browser's native `SubtleCrypto` (Web
Crypto API). Verified directly against MDN (`SubtleCrypto.encrypt`):
AES-GCM is Baseline/widely available since January 2020, requires a 12-byte
IV, is an AEAD (confidentiality + integrity in one operation — this is why
it's chosen over AES-CBC, which needs a separate MAC and is easier to get
wrong). Also cross-checked against OWASP's Cryptographic Storage Cheat
Sheet: "AES with a key that's at least 128 bits (ideally 256 bits)... GCM
and CCM as first choices" — matches.

**Nonce/IV discipline**: a fresh `crypto.getRandomValues(new Uint8Array(12))`
per encryption call, never reused for the same key. With a random 96-bit IV
and AES-GCM, the birthday-bound collision risk becomes a real concern only
after roughly 2^32 encryptions under the *same key* (NIST SP 800-38D's own
guidance for random-IV construction) — many orders of magnitude beyond any
realistic per-project sync volume. Documented here explicitly as a follow-up
hardening candidate (a persisted per-key monotonic counter IV instead of
random, which removes the birthday bound entirely) rather than a V1 blocker.

**XChaCha20-Poly1305 rejected for V1**: not a native `SubtleCrypto`
algorithm (confirmed — MDN lists exactly four: RSA-OAEP, AES-CTR, AES-CBC,
AES-GCM), so it would require an external library in the one place (the
browser) where a native, browser-vendor-audited implementation is already
available and sufficient. Prefer the native primitive.

**What is encrypted, and by whom:**

| Content | Encrypted? | By | When |
|---|---|---|---|
| `goals.md`, `claims.md`, `procedures.md`, `run.md`, `exploration.md`, `ledger.md` contents | Yes | Browser | Before the HTTP request to the remote server is made |
| Activity/event history entries | Yes | Browser | Same request |
| `display_hint` (folder basename) | Never leaves the local bridge — the server never receives it at all | n/a | n/a |
| Recovery-wrapped P-DEK | Yes (it's already ciphertext — the P-DEK wrapped under the recovery KEK) | Browser | At first sync (or first recovery-passphrase setup) |
| Ongoing incremental deltas | Yes | Local MCP process, using the OS-keychain-cached P-DEK | At the moment of the local hook (§ G) |

## E. Key hierarchy

```
                     Sync Recovery Passphrase (user-chosen, never sent to server)
                                    │
                         Argon2id (client-side KDF)
                                    │
                            Recovery-KEK
                                    │
                          AES-256-GCM "wrap"
                                    │
                                    ▼
                         wrapped P-DEK (ciphertext, server-stored)
                                    ▲
                                    │ unwrap (browser only, on recovery/new device)
                                    │
                    ┌───────────────┴───────────────┐
                    │                                │
              Browser (session)              Local MCP process
              holds raw P-DEK                holds raw P-DEK
              only in memory /               cached via OS keychain
              non-extractable                (keyring: Credential
              CryptoKey while tab            Manager / Keychain /
              is open                        Secret Service)
```

- **P-DEK**: one fresh, random 256-bit AES-GCM key **per project**, generated
  client-side (`crypto.getRandomValues`) the first time that project is
  synced. Never derived from username, email, `user_id`, JWT, OAuth token,
  project name, or filesystem path (all of those are either public-ish,
  guessable, or shared across projects — none is a valid key source, and
  this list is checked explicitly here because it's the most common way this
  kind of design goes wrong).
- **Recovery-KEK**: derived client-side from the user's Sync Recovery
  Passphrase via Argon2id (§ I). Used only to wrap/unwrap P-DEKs — never used
  to encrypt project content directly.
- **Wrapping mechanism**: AES-256-GCM encrypting the raw 32-byte P-DEK under
  the Recovery-KEK (same primitive as data encryption — deliberately not a
  second construction; NIST/OWASP don't mandate a distinct "key-wrap mode"
  when AEAD is already in use for both purposes, and using one audited
  primitive throughout is simpler to review than two).
- **V1 explicitly has no separate per-device asymmetric wrapping** (no
  HPKE/X25519 device keys). See § Rejected alternatives for why, and § H for
  what this means for new devices in V1.
- **What the server stores**: `wrapped_p_dek` (ciphertext), the Argon2id
  `salt` (not secret — a salt's role is uniqueness, not confidentiality;
  standard to store it plaintext alongside the wrapped key it protects),
  Argon2id parameters used (so they can be strengthened later without
  breaking old wraps), and nothing else key-related.
- **What the server never receives, under any circumstance**: the raw P-DEK,
  the recovery passphrase, the Recovery-KEK, or any local MCP OS-keychain
  material.

## F. Local MCP key storage

`keyring` (the standard cross-platform Python package — Windows Credential
Manager via `pywin32`, macOS Keychain via `Security.framework`, Linux Secret
Service/libsecret) is the concrete dependency this design proposes adding to
`backend/requirements.txt`, used only by the local bridge process, only for
this one purpose: `keyring.set_password("stealthlab-sync", project_id,
p_dek_base64)` after step C.6, read back on every ongoing-sync hook (§ G).
Never written to `.stealth/meta.json`, never an environment variable, never
logged (§ M). This is genuinely new — no keychain/credential-store
integration exists anywhere in this codebase today (confirmed by search).

## G. Ongoing automatic sync

Hooks into the **existing** local event points — no new watcher, no polling
daemon, no realtime infrastructure:

- `app.stealth.journal.append_events` (already called by
  `generate_projection`, `record_run_update`, `open_exploration`/
  `close_exploration`) is the existing "something changed" signal.
- Add one new, best-effort, purely local hook at the same call sites: if
  `keyring.get_password("stealthlab-sync", project_id)` returns a key (i.e.
  this project is synced), build a small delta payload (the changed
  `.stealth/*.md` file(s) plus the new journal event), encrypt it locally
  with the cached P-DEK, and POST the ciphertext to the remote API,
  tagged with the journal's own monotonic `seq` as the **revision number**.
- **Offline handling**: if the POST fails, append one line to a new,
  purely-local `.stealth/.sync-queue.jsonl` (already-encrypted ciphertext +
  revision, so nothing further needs to happen except "send these bytes") —
  never blocks the local operation that triggered it. On the next local MCP
  call for that workspace (natural "reconnect" point, not a background
  timer), drain the queue in revision order. This satisfies "local keळ
  continues working normally offline; pending changes sync when connectivity
  returns" without any polling loop.
- **Idempotency**: uploads are keyed by `(project_id, revision)`; a repeated
  POST for a revision already recorded is a no-op. Revision, not a
  content-hash of the ciphertext, is the dedup key — ciphertext is
  non-deterministic by construction (random IV), so two uploads of identical
  plaintext produce different ciphertext bytes on purpose (this is required
  for semantic security: identical ciphertext for identical plaintext would
  leak "these two states are the same" to the server).
- **No CRDT, no bidirectional sync in V1.** This is local-authoritative,
  one-way (local → server) sync only. A second device with its own local
  `.stealth` checkout syncing concurrent, conflicting local edits back is
  explicitly a later phase — V1 does not attempt to merge or resolve that,
  and does not pretend to.

## H. New-device access (V1)

Because V1 has no per-device asymmetric wrapping (§ E), the only way a new
device gets the P-DEK is the **recovery path** (§ I): sign in, fetch the
wrapped P-DEK + salt, enter the recovery passphrase, derive the Recovery-KEK
locally, unwrap P-DEK locally. If that device also runs a local MCP process
against the same (or a fresh) checkout of the project and wants ongoing
local sync, the browser hands the now-unwrapped P-DEK to that device's local
bridge exactly the same way as initial sync (§ C, step 6) — no new
mechanism, the existing loopback handshake is reused.

This means, honestly stated: **V1 has exactly one authorization path onto a
project's key material — the recovery passphrase.** There is no
"authorize this new device from an existing device" flow in V1 (Phase 8's
option 2). That is a real, deliberate scope limit, not an oversight — see
§ Rejected alternatives.

## I. Recovery

**Sync Recovery Passphrase**, chosen by the user, distinct from their
Supabase login credential (a login password/OAuth session proves identity;
it is never itself an encryption key — Phase 7's own framing, and correct:
Supabase never exposes the user's password to the client in a form usable
as key material anyway, and OAuth providers never do either).

- **Derivation**: Argon2id, client-side. Verified OWASP Password Storage
  Cheat Sheet baseline parameters: `m=19456` KiB (19 MiB), `t=2`, `p=1` (the
  cheat sheet's own preferred baseline; that document's guidance is for
  password *hashing*, not key derivation specifically, but its parameter
  table is the right OWASP-sourced starting point for a KDF operating under
  similar hardware/timing constraints — this repo's Key Management Cheat
  Sheet reading did not add anything KDF-specific beyond that).
- **Salt**: random 16 bytes, generated client-side once per account at
  recovery-passphrase setup, stored server-side in plaintext next to the
  wrapped key (§ E) — salts are not secret.
- **Library**: native `SubtleCrypto` has no Argon2 primitive (only PBKDF2/
  HKDF are native KDFs — confirmed via the same MDN check as § D), so an
  external, WASM-based Argon2id implementation is required in the browser.
  **Open item, not resolved here**: which specific library. This needs its
  own verification pass (bundle size, maintenance status, and ideally an
  audit trail) before implementation — naming one without that check would
  be exactly the "don't blindly trust a recommendation" failure mode this
  design is trying to avoid.
- Never sent to the server, never logged, never reused as the ordinary login
  password.
- **Loss scenario, stated plainly**: if a user loses every device that holds
  the unwrapped P-DEK *and* loses the recovery passphrase, that project's
  synced content is permanently unrecoverable. No server-side backdoor
  exists or will be added. This must be told to the user in the UI at
  recovery-passphrase setup time, not buried in a settings page.

## J. Unsync / deletion

New MCP tool (naming follows the established `sync`-terminology convention):
`unsync_local_project(repo_path, confirm)` — same preview/confirm shape as
`sync_local_project`. On confirm:

- Delete the `synced_projects` row (or, if project-level history matters
  later, mark it `unsynced_at` rather than hard-delete — a decision for
  implementation time, not blocking this design).
- Delete every ciphertext `raw_objects` row belonging to that project
  (snapshot + any accumulated deltas) and the wrapped-key row.
- Tell the local bridge to `keyring.delete_password(...)` for that
  `project_id` — this is what actually stops ongoing sync (§ G's hook checks
  for a cached key; no key, no more uploads).
- **Never touches `.stealth/` locally.** Nothing in the unsync path opens,
  writes, or deletes any local file — it only ever talks to the remote DB/
  object storage and the local OS keychain entry.
- **Honest deletion semantics**: if the configured object-storage backend
  (S3/R2/whatever is deployed) or the underlying Postgres provider (Supabase)
  retains backups, a `DELETE` here removes the *live*, queryable copy
  immediately, but cannot guarantee erasure from provider-side backup
  retention on whatever schedule that provider uses. This must be stated to
  users exactly like that — not "permanently and completely erased
  everywhere," which V1 cannot actually promise. The user can re-sync later;
  a fresh sync mints a fresh P-DEK (old ciphertext, even if it somehow
  survived in a backup, is not decryptable with the new key).

## K. Threat model

**In scope — V1 defends against:**

1. **Database dump** — attacker gets `raw_objects`/`synced_projects` rows:
   sees ciphertext, an opaque `project_id`, `owner_subject`, timestamps,
   sizes. No project content, no filenames, no P-DEK.
2. **Object-storage compromise** — same: ciphertext only.
3. **SQL injection / data exfiltration** — same boundary; whatever is
   exfiltrated is still ciphertext.
4. **Backend API compromise** (an attacker who can call the REST API as the
   service) — still cannot decrypt; the service never holds a P-DEK.
5. **Malicious backend operator** — a human with full DB/object-storage
   access sees the same ciphertext everyone else in this list sees.
6. **Compromised service credentials** — same as (4).
7. **Cross-account authorization bug** — mitigated by (a) `owner_subject`
   scoping identical to the existing `/v1/me/stealth-projects` pattern
   (never trusts a client-supplied id) and (b) even a successful cross-
   account read only yields another account's ciphertext, not their P-DEK.
8. **Network interception** — TLS in transit (existing infra) plus the
   payload itself is already ciphertext before it's sent.
9. **Unauthorized browser origin reaching the local bridge** — the
   server-side `Origin` allowlist (§ C).
10. **DNS rebinding** — the `Host` header check (§ C).
11. **CSRF against the local bridge** — same two checks; capability tokens
    additionally mean even a request that passed Origin/Host still needs a
    valid, single-use, subject-matched, unexpired token to do anything
    beyond the harmless discovery probe.
12. **Accidental plaintext logging** — addressed by explicit logging
    discipline (§ M); needs enforcement (tests) at implementation time.
13. **Accidental analytics/error-reporting leakage** — same; § M.
14. **A stolen bearer/auth token** — grants access to that account's
    ciphertext and metadata (same blast radius as today's REST API for any
    other private endpoint) but still not the P-DEK, which is never
    server-derivable from the token (§ E's explicit non-derivation list).

**Explicitly out of scope — V1 does NOT protect against, and does not claim
to:**

- A fully compromised user operating system (a real root/kernel-level
  attacker on the user's machine can read anything the user's own processes
  can read, including an unwrapped P-DEK in memory or in the OS keychain the
  legitimate process is itself entitled to read).
- Malware controlling the user's local browser.
- A malicious or compromised browser extension with access to the page's
  rendered DOM/JS execution context (it can read decrypted plaintext exactly
  as the legitimate page can — this is a fundamental limit of *any*
  browser-side E2EE design, not something this architecture can fix).
- A compromised browser or local MCP process instance *while plaintext or
  an unwrapped key is actively present in its memory* — this design
  minimizes the window that's true (§ D/E), it does not eliminate it.

**Explicitly not claimed**: "zero-knowledge" as a blanket marketing term is
not used anywhere in this design. What is claimed, precisely: the keळ
backend and its operators cannot decrypt synced project content without
either the user's recovery passphrase or physical/process access to a
device that already holds the unwrapped key. `extractable: false` on a
`CryptoKey` is **not** claimed to defeat XSS — an attacker executing inside
the trusted origin can still invoke permitted cryptographic operations
(encrypt/decrypt using the key) or read whatever plaintext the page itself
legitimately renders; non-extractability only stops the raw key bytes from
being read out directly. This is stated here explicitly because it's the
single most common overclaim in this class of design.

## L. Metadata policy — exact plaintext/ciphertext boundary

**Plaintext, server-visible:**
`owner_subject`, opaque `project_id` (already a UUID, not path-derived),
device/session identifiers tied to the local bridge if any are introduced at
implementation time, sync revision number, ciphertext size in bytes,
`synced_at`/`bootstrapped_at`-equivalent timestamps, coarse sync status
(`pending`/`synced`/`error`), Argon2id salt and parameters.

**Ciphertext only, server never sees plaintext:**
all six `.stealth/*.md` contents, activity/event entries, the wrapped P-DEK
itself (it's ciphertext of a key, not the key).

**Never reaches the server at all, encrypted or not:**
raw filesystem paths, the project's local folder name (`display_hint` stays
on the loopback interface, § C step 2), the recovery passphrase, the raw
P-DEK, OS-keychain contents.

## M. Logging / telemetry policy

- New routes (`/local-sync/*` and the remote sync-upload endpoint) must
  never log full request/response bodies — only method, path, status,
  subject, and byte count, matching the existing `record_security_event`
  convention already used in `app/api/deps.py` (`actor_subject`, `action`,
  `object_type`, `object_id` — no payload field).
- Error messages on these paths must interpolate only sizes/counts/ids,
  never content (the existing `StateNotice`/`ApiState` error shape on the
  frontend already carries only a short message string, not raw response
  bodies — consistent with this).
- Telemetry may record sync success/failure, payload size, duration, a
  generic error category, and auth events — never file contents, never key
  material, by construction (the telemetry layer is never handed the
  plaintext or the key in the first place, so this is enforced by the data
  simply not being there, not by a redaction step that could be forgotten).
- This needs an explicit test (§ Test plan) once implemented, since
  "we didn't intend to log it" is not the same guarantee as "it's
  structurally impossible to log."

## N. Rejected alternatives (and why)

- **HPKE (RFC 9180) for per-device key distribution** — rejected for V1.
  Verified: not a native `SubtleCrypto` primitive (MDN). The most complete
  JS implementation found (`hpke-js`, built on Web Crypto, passes the
  RFC 9180 test vectors) is explicitly **not formally audited** as of this
  research. Depending on an unaudited library for the one place device-to-
  device key transport security actually matters is a worse trade than
  deferring per-device asymmetric wrapping to a later phase once a mature,
  audited implementation exists (or the Web Crypto API gains it natively).
- **WebAuthn PRF for key derivation** — rejected for V1, deferred to a
  future phase. Verified: WebAuthn Level 3 is a real W3C Recommendation
  (25 August 2026) and does define the PRF extension. What is *not*
  resolved by that fetch is PRF output stability across synced/roaming
  passkeys, which is exactly the property a recovery mechanism would need
  to depend on — using it now without verifying that specifically would be
  the "assume it just works" failure mode this document is required to
  avoid. The passphrase-based recovery in § I is simpler, has predictable
  cross-platform behavior, and doesn't require a platform authenticator
  ceremony as a hard dependency for account recovery.
- **CRDTs for bidirectional sync** — rejected; V1 is one-way (local →
  server) only (§ G). Nothing in the existing architecture demonstrates a
  concurrent-multi-device-editing requirement today, and introducing CRDT
  machinery for a case that doesn't exist yet is exactly the premature
  complexity this task explicitly warns against.
- **XChaCha20-Poly1305** — rejected in favor of native AES-GCM; see § D.
- **Deriving the P-DEK from the JWT/OAuth token/login password** — rejected;
  see § E's explicit non-derivation list and Phase 7's own framing
  (authentication and encryption are separate concerns; Supabase/OAuth never
  hands the client key-usable secret material anyway).
- **CORS-only protection for the local bridge** — rejected; CORS is a
  browser-enforced, response-side control and does not stop a same-origin-
  looking request from reaching the bridge in the first place, nor does it
  address DNS rebinding. Server-side `Origin` + `Host` validation plus
  single-use capability tokens (§ C) is required in addition.

---

## Existing plaintext data (Part 20 — finding)

Checked directly against the live production database this session:
`synced_projects` currently has **0 rows**; `raw_objects` currently has
**0 rows** with `content_type = 'application/json'` (the content-type the
current plaintext bootstrap path uses). No plaintext synced-project snapshot
currently exists anywhere reachable by any API. (One snapshot was created
and then deleted as part of the real end-to-end acceptance test run earlier
in this project; that cleanup already removed it — see that session's own
report for the exact rows deleted.) **No migration/deletion work is required
for existing data** — there is none to migrate. This finding should be
re-verified at implementation time in case anything changed between now and
then.

---

## Test plan (Part 21 — to guide, not to run yet)

**Local discovery**: browser can detect a running local bridge; a script
running from a disallowed `Origin` is rejected; a request with a forged
`Host` header (simulating DNS rebinding) is rejected; an expired capability
is rejected; a capability reused for a second request is rejected; a
capability used for the wrong step is rejected.

**Encryption**: the remote upload endpoint's request body contains no
`.stealth/*.md`-shaped plaintext; the `synced_projects`/`raw_objects` rows
contain no plaintext (byte-level assertion, not just "no obvious substring");
a bit-flipped ciphertext fails AES-GCM's tag check and is rejected, not
silently misdecrypted; two encryptions of identical plaintext under the same
key produce different ciphertext (proves IV uniqueness is actually wired
up, not accidentally constant).

**Keys**: no route or table ever contains an unwrapped P-DEK; no route
accepts a JWT as key material; the recovery passphrase never appears in any
request to the remote server; wrap/unwrap round-trips correctly; a wrong
recovery passphrase fails to unwrap (not "unwraps to garbage that then fails
downstream" — should fail at the unwrap step itself, since AES-GCM is
authenticated).

**Local bridge**: `prepare-payload` never returns a file outside
`ALLOWED_SNAPSHOT_FILES`; requesting an unselected `project_id` is refused;
the discovery endpoint never returns project data.

**Sync**: initial sync round-trips (encrypt → upload → fetch → decrypt →
matches original); repeated upload of the same revision is a no-op;
simulated offline (upload fails) queues locally and drains on the next local
operation; unselected projects never produce any remote-bound request.

**Isolation**: user A cannot fetch user B's ciphertext or wrapped key via
the REST API (same `owner_subject`-scoping test pattern already used for
`/v1/me/stealth-projects`); guessing another user's real `project_id`
behaves identically to a nonexistent one (404, indistinguishable) — already
an established pattern in `app/api/me.py`, must hold for the new
sync-upload/fetch routes too.

**Unsync**: remote rows deleted; local `.stealth/*` files byte-for-byte
unchanged after unsync; the OS-keychain entry for that project is gone;
re-syncing afterward works and mints a fresh P-DEK (not the old one).

**Recovery**: a second (simulated) device can unwrap and decrypt using only
the recovery passphrase; a lost/never-authorized device has no path to the
key at all; a wrong passphrase fails safely with a clear error, never a
silent wrong-key decrypt.

---

## Answers to Part 22's checklist

1. **Key hierarchy**: per-project random P-DEK (AES-256-GCM) → wrapped by a
   Recovery-KEK (Argon2id-derived from a user-chosen passphrase) via
   AES-256-GCM. No per-device asymmetric layer in V1 (§ E, § H, § N).
2. **Encryption primitive**: AES-256-GCM, native `SubtleCrypto`, 12-byte
   random IV per operation (§ D).
3. **Key-wrapping mechanism**: AES-256-GCM (same primitive, used to wrap the
   32-byte P-DEK under the Recovery-KEK) (§ E).
4. **Local bridge protocol**: five-step, capability-chained, Origin+Host-
   validated handshake on `127.0.0.1:8765` (§ C).
5. **Initial sync sequence**: discover → consent ("SYNC LOCAL PROJECTS?") →
   handshake → select → bridge returns plaintext over loopback only →
   browser encrypts → browser uploads ciphertext + wrapped key → browser
   hands raw P-DEK to the bridge over loopback → bridge caches it in the OS
   keychain (§ C, § F).
6. **Ongoing incremental-sync sequence**: hook into the existing local
   journal write points → if a cached P-DEK exists, encrypt the delta →
   upload tagged with the journal's monotonic revision → on failure, queue
   locally and drain on the next local operation (§ G).
7. **New-device flow (V1)**: recovery-passphrase-only (§ H) — no
   device-to-device authorization flow in V1.
8. **Recovery flow**: Argon2id(passphrase, salt) → Recovery-KEK → unwrap
   P-DEK → decrypt client-side (§ I).
9. **Unsync/delete flow**: delete server-side ciphertext + wrapped key rows,
   purge the local OS-keychain entry, never touch `.stealth/` (§ J).
10. **Server plaintext vs. ciphertext boundary**: exact table in § L.
11. **Browser storage strategy**: raw P-DEK held only in memory / as a
    non-extractable `CryptoKey` for the session; no `localStorage`, no
    plaintext IndexedDB key material. (The exact IndexedDB-vs-memory-only
    trade-off for *session persistence* across tab reloads is an open
    implementation-time decision, not resolved here, because it depends on
    UX requirements — e.g., "must the user re-enter the passphrase on every
    reload" — that are product decisions, not security ones, once the
    non-extractable-CryptoKey pattern is used either way.)
12. **Local MCP key storage strategy**: OS keychain via the `keyring`
    package, one entry per synced `project_id` (§ F).
13. **Threat model**: § K, full list.
14. **Security limitations**: § K's "explicitly out of scope" list, stated
    without hedging.
15. **Libraries/packages proposed**: `keyring` (backend, new dependency, OS
    credential store access). A browser-side Argon2id WASM library
    (**specific choice deliberately left open** — needs its own maintenance/
    audit verification pass before implementation, § I). No other new
    dependency — AES-GCM and PBKDF2/HKDF are native `SubtleCrypto`.
16. **Browser/runtime compatibility assumptions**: `SubtleCrypto` AES-GCM
    (Baseline widely available since Jan 2020 — safe to assume). WebAuthn
    PRF explicitly NOT assumed/required for V1.
17. **Migration plan for existing plaintext data**: none required — verified
    zero rows exist (§ Existing plaintext data).

---

## What this document does not do

It does not write any implementation code, does not touch `sync_behavior`,
does not add a migration, and does not stand up a local bridge. Per the
task that requested it, this is the design gate — implementation follows in
a separate pass, against this document, with the one open item (§ I's
specific Argon2id library choice) resolved first.

---
---

# Implementation Closure

Status: **DESIGN CLOSED**, with one bounded, explicitly-flagged residual item
(§ 3, the Argon2id library's audit status) that gates *that one component*
before shipping, not the rest of the architecture. Everything else below has
a defensible, concrete answer. Still no implementation code, no migration
applied, no local bridge stood up in this pass — this section only resolves
open design questions from the ADR above.

Terminology check for this section too: sync / local project sync / synced
project / unsync throughout; no reintroduction of "claim."

## 1. Ongoing local-sync authentication credential

**Answer: C, adapted** — extend the repository's existing worker/service
credential mechanism (`backend/app/services/service_identity.py`, migration
99) rather than inventing a new protocol. That module already provides
exactly the right *shape*: short-lived signed JWTs, a database-backed
revocation registry (`service_credentials`: `credential_id`, `fingerprint`,
`expires_at`, `revoked_at`), per-credential scope enforcement (a requested
scope must be a subset of what's granted, escalation rejected not trimmed),
and audit logging on issue/revoke — all already built, tested, and running
(this table exists in the real database as of this session's earlier
migration catch-up).

It does **not** fit unmodified: `ServiceAuthContext` has no per-user
`owner_subject`, and `require_authenticated_user` explicitly rejects service
credentials ("a user identity is required; service credentials are not
accepted here") — services aren't users, by design, and that design is
correct and must stay correct for every *other* endpoint. A local sync
device credential is a third kind, distinct from both:

- **New, dedicated issuer/audience** (e.g. `SYNC_DEVICE_TOKEN_ISSUER` /
  `_AUDIENCE`, its own signing key config, mirroring `ServiceTokenConfig`'s
  shape) — never overlaps with the human Supabase/OIDC trust domain or the
  worker trust domain. A sync device token can never be presented where a
  Supabase user token or a worker service token is expected, and vice versa
  (different `iss`/`aud`, rejected by construction the same way
  `service_identity.py`'s docstring already states for its own tokens: "a
  Supabase user token can never verify here").
- **`sub` claim = the user's own real `owner_subject`** (their OIDC
  subject) — this is what lets the sync-upload endpoint verify "this
  credential belongs to the account that owns this project" directly from
  the token, as defense in depth on top of (never instead of) the existing
  DB-level `owner_subject` check every other `/v1/me/*` route already does.
- **Exactly one scope**: `sync:upload`. Cannot call any other route, cannot
  read the account's other data, cannot mint further credentials.

**Issued**: by a new, Supabase-session-authenticated REST endpoint (e.g.
`POST /v1/me/sync-devices`) — only a browser holding a real, currently-valid
Supabase session can mint one for itself. Handed to the local bridge over
the SAME loopback channel already carrying the P-DEK handoff (§ C step 6 of
the ADR above; extend that one capability-chained step to deliver both, or
add one more chained step — either is a small addition to an already-
designed sequence, not new infrastructure).

**Stored**: OS keychain, via the same `keyring` mechanism as the P-DEK (§ F),
under its own entry namespace (`stealthlab-sync-device`, keyed by
`project_id` or `device_id`) — never `.stealth/meta.json`, never a plaintext
file, never an environment variable, never a URL.

**Scope of access**: `sync:upload` only, and only for the `project_id`(s) the
token's issuing request named — the upload endpoint still independently
verifies `synced_projects.owner_subject == token.sub` before accepting any
ciphertext, so a token can never be used to write to a project it wasn't
issued for even if scope alone were somehow insufficient.

**Lifetime and rotation**: issued with a bounded lifetime (recommend 30
days, matching "must be able to keep syncing after the browser closes" —
long enough that ordinary usage doesn't force frequent re-auth, short enough
that an unrevoked-but-forgotten credential doesn't live forever). The local
process can **self-rotate**: while its current token is still valid, it may
call a `POST /v1/me/sync-devices/rotate` (or equivalent) presenting the
current token to receive a fresh one with a new expiry and a new `jti` —
this needs no browser involvement, exactly satisfying "must keep working
after the browser closes," indefinitely, as long as rotation actually
happens before expiry. If it's allowed to fully expire (machine offline for
a long stretch, or a bug in the rotation trigger), the local process falls
back to: ongoing sync pauses, queues locally (§ G's existing offline-queue
mechanism), and resumes automatically the next time the user opens
`/account` while signed in, which silently re-issues a fresh credential and
hands it to the bridge exactly as at first sync. This is an availability
degrade, not a security hole — never a silent security downgrade (e.g.
never falls back to using the general Supabase token instead).

**Revocation**: `UPDATE sync_device_credentials SET revoked_at = now() ...`
(new table, same shape as `service_credentials`) — checked on every verify,
same cache-TTL-bounded observation window `service_identity.py` already
documents (≤ `auth_cache_ttl` seconds, default 30, to notice a revocation).
Reachable from the account UI (§ 14 of the ADR's UNSYNC action revokes the
credential(s) for that project as part of unsyncing; a broader "manage
synced devices" list under Settings — the one place this design genuinely
needs a small Settings touch, per the ADR's own "except where navigation
integration is genuinely required" allowance — lets a user revoke any
device's sync credential directly, e.g. after a machine is lost).

**After unsync**: the credential for that project is revoked immediately
(§ J of the ADR); the local process's next attempted use gets `credential_
revoked_or_unregistered` (the exact rejection reason `verify_service_token`
already returns for this case in the worker-credential path — reused
verbatim) and stops retrying that project.

**If the local machine is stolen**: the credential sits in the OS keychain,
protected by whatever the OS credential store itself provides (e.g. tied to
the logged-in OS account, encrypted at rest by the OS). If the machine is
stolen *powered off or locked*, the attacker does not trivially get it —
same boundary as every other OS-keychain-protected secret, and consistent
with this design's own stated threat model (a fully compromised/logged-in
OS session is explicitly out of scope, § K of the ADR). The moment the
legitimate user notices, they revoke that device's credential from any other
signed-in device or browser — immediate (bounded by the cache TTL above),
no re-provisioning of the whole account required, and it does not touch the
P-DEK's own key material at all (revoking the sync credential stops future
uploads; it does not and cannot retroactively decrypt anything, and does
not require rotating the P-DEK itself unless the user separately suspects
the P-DEK was also exposed — a judgment call left to the user, not
automated).

## 2. Local project discovery — exact source of the list

Inspected directly: `preview_sync_local_project`/`sync_local_project` (and
every other `.stealth`-touching MCP tool) take `repo_path` as a **caller-
supplied parameter on every call** — the MCP server process holds no
persistent notion of "the current project." It is a single long-lived
process (one hard-coded port, `8765` — only one instance can bind it at a
time on a given machine) that can be, and routinely is, called with
different `repo_path`s across its lifetime by whatever agent is connected.
**There is no existing registry of "every local keळ project this machine
knows about."** Confirmed by search: no such file or table exists today.

**Therefore, for the browser to ever offer more than "the one project the
user is currently looking at," a small local registry is required — the
ADR already anticipated this, and this closes it concretely:**

- **Location**: a user-scoped application-data directory, never inside any
  single repo's `.stealth/` (it must span multiple repos) — the
  OS-appropriate convention (e.g. via Python's `platformdirs`:
  `%APPDATA%\stealthlab\local_projects.json` on Windows, `~/.config/
  stealthlab/local_projects.json` on Linux, `~/Library/Application
  Support/stealthlab/local_projects.json` on macOS). Not `~/.stealthlab`
  hardcoded — match whatever this repo's existing config-path convention is
  at implementation time if one already exists; none was found today, so
  this introduces the first one, deliberately using the standard OS
  convention rather than inventing a bespoke path.
- **Contents — exactly this, nothing else**: a JSON object keyed by
  `stable_project_id`, each entry `{repo_path, display_hint (folder
  basename), last_local_activity_at}`. **No project content, ever.**
- **Written by**: `ensure_stable_project_id` (already the one function every
  relevant call site — `init_workspace`, `generate_projection`, the sync
  preview tool — already goes through). Extend it to also upsert this one
  registry entry whenever it runs. This is the smallest possible hook: one
  additional local, best-effort file write, in a function every relevant
  code path already calls, not a new watcher and not a new daemon.
- **Read by**: the local bridge's `list-projects` step (§ C.2 of the ADR) —
  reads this one file, never touches the filesystem beyond it. **No
  recursive scan, no home-directory crawl, ever.**
- **A project only appears in it after `ensure_stable_project_id` has run
  for it at least once** — i.e., after the user has actually used keळ MCP
  in that repo at least once. A repo keळ has never touched simply isn't
  discoverable, which is correct: there's nothing to sync in it anyway.
- **Staleness**: `repo_path` in the registry can go stale if a project is
  moved (the stable id itself survives the move, per the ADR's already-
  verified `ensure_stable_project_id` behavior, but this REGISTRY's cached
  `repo_path` does not auto-follow a move done outside keळ's own tooling).
  Handled by: the next time `ensure_stable_project_id` runs against the
  *new* path (any local MCP tool call there), it upserts a fresh registry
  entry for that `stable_project_id` at the new path — self-healing on next
  use, not maintained proactively. A moved-and-never-reopened project
  simply shows its last-known path until then; harmless, since the registry
  never claims to be authoritative over anything but "what to show in a
  discovery list," never used as the source of truth for identity itself
  (that's still `.stealth/meta.json`'s `stable_project_id`, per the
  original ADR).

If a future need arises for one browser session to discover projects
across *multiple simultaneously-running* local bridge processes (e.g. two
different repos each running their own MCP server on different ports) — the
current architecture doesn't support multiple bridge processes at all
(hardcoded single port), so this is explicitly out of scope for V1 and not
addressed further here.

## 3. Argon2id library — evaluated, provisionally selected, one gate remains

Two credible browser-compatible, RFC-9106-referencing candidates were
directly inspected (not assumed from name recognition):

| | `hash-wasm` | `openpgpjs/argon2id` |
|---|---|---|
| Maintenance | Active (300+ commits, versioned npm releases) | Repository exists under the OpenPGP.js org; commit recency not confirmable from the content fetched |
| Scope | General-purpose hash library; Argon2 is one of many algorithms | Purpose-built for Argon2id only |
| Dependencies | Zero | Not confirmed to have any beyond the bundled WASM |
| Bundle size | ~11 KB (gzipped, Argon2 module only, tree-shakeable) | < 7 KB minified+gzipped, WASM inlined as base64 |
| SIMD | Not confirmed | Yes, with automatic non-SIMD fallback (relevant for Safari) |
| Test vectors | Not confirmed from available content | Referenced but not confirmed present in the fetched content |
| **Third-party security audit** | **No evidence found** | **No evidence found** |
| Bundler requirement | Works via CDN without a bundler | Works without a bundler; also supports Rollup/Webpack |

**Neither candidate has a documented, named, third-party security audit.**
Per this task's own instruction not to claim one without explicit evidence,
none is claimed here for either.

**Provisional V1 recommendation: `openpgpjs/argon2id`** — narrower scope
(does exactly one thing, reducing the attack surface relevant to this use
case versus a multi-algorithm library), smaller bundle, SIMD-aware with a
correct Safari fallback, and maintained under an organization whose own
flagship product (OpenPGP.js, used in production by e.g. ProtonMail) has
its own strong incentive to keep this correct. This is a **provisional**
selection, not a closed one: **a dedicated review of this specific
library's source (not just its README) — ideally a short, scoped, paid or
volunteer security review, or at minimum a maintainer-track-record and
CVE-history check beyond what a content fetch can establish — must happen
before this ships to real users.** That review is the one concrete,
named gate this closure section leaves open. It does not block designing or
building the rest of the system: the interface this library sits behind
(`deriveRecoveryKEK(passphrase, salt, params) → CryptoKey`) is stable and
swappable regardless of which WASM implementation answers it, so
implementation of §§ C–J of the ADR can proceed in parallel with that
review.

**Parameters — reassessed, not left at the OWASP password-storage
baseline**: RFC 9106 §4 gives two reference points — "2 GiB RAM, t=1, p=4"
(first recommendation, general-purpose) and "64 MiB RAM, t=3, p=4"
(second recommendation, memory-constrained) — both aimed at *server-side*
authentication throughput budgets, and both explicitly heavier than what a
browser tab (especially on a mobile device, single-threaded WASM, no
guaranteed multi-GB headroom) can reliably run without risking an OOM
crash or the tab appearing to hang. OWASP's Password Storage baseline
(`m=19456` KiB / 19 MiB, `t=2`, `p=1`) is explicitly a *minimum* for
password *hashing under frequent-login latency pressure* — this operation
runs rarely (once at recovery-passphrase setup, and again only on a new
device's recovery), so the UX budget is genuinely different: a few seconds,
once, is acceptable in a way it would not be on every login.

**Recommended V1 parameters: `m=65536` KiB (64 MiB), `t=3`, `p=1`** —
RFC 9106's memory-constrained recommendation's memory/time values, with
parallelism reduced from 4 to 1. Parallelism reduction is deliberate, not
arbitrary: Argon2's `p` lanes only deliver a real security/throughput
trade-off when the implementation can execute them concurrently across
threads; a single-threaded (or non-`SharedArrayBuffer`-backed) WASM
execution context — the common case for a browser tab that hasn't opted
into cross-origin-isolation — runs lanes sequentially, at which point `p>1`
mainly multiplies wall-clock time without adding the parallel-hardware
resistance `p` is meant to buy. `m=64 MiB, t=3` alone already exceeds
OWASP's stated minimum by more than 3x on the memory axis, which is the
axis that matters most against GPU/ASIC attackers.

**Desktop benchmark — now measured for real** (implementation pass,
post-design): run live in the actual `openpgpjs/argon2id` WASM build this
codebase ships (`prod_frontend/public/wasm/argon2id-{simd,no-simd}.wasm`),
via the real browser this session had access to (a desktop-class Chromium
environment), 3 runs each after a warm-up run to exclude one-time
WASM-instantiation overhead:

| Parameters | Runs (ms) | Avg |
|---|---|---|
| `m=64 MiB, t=3, p=1` (chosen) | 413, 301, 306 | **340 ms** |
| `m=32 MiB, t=3, p=1` | 167, 167, 150 | 161 ms |
| `m=19 MiB, t=2, p=1` (OWASP password-storage baseline) | 67, 60, 59 | 62 ms |

The chosen parameters complete in ~0.3s on this desktop-class machine —
comfortably inside the "1–3 seconds on a modern laptop" target, with
headroom to spare. **Still not measured: a real low/mid-range mobile
device** (this session has no physical or emulated real-hardware mobile
browser attached, only viewport emulation, which does not reflect real
mobile CPU/WASM performance) — that measurement remains a required,
explicitly-tracked pre-ship gate, not a design blocker. If it comes in
over the ~5s mobile target, drop to the `m=32 MiB` row first (already
measured above, no re-derivation needed) before touching `t`.

## 4. ADR review — corrections and refinements

- **AES-GCM nonce/IV**: the ADR's "~2^32 encryptions per key before
  meaningful collision risk, for a random 96-bit IV" is the standard,
  correctly-cited NIST SP 800-38D guidance and needs no correction. Given
  V1's per-project key volume (one project, incremental deltas, realistically
  thousands, not billions, of encryptions over a project's lifetime), this
  is not a practical concern — noted, not re-argued.
- **Key wrapping mechanism — refined, not changed in substance**: the ADR
  described wrapping as "AES-256-GCM encrypting the raw key bytes." The
  concrete, spec-correct way to do this in the browser is `SubtleCrypto`'s
  native `wrapKey()`/`unwrapKey()` operations (a standard part of the Web
  Crypto API, alongside `encrypt`/`decrypt`/`deriveKey`), rather than
  manually exporting key bytes and calling `encrypt()` on them as if they
  were an ordinary plaintext buffer — `wrapKey` handles the key-format
  export/import correctly and is the idiomatic call for exactly this
  operation. Same primitive (AES-GCM), same security property, more
  correct API usage. Recorded here as an implementation-time correction,
  not an architecture change.
- **OS keychain — a real gap, now documented, not previously stated**:
  `keyring` (§ F, § 1) requires a working backend at runtime — Windows
  Credential Manager and macOS Keychain are always present, but on Linux
  it requires a running Secret Service provider (GNOME Keyring, KWallet,
  etc.), which is **not guaranteed on a headless or minimal Linux dev/CI
  machine.** Required behavior, not previously specified: if no keychain
  backend is available, **ongoing local auto-sync for that machine is
  disabled, fail-closed** — the local bridge must not fall back to writing
  the P-DEK or the sync device credential to a plaintext file merely
  because the "correct" storage isn't available. The user is told plainly
  that automatic sync isn't available on this machine, and that sync can
  still happen manually the next time a browser session performs the
  loopback handshake (which holds the key only in memory for that
  operation, never needing durable local storage at all). This is an
  availability trade-off stated honestly, not a silent security downgrade.
- **Metadata — confirmed as designed**: re-reading § L against § C/§ D
  together, the boundary holds: `display_hint` genuinely never crosses the
  loopback interface to the remote server (it's returned by `list-
  projects` to the browser for local display only; nothing in the upload
  path in § C/F carries it further). No correction needed, but worth
  restating since it's easy to get this wrong by accident at
  implementation time — this is exactly the kind of thing a test (§ Test
  plan, "no plaintext project content in the request body") must actually
  check, not just this document asserting it.
- **Browser storage claims**: unchanged from the ADR — non-extractable
  `CryptoKey` for the session, no `localStorage`. No correction found.
- **Capability token lifecycle**: unchanged; the closure adds that the
  bridge must check `Origin`/`Host` **before** parsing any request body,
  including on `start-handshake` (the one step with no prior capability to
  check) — rejecting on transport-level identity before ever looking at
  the payload is a small but real ordering requirement worth stating
  explicitly rather than leaving implicit.
- **Logging/telemetry**: unchanged; § 1's new sync-device-credential routes
  fall under the exact same "never log the body, only method/path/status/
  subject/size" rule already stated for the upload/bridge routes.
- **Deletion semantics**: unchanged; § 1 adds that revoking a sync device
  credential is immediate (subject to the existing cache-TTL observation
  window) and independent of whether the P-DEK itself is ever rotated.
- **Account recovery**: unchanged in substance; no correction found.
- **OAuth vs. encryption-key separation**: unchanged; § 1's new credential
  type adds a *third* clearly-separated trust domain (human OIDC / worker
  service tokens / sync device tokens) rather than blurring the existing
  two-domain separation.

## 5. Server-access review — RLS, privilege, and the real enforcement boundary

**Verified directly against the live database, not assumed:**

```
connected as role: postgres
rolbypassrls: True
```

This backend's `DATABASE_URL` connects as `postgres`, and that role carries
`BYPASSRLS`. Per Supabase's own documentation (fetched directly): *"A
secret key authorizes access through the `service_role` Postgres role,
which has the `bypassrls` attribute"* and *"Backend systems connecting with
elevated privileges (service_role or roles with bypassrls) operate outside
RLS protections."* That is exactly this backend's situation.

**Honest consequence, stated plainly**: Row-Level Security, as currently
configured anywhere in this repository, provides **zero** enforcement for
this backend's own queries — not for the five existing tables migration 29
already added policies to (`evidence`, `executions`, `change_sets`,
`change_set_operations`, `failure_routes`), and it would provide zero
enforcement for `synced_projects`/`raw_objects` even if RLS policies were
added to them today, because `BYPASSRLS` overrides `FORCE ROW LEVEL
SECURITY` regardless of table ownership. Migration 29's own docstring
already half-anticipated this ("this deployment's backend typically
connects as the owner... the backstop would be decorative exactly where it
matters") but the actual situation is stronger than that: it's not just
table ownership, the connection role itself is explicitly privileged to
ignore RLS everywhere, unconditionally.

**Why this is not, today, a hole**: the browser never connects to Postgres
directly, under any role, RLS-bypassing or not — it only ever talks to
this FastAPI backend over HTTPS, and the backend is the sole holder of
`DATABASE_URL`. Every `/v1/me/*` route (including the new sync routes this
design adds) already enforces ownership entirely in application code —
`WHERE owner_subject = $1`, `principal.subject` sourced only from a
verified token, never a client-supplied field — the exact pattern already
proven for `/v1/me/stealth-projects` and to be reused unchanged for the
new sync-upload/fetch routes. This is a legitimate, sufficient enforcement
boundary *given that access path*, and Supabase's own guidance doesn't say
otherwise — it says RLS is the boundary once you introduce a
*lower-privileged, RLS-subject* connection path (e.g. exposing a
PostgREST/anon-key path directly to the browser), which this repository
does not do and this design does not introduce.

**Recommendation, as genuine defense-in-depth (not required for
correctness given the above, and explicitly not implemented in this
pass)**: extend the *exact* pattern migration 29 already established —
`ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + one policy
function — to `synced_projects` and `raw_objects`, keyed on an
`owner_subject`-bound `SET LOCAL` GUC (a sibling of `sl_tenant_scope_
allows`, since the isolation axis here is per-account, not per-org-tenant,
so it needs its own function, not a call into the existing tenant one).
This buys real protection **only** in a future where either (a) the
connection role is ever changed to drop `BYPASSRLS`, or (b) a second,
lower-privileged connection path is ever added (e.g. a future direct-from-
browser Supabase client path for some other feature). Recorded here as a
recommended follow-up migration, not written or applied in this pass, per
this task's explicit "no migration" instruction.

**Other checks in this section, verified:**

- **No service/secret key ever reaches the browser**: confirmed unchanged —
  `prod_frontend`'s only Supabase-related client config is
  `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` (public by
  Supabase's own design for the anon key), and `DATABASE_URL`/any service
  credential lives only in `backend/.env`, never shipped to any frontend
  bundle (confirmed by the existing build output inspected throughout this
  project — no backend secret has ever appeared in a Next.js build
  artifact).
- **Privileged backend paths still perform explicit ownership checks**:
  confirmed — every `/v1/me/*` route, and the two new sync routes this
  design adds, always filter by the verified `principal.subject`, never a
  request parameter (the same discipline this session's own earlier audits
  of `/v1/me/stealth-projects` already verified with a passing regression
  test for exactly this).
- **Object storage access does not cross accounts**: `object_storage.py`'s
  `sha256`-addressed `raw_objects` table has no owner column of its own by
  design (content-addressing means the SAME ciphertext bytes, if two
  accounts somehow produced byte-identical output, would share one stored
  blob) — this is fine for the *existing* usage (nothing sensitive is
  addressable without also knowing its locator, which is only ever handed
  out via an ownership-checked `synced_projects`/`raw_objects` join), and
  remains fine for encrypted payloads specifically because ciphertext is
  never byte-identical across independent encryptions even of identical
  plaintext (random IV) — so cross-account content collision in the object
  store is not just access-controlled but, for encrypted payloads,
  essentially statistically impossible in addition to being access-
  controlled. Access is still gated by the owning `synced_projects` row's
  `owner_subject`, never by knowledge of a locator alone.
- **No public route exposes synced project data**: re-confirmed by the same
  grep sweep as the original ADR — only `app/api/me.py`, `app/mcp_server/
  server.py`, and `app/stealth/project_sync.py` reference this feature's
  tables/functions; nothing in `problems.py`, `contributors.py`,
  `publications.py`, or search touches it.

## 6. Remaining unresolved issues

**Update (post-implementation pass):** the system described in §§ A–J and
this closure has since been built and tested (backend: migration 109,
`sync_device_identity.py`, `local_registry.py`, `local_key_store.py`,
`local_sync_bridge.py`, `local_sync_encrypt.py`, `ongoing_sync.py`,
`unsync_local_project`; frontend: `sync-crypto.ts`, `local-bridge.ts`,
`sync.ts`, the `/account` sync flow and `/account/projects/[id]` decrypt
flow). The four items below were re-examined at that point; two are now
closed, two remain open for the reasons stated.

1. **Argon2id library formal review** (§ 3) — **still open.** Provisionally
   selected, audit/review pending; this requires a dedicated human security
   review this session cannot perform. Gates shipping that one component,
   not the rest.
2. **Real-device Argon2id benchmark** (§ 3) — **desktop: closed, measured
   for real** (413/301/306ms, avg 340ms, for the chosen `m=64MiB,t=3,p=1`
   parameters, in the actual shipped WASM build, via a real browser this
   session had access to — see § 3's own updated table, including two
   lighter fallback parameter sets already measured in case they're ever
   needed). **Mobile: still open** — this session has no real or emulated
   low/mid-range mobile hardware attached; viewport emulation does not
   reflect real mobile CPU/WASM performance, so no mobile number is
   claimed.
3. **RLS defense-in-depth migration for `synced_projects`/`raw_objects`**
   (§ 5) — still open, unchanged from the original assessment: recommended,
   not required for correctness given the current single-connection-path
   architecture (verified: this backend's connection role has
   `rolbypassrls=true`), becomes load-bearing only if that architecture
   changes.
4. **Local registry file path convention** (§ 2) — **closed.** Implemented
   via `platformdirs.user_data_dir("stealthlab", ...)` — the standard,
   cross-platform convention, and (confirmed at implementation time) no
   pre-existing config-directory convention existed elsewhere in this
   codebase to conflict with.

**Also newly closed in the implementation pass, beyond the original four:**
the ongoing-sync hook (§ G) is now wired into three existing local write
points, not just one — `record_stealth_edit`, `record_run_update`, and
`close_exploration` — each verified by an offline test that the hook
actually fires with the right arguments. New-device recovery (§ H) also
gained a real follow-on: after client-side decryption via the recovery
passphrase, `/account/projects/[id]` can now hand the already-unwrapped
P-DEK to a local bridge on that same device too (if one exists and has its
own checkout of the same project), enabling ongoing sync from a second
device without any new backend surface — reusing the existing
handshake/prepare/register chain unchanged.

---

**Verdict: DESIGN CLOSED**, with the four items above tracked as named,
non-blocking residuals to close before this ships to real users — not
before implementation begins.
