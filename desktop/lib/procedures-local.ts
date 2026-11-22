// Drafts of a Way, on this machine (2026-11-18). A draft is a procedure a person is still writing: the goal, the steps,
// and the model each step runs on. It lives in the app's own SQLite file next to the chats (lib/localdb.ts's chats.db),
// never in Postgres, so it costs nothing on the server and cannot be seen by anyone else until the person submits it.
//
// Why local and not a `draft` status on the server: the server only runs a Way whose status is ai_approved or approved
// (submissions/runs.py:392). A server-side draft that its owner could run would also be runnable by everyone the Way's
// visibility reaches, which is the review gate this system exists to keep. A local draft runs on the person's own engine,
// under their own account, and becomes a submission only when they choose to send it.
//
// The row is the draft as the person left it: `steps` is the JSON the form holds, so a half-finished step survives a
// restart. The table is additive and created on open; an older chats.db simply gains it.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { DatabaseSync } from "node:sqlite"

export type DraftStep = { kind: "human" | "automated"; title?: string; description?: string; prompt?: string; locator?: string }
export type Draft = {
  id: string
  goal: string
  description: string
  steps: DraftStep[]
  /** The model the draft runs on, when the person chose one; empty means the app's usual routing. */
  model?: string
  created_at: number
  updated_at: number
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS way_drafts (
  id          TEXT PRIMARY KEY,
  goal        TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  steps       TEXT NOT NULL DEFAULT '[]',
  model       TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_way_drafts_updated ON way_drafts(updated_at DESC);
`

const file = () => path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "chats.db")

let db: DatabaseSync | null = null
function open(): DatabaseSync {
  if (db) return db
  const p = file()
  fs.mkdirSync(path.dirname(p), { recursive: true })
  db = new DatabaseSync(p)
  db.exec(SCHEMA)
  return db
}

/** A draft's steps as JSON, read back without ever throwing on a row someone hand-edited. */
function stepsOf(raw: unknown): DraftStep[] {
  try {
    const list = JSON.parse(String(raw || "[]"))
    return Array.isArray(list) ? list.filter((s) => s && (s.kind === "human" || s.kind === "automated")) as DraftStep[] : []
  } catch { return [] }
}

const row = (r: any): Draft => ({
  id: String(r.id), goal: String(r.goal || ""), description: String(r.description || ""), steps: stepsOf(r.steps),
  ...(r.model ? { model: String(r.model) } : {}), created_at: Number(r.created_at) || 0, updated_at: Number(r.updated_at) || 0,
})

/** Every draft, newest first: the list the panel shows. */
export function listDrafts(): Draft[] {
  const rows = open().prepare("SELECT * FROM way_drafts ORDER BY updated_at DESC").all() as any[]
  return rows.map(row)
}

export function getDraft(id: string): Draft | null {
  const r = open().prepare("SELECT * FROM way_drafts WHERE id = ?").get(id) as any
  return r ? row(r) : null
}

/** Save a draft: `id` present keeps it and bumps updated_at, absent makes a new one. Returns the saved draft. */
export function saveDraft(d: Omit<Draft, "created_at" | "updated_at">): Draft {
  const now = Date.now()
  const db = open()
  const steps = JSON.stringify(Array.isArray(d.steps) ? d.steps : [])
  const existing = getDraft(d.id)
  if (existing) {
    db.prepare("UPDATE way_drafts SET goal = ?, description = ?, steps = ?, model = ?, updated_at = ? WHERE id = ?")
      .run(d.goal || "", d.description || "", steps, d.model || null, now, d.id)
  } else {
    db.prepare("INSERT INTO way_drafts (id, goal, description, steps, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .run(d.id, d.goal || "", d.description || "", steps, d.model || null, now, now)
  }
  return getDraft(d.id)!
}

/** Forget a draft. Called once the server has the submission, so the person does not keep two copies of one Way. */
export function deleteDraft(id: string): void {
  open().prepare("DELETE FROM way_drafts WHERE id = ?").run(id)
}
