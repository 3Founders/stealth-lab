# Local chat database: how other harnesses do it, and what kell should build

Research date: 2026-11-10. Sources are linked at the end. This note is the design input for kell's own
chat store, independent of OpenCode's `opencode.db`.

## Why a separate store

Today the desktop app's history is OpenCode's history:

- sessions and messages live in `~/.local/share/opencode/opencode.db`;
- turn snapshots live in `~/.local/share/opencode/snapshot/<project>/<repo>` (a shadow git);
- the app reads the engine's schema directly for the file view, diff, undo and session list.

That is fine while OpenCode is the only executor. It is wrong for a product that:

- imports Claude Code and Codex chats;
- wants its own titles, attachments, summaries and search;
- must survive an engine upgrade that changes `session_message.data`;
- should not lose the user's history if the engine database is deleted or replaced.

The local store is the app's durable history. The engine remains the executor.

## What other harnesses do

### DeepSeek Harness (DSH)

DSH is the most useful reference because it treats persistence as a set of contracts, not "the database".

**Session is an event log.** A session is an append-only `SessionEvent` log and is the single source of
truth. The model's message history is derived from it by `deriveMessages()`; there is no parallel
"persisted message" type. Their rule is **"model-visible <=> logged"**: anything that reaches a model
request must be reconstructable from the log. Failed, retried and cancelled attempts are kept as
`assistant/attempt` events without entering model history.

**Default persistence is JSONL, compressed.** `session-persistence-jsonl` writes one logical JSONL log per
session under `$DSH_HOME/sessions/<workspace>/<session>/session.vN.jsonl.zstd`:

- zstd compression by default, plain JSONL by configuration;
- versioned generations (`session.jsonl`, `session.v1.jsonl`, ...), selected by the highest version;
- atomic publication, crash-safe writes, interrupted-turn repair;
- one live writer per session (in-process claim plus a file lock).

**SQLite is an optional provider.** `@deepseek-ai/dsh-session-persistence-sqlite` is an opt-in
`SessionPersistence` backend:

- schema 17, ordinary ROWID tables, composite primary key `events(session_id, seq)`;
- one row per logical event, except packed rows for consecutive same-block text, reasoning or tool-call
  deltas (`text-chunks`, `reasoning-chunks`, `tool-call-chunks`);
- at most 1,024 events and 1 MiB of uncompressed text per packed row;
- payloads below 4 KiB stay `TEXT`; at or above 4 KiB the writer uses zstd level 3 and stores a `BLOB`
  only if the frame is smaller;
- provenance sequence numbers are delta-encoded (ZigZag varints);
- reads reconstruct the exact logical `SessionEvent[]`; physical tags never reach prompts or tools;
- no migration path in the pre-release implementation; a schema mismatch refuses.

**Search is a separate derived index.** `session-query-sqlite` uses SQLite FTS5 (`unicode61`) for
full-text search over sessions and events. It is a derived index, not the authoritative log; it can be
opened lazily and can be `:memory:`. Exact reads and relationship traces do not depend on the index.

**Attachments are content-addressed objects.** The attachment store owns immutable binary objects;
session events keep only content-addressed references. The database never stores the bytes inline.

**Three persistence families, deliberately separate:**

1. `SessionPersistence` — the authoritative event log and its header;
2. generic Storage/Domain — host-side records with a consumer-owned schema;
3. attachment store — immutable blobs by hash.

**Takeaways for kell:**

- separate the authoritative history from any derived search index;
- keep an event or message log that can reconstruct what the model saw;
- compress large payloads, but keep small searchable text as text;
- store attachments by reference/hash, not base64 in every message;
- version the schema and fail loudly rather than silently misreading;
- serialise writes and handle interrupted turns.

### Claude Code

Claude Code writes one JSONL transcript per session under:

```text
~/.claude/projects/<encoded-launch-cwd>/<session-uuid>.jsonl
```

Subagent transcripts nest under `subagents/agent-*.jsonl`. The SDK exposes a `SessionStore` adapter so
transcripts can be mirrored to object storage, a key-value store or a database. `SessionKey` is
`projectKey + sessionId + subpath`.

Important trap: the encoded directory is the **launch cwd**, not necessarily the session's own `cwd`.
A session launched from one directory but pointed at another repo is filed under the launch path. Record
both values; do not infer the project from the folder name alone.

**Takeaways:** JSONL is easy to inspect and append to, but the directory encoding is lossy. If kell reads
Claude transcripts, it must use the `cwd` recorded in the events and store its own project/session
metadata.

### Codex CLI

Codex writes one rollout per session under:

```text
~/.codex/sessions/<year>/<month>/<day>/rollout-<timestamp>-<uuid>.jsonl[.zst]
```

- `session_meta` is the header (id, cwd, originator, CLI version, source);
- `event_msg` carries `user_message`, `agent_message`, `item_completed`, token counts, task boundaries;
- `response_item` carries model messages, tool calls and reasoning;
- `~/.codex/session_index.jsonl` is a separate name index (`id`, `thread_name`, `updated_at`);
- older files may be uncompressed; newer ones are zstd frames.

**Takeaways:** date-sharded directories and zstd keep a large history manageable; a small side index for
titles is cheaper than reading every rollout for a name; a session has several event shapes, so a
migration layer is needed.

### OpenCode

OpenCode uses one SQLite database, `~/.local/share/opencode/opencode.db`, with `session` and
`session_message` tables. Message payloads are JSON in `session_message.data`; turn snapshots are in a
shadow git under `~/.local/share/opencode/snapshot`. The desktop app currently reads this database
directly (read-only) for sessions, diffs and undo.

**Observed in 2.2.5 (2026-11-10):** `experimental.session.import` accepts a full `Session.Info` and a
list of user and assistant messages, but only materialises the **user** messages in `session_message`.
An import of `[user, assistant]` returned 200 and `/api/session/:id/message` returned only the user
row; the assistant row was not stored. This is why imported Claude/Codex chats showed only the person's
prompts in the app.

**Takeaways:** SQLite is good for live queries, but the app should not couple its product schema to the
engine's internal JSON. A separate local DB with an explicit mapping is the safer boundary. The local
store must own the imported transcript, not rely on the engine import to preserve assistant turns.

### Hermes Agent

Hermes uses `~/.hermes/state.db`, a SQLite database in WAL mode:

- `sessions` (metadata, model, token/cost accounting, workspace fields);
- `messages` (role, content, tool calls, reasoning, timestamps, token counts);
- `messages_fts` and `messages_fts_trigram` (FTS5 external-content indexes);
- `state_meta` (key/value);
- the gateway is the only writer of canonical sessions; other surfaces write through it.

**Takeaways:** WAL + FTS5 + a single writer is a proven local-chat design. The FTS index is derived and
kept in sync by triggers; it is not the source of truth.

### Ethos

Ethos uses `~/.ethos/sessions.db` with SQLite WAL, `STRICT` tables, and an FTS5 external-content index
kept in sync by insert/update/delete triggers. It exposes a `SessionStore` interface and appends each
completed message as a whole row; a failed turn keeps the user message but not a partial assistant
message.

**Takeaways:** append whole messages, not stream deltas; keep the user message even when the assistant
turn fails; `STRICT` tables catch schema drift early.

### Aider

Aider keeps human-readable chat history in `.aider.chat.history.md` and input history in
`.aider.input.history` in the repository. It is not a database, but it is a useful export format and a
reminder that a plain-text/Markdown export is valuable.

## What kell should build

### Scope

The local DB is the app's history and search store. The engine remains the executor. A session has:

- a local id (the app's stable id);
- an optional engine id (the OpenCode session used for continuation);
- source (`kell`, `claude`, `codex`, `opencode`);
- title, directory, timestamps, metadata;
- messages in order, with attachments and optional summaries.

### Location

```text
~/.stealthlab/chats.db
```

Use SQLite WAL, `foreign_keys=ON`, `busy_timeout`, and `STRICT` tables where supported. The file is
local-only; nothing in it is sent anywhere by the store itself.

### Schema (v1)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  engine_id   TEXT,
  source      TEXT NOT NULL DEFAULT 'kell',
  title       TEXT,
  directory   TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  metadata    TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  role        TEXT NOT NULL,
  text        TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  model       TEXT,
  provider    TEXT,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cost_usd    REAL,
  summary     TEXT,
  attachments TEXT,
  UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text,
  content='messages',
  content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.rowid, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS attachments (
  id          TEXT PRIMARY KEY,
  message_id  TEXT REFERENCES messages(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  mime        TEXT,
  size        INTEGER,
  rel         TEXT,
  path        TEXT,
  sha256      TEXT,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  from_seq    INTEGER NOT NULL,
  to_seq      INTEGER NOT NULL,
  level       INTEGER NOT NULL DEFAULT 0,
  text        TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
```

Notes:

- `messages.text` is the full text for the UI and search. `summary` is the compressed form later sent to
  providers; keeping both means the UI can show the full reply while the model sees the short one.
- `attachments` keeps paths and hashes, not bytes. The engine reads a path when it needs the content.
- `messages_fts` is derived; it can be rebuilt from `messages`.
- `summaries` is for hierarchical compression (L0/L1/L2) later; v1 can leave it empty.

### Write path

- **`/api/prompt`**: ensure the session row; append the user message with attachment metadata; stream the
  turn; accumulate `session.text.delta`; append one assistant message when the turn ends (success or
  failure). A failed turn keeps the user message and whatever assistant text arrived.
- **`/api/import`**: after the engine import succeeds, write the imported turns to the local DB with
  `source = claude|codex|opencode`. The local row is the app's copy; the engine id is stored for
  continuation.
- **`/api/local/sync`**: backfill existing engine sessions. List engine sessions, create missing local
  rows, and lazily fetch a session's messages when it is first opened.

### Read path

- **`/api/local/sessions`**: list local sessions, newest first.
- **`/api/local/sessions?id=`**: messages for one session, in `seq` order.
- The UI switches to these routes. If a local session has no messages yet, the route backfills from the
  engine once and then serves the local copy.
- Search: add `/api/local/search?q=` over `messages_fts` after v1.

### Migration

1. Create `chats.db` on first use.
2. Backfill existing engine sessions (list + messages) on first load.
3. Imported Claude/Codex/OpenCode chats are written to both the local DB and the engine.
4. If the engine database is deleted, the local DB still has the history; opening a session for
   continuation can recreate the engine session from the local messages.

### Tradeoffs

- **Duplication.** The engine and the local DB both hold messages. That is deliberate: the engine's copy
  is for execution; the local copy is for the product.
- **Ordering.** Use `seq` per session, not timestamps; timestamps are for display.
- **Compression.** Keep full text in `messages`; store summaries separately. Do not replace the full text
  until the UI has a way to show it.
- **Attachments.** Path references are cheaper than bytes and keep the DB small. Bytes are only inlined
  when the file is outside the project or is an image/PDF the model must see.
- **Privacy.** The local DB is local-only. A later export/backup feature should be explicit, not implicit.

### Implementation order

1. `desktop/lib/localdb.ts` — schema, WAL, helpers (`ensure`, `upsertSession`, `appendMessage`,
   `listSessions`, `getMessages`, `importSession`).
2. `/api/local/sessions` and `/api/local/sync`.
3. Write-through in `/api/prompt` and `/api/import`.
4. Switch `page.tsx`'s `loadSessions` / `openSession` to the local routes.
5. Backfill existing engine sessions.
6. Add FTS search and summaries.

## Sources

- DeepSeek Harness, `packages/session/session-persistence-sqlite/README.md`:
  https://github.com/deepseek-ai/deepseek-harness/blob/141eb6fef83422698aef7a981029e843e8161534/packages/session/session-persistence-sqlite/README.md
- DeepSeek Harness, persistence and database design:
  https://hoco-scy.github.io/deepseek-harness-deep-dive/pages/persistence-database.html
- DeepSeek Harness, session query (FTS5):
  https://deepseekdocs.com/en/docs/features/session-query
- DeepSeek Harness, the session system (JSONL, generations, zstd):
  https://deepseekdocs.com/en/docs/learn/core/session
- DeepSeek Harness, persistence subsystem:
  https://github.com/deepseek-ai/deepseek-harness/blob/141eb6fef83422698aef7a981029e843e8161534/docs/subsystems/persistence.md
- Claude Code, persist sessions to external storage:
  https://code.claude.com/docs/en/agent-sdk/session-storage
- Claude Code JSONL transcript format:
  https://dxdev.com/blog/reverse-engineer-claude-code-jsonl-transcript-format/
- Hermes Agent, session storage:
  https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage
- Ethos, sessions and history:
  https://ethosagent.ai/docs/using/explanation/sessions-and-history.md
- DeepSeek Harness architecture notes:
  https://martianlee.github.io/posts/2026-09-05-deepseek-harness-architecture
- Aider chat history (local Markdown files):
  https://aider.chat/docs/usage/conventions.html
- Codex CLI local rollout format (observed on this machine; see `~/.codex/sessions` and
  `~/.codex/session_index.jsonl`).
- OpenCode local store (observed on this machine; `~/.local/share/opencode/opencode.db`).
