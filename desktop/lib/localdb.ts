// The app's own chat store, independent of OpenCode's opencode.db (docs/localdb.md).
//
// SQLite in WAL mode: sessions, messages, attachments and summaries. The engine still executes turns; this is the
// durable history the UI reads, so an engine upgrade or a deleted engine database cannot take the person's chats with
// it. Attachments are references (path/rel/hash), never bytes in the message row. FTS5 is a derived index; if the
// SQLite build lacks FTS5 the store still works and search is simply unavailable.
import { randomUUID } from "node:crypto"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { DatabaseSync } from "node:sqlite"

export type LocalSession = {
  id: string; engine_id: string | null; source: string; title: string | null; directory: string | null
  created_at: number; updated_at: number; metadata: string | null
}
export type LocalMessage = {
  id: string; session_id: string; seq: number; role: string; text: string; created_at: number
  model: string | null; provider: string | null; tokens_in: number | null; tokens_out: number | null
  cost_usd: number | null; summary: string | null; attachments: string | null
}
export type InMessage = {
  role: "user" | "assistant" | "system"; text: string; at?: number; model?: string; provider?: string
  tokensIn?: number; tokensOut?: number; cost?: number; summary?: string; attachments?: unknown
}

const SCHEMA = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
-- RAM: a small page cache, temporary sorting on disk, and a bounded WAL. The chat store is read in small pages, so a
-- large cache buys nothing and only raises the Next server's resident set.
PRAGMA cache_size = -4000;
PRAGMA temp_store = FILE;
PRAGMA mmap_size = 0;
PRAGMA synchronous = NORMAL;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA journal_size_limit = 33554432;

CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  engine_id   TEXT,
  source      TEXT NOT NULL DEFAULT 'kell',
  title       TEXT,
  directory   TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  metadata    TEXT
) STRICT;

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
) STRICT;

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

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
) STRICT;

CREATE TABLE IF NOT EXISTS summaries (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  from_seq    INTEGER NOT NULL,
  to_seq      INTEGER NOT NULL,
  level       INTEGER NOT NULL DEFAULT 0,
  text        TEXT NOT NULL,
  created_at  INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS projects (
  path            TEXT PRIMARY KEY,
  name            TEXT,
  last_opened_at  INTEGER NOT NULL,
  hidden          INTEGER NOT NULL DEFAULT 0
) STRICT;
`

const FTS = `
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text, content='messages', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
END;
`

const file = () => path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "chats.db")

let db: DatabaseSync | null = null
let fts = false

function open(): DatabaseSync {
  if (db) return db
  const p = file()
  fs.mkdirSync(path.dirname(p), { recursive: true })
  db = new DatabaseSync(p)
  db.exec(SCHEMA)
  try { db.exec(FTS); fts = true } catch { fts = false }   // FTS5 is an addition; the store works without it
  return db
}

export const localDbPath = () => file()
export const hasFts = () => { open(); return fts }

const titleOf = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 80)

/** Create a session if it is not there; update its title/directory/engine id when given. */
export function ensureSession(s: {
  id?: string; engineId?: string | null; source?: string; title?: string | null; directory?: string | null
  createdAt?: number; updatedAt?: number; metadata?: unknown
}): string {
  const d = open()
  const id = s.id || randomUUID()
  const now = Date.now()
  const existing = d.prepare("select id from sessions where id = ?").get(id) as { id: string } | undefined
  if (existing) {
    d.prepare(`update sessions set
      engine_id = coalesce(?, engine_id), title = coalesce(?, title), directory = coalesce(?, directory),
      updated_at = ?, metadata = coalesce(?, metadata) where id = ?`)
      .run(s.engineId ?? null, s.title ?? null, s.directory ?? null, s.updatedAt ?? now,
           s.metadata === undefined ? null : JSON.stringify(s.metadata), id)
  } else {
    d.prepare(`insert into sessions (id, engine_id, source, title, directory, created_at, updated_at, metadata)
      values (?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(id, s.engineId ?? null, s.source || "kell", s.title ?? null, s.directory ?? null,
           s.createdAt ?? now, s.updatedAt ?? now, s.metadata === undefined ? null : JSON.stringify(s.metadata))
  }
  return id
}

/** Append one whole message (stream deltas are assembled by the caller). Returns its id. */
export function appendMessage(sessionId: string, m: InMessage): string {
  const d = open()
  const id = randomUUID()
  const at = m.at ?? Date.now()
  const next = d.prepare("select coalesce(max(seq), 0) + 1 as n from messages where session_id = ?").get(sessionId) as { n: number }
  d.prepare(`insert into messages (id, session_id, seq, role, text, created_at, model, provider, tokens_in, tokens_out, cost_usd, summary, attachments)
    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(id, sessionId, next.n, m.role, m.text, at, m.model ?? null, m.provider ?? null,
         m.tokensIn ?? null, m.tokensOut ?? null, m.cost ?? null, m.summary ?? null,
         m.attachments === undefined ? null : JSON.stringify(m.attachments))
  d.prepare("update sessions set updated_at = ?, title = coalesce(title, ?) where id = ?")
    .run(at, m.role === "user" ? titleOf(m.text) : null, sessionId)
  return id
}

/** Write an imported conversation in one transaction. Returns the local session id. */
export function importSession(s: {
  id?: string; engineId?: string | null; source: string; title?: string | null; directory?: string | null
  createdAt?: number; updatedAt?: number; messages: InMessage[]
}): string {
  const d = open()
  const id = ensureSession(s)
  d.exec("BEGIN")
  try {
    for (const m of s.messages) appendMessage(id, m)
    d.prepare("update sessions set title = coalesce(?, title), updated_at = ? where id = ?")
      .run(s.title ?? null, s.updatedAt ?? Date.now(), id)
    d.exec("COMMIT")
  } catch (e) {
    try { d.exec("ROLLBACK") } catch { /* already rolled back */ }
    throw e
  }
  return id
}

export function sessionExists(id: string): boolean {
  const d = open()
  return !!d.prepare("select 1 from sessions where id = ?").get(id)
}

export function messageCount(sessionId: string): number {
  const d = open()
  const r = d.prepare("select count(*) as n from messages where session_id = ?").get(sessionId) as { n: number }
  return r?.n ?? 0
}

export function listSessions(limit = 200): LocalSession[] {
  const d = open()
  return d.prepare("select * from sessions order by updated_at desc limit ?").all(limit) as LocalSession[]
}

export function getSession(id: string): LocalSession | undefined {
  const d = open()
  return d.prepare("select * from sessions where id = ?").get(id) as LocalSession | undefined
}

export function getMessages(sessionId: string, limit = 500): LocalMessage[] {
  const d = open()
  return d.prepare("select * from messages where session_id = ? order by seq asc limit ?").all(sessionId, limit) as LocalMessage[]
}

/** Only the assistant rows a prompt needs for its bounded "prior work" summary: never the whole transcript. */
export function getAssistantMessages(sessionId: string, limit = 12): LocalMessage[] {
  const d = open()
  return (d.prepare("select * from messages where session_id = ? and role = 'assistant' order by seq desc limit ?")
    .all(sessionId, limit) as LocalMessage[]).reverse()
}

export function setSummary(messageId: string, summary: string): void {
  const d = open()
  d.prepare("update messages set summary = ? where id = ?").run(summary, messageId)
}

/** Full-text search over message text (newest first). Empty when this SQLite build has no FTS5. */
export function search(query: string, limit = 50): { session_id: string; message_id: string; text: string; created_at: number }[] {
  const d = open()
  if (!fts || !query.trim()) return []
  try {
    return d.prepare(`select m.session_id, m.id as message_id, m.text, m.created_at
      from messages_fts f join messages m on m.rowid = f.rowid
      where messages_fts match ? order by m.created_at desc limit ?`).all(query, limit) as any[]
  } catch { return [] }
}

// ---- Projects -----------------------------------------------------------------------------------------------------
// The recent-project list lives here, not in localStorage: the app's origin changes every start and the boot script used
// to clear it. A project is a folder path; `last_opened_at` orders the list, `hidden` is the × in the sidebar.
export type LocalProject = { path: string; name: string | null; last_opened_at: number; hidden: number }

export function listProjects(limit = 20): LocalProject[] {
  const d = open()
  return d.prepare("select path, name, last_opened_at, hidden from projects where hidden = 0 order by last_opened_at desc limit ?").all(limit) as LocalProject[]
}

/** Whether the store has ever been filled, hidden rows included (the one-time engine seed must not run again after ×). */
export function anyProjects(): boolean {
  const d = open()
  return d.prepare("select 1 from projects limit 1").get() !== undefined
}

export function rememberProject(path: string, name?: string | null): void {
  const d = open()
  d.prepare(`insert into projects (path, name, last_opened_at, hidden) values (?, ?, ?, 0)
    on conflict(path) do update set last_opened_at = excluded.last_opened_at, hidden = 0,
      name = coalesce(excluded.name, projects.name)`).run(path, name ?? null, Date.now())
}

/** Add projects discovered from the engine without putting them above the ones the person opened here. */
export function mergeProjects(paths: string[]): void {
  const d = open()
  for (const p of paths) {
    if (!p) continue
    d.prepare("insert into projects (path, name, last_opened_at, hidden) values (?, null, 0, 0) on conflict(path) do nothing").run(p)
  }
}

/** First-run seed from the engine's own project table; existing rows win. */
export function importProjects(rows: { path: string; last: number }[]): void {
  const d = open()
  for (const r of rows) {
    if (!r.path) continue
    d.prepare("insert into projects (path, name, last_opened_at, hidden) values (?, null, ?, 0) on conflict(path) do nothing")
      .run(r.path, r.last || 0)
  }
}

export function forgetProject(path: string): void {
  const d = open()
  d.prepare("update projects set hidden = 1 where path = ?").run(path)
}

export function unhideProject(path: string): void {
  const d = open()
  d.prepare("update projects set hidden = 0 where path = ?").run(path)
}
