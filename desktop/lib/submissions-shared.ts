// The submission API's shared values and types, safe for BOTH the browser and the server (2026-11-18).
//
// Why this file exists: the panel runs in the page, so importing lib/submissions-api into it pulled lib/mcp.ts -- which
// reads the sign-in with node:fs/node:os/node:path -- into the browser bundle, and `next build` refused it with
// "Reading from node:fs is not handled by plugins". The panel needs these two helpers and a pile of types; none of them
// touch the filesystem, so they live here and both sides import from here. Nothing in this module may import node:*.

export type StepKind = "human" | "automated"
export type LocatorType = "url" | "file" | "script" | "tool"

export interface StepIn {
  kind: StepKind
  title?: string
  description?: string
  prompt?: string
  locator?: string | { type: LocatorType; value: string }
}
export interface SubmissionIn { goal: string; description?: string; steps: StepIn[] }

export interface Preview { goal: string; mode: string; mode_label: string; message: string; steps: StepIn[] }
/** The three rewrites a Way can be asked for before it is submitted (backend/app/submissions/refine.py). */
export type RefineMode = "improve" | "specialize" | "abstract"
/** A rewrite of the WHOLE draft: the goal, its description and every step. Nothing is stored until it is submitted. */
export interface RefineResult {
  goal: string; description: string; mode: RefineMode; mode_label: string; mode_note: string; steps: StepIn[]; model?: string
}

export interface Step {
  order: number; kind: StepKind; title: string; description: string; prompt: string
  locator: { type: LocatorType; value: string } | null
  status: "reviewing" | "ai_approved" | "approved" | "rejected"
  reason: string; reviewer: string | null; revision: number
  goal_match?: { decision?: string; goal?: string | null } | null
}

/** A Way kell found for a rejected step. Searched when the panel asks, never stored. */
export interface Suggestion { name: string; goal: string; way_id?: string; why?: string; steps?: string[] }

export interface Submission {
  id: string; goal_name: string; description: string; mode: string; mode_label: string
  status: "reviewing" | "needs_changes" | "ai_approved" | "approved" | "withdrawn"
  parent_procedure_id: string | null
  steps: Step[]; created_at?: string; message?: string
}

export interface SubmissionRow {
  id: string; goal_name: string; mode: string; status: Submission["status"]; created_at: string
  steps: number; rejected: number
}

export interface Run {
  id: string; status: "running" | "waiting_human" | "waiting_session" | "done" | "failed" | "cancelled"
  next_step: number; steps?: number; error: string | null; mode?: "session" | "headless"
  waiting_on: { kind: "step" | "signin"; order: number; title: string; description?: string; link?: string } | null
  outputs: { order: number; title: string; actor: "human" | "ai"; output: string }[]
}

/** One of the person's runs, wherever it was started (the website, the desktop, a chat). */
export interface RunRow {
  run_id: string; status: Run["status"]; mode: "session" | "headless"; way_id: string; way_name: string
  input: string; step: number; of: number; step_title: string
  started_at: string | null; updated_at: string | null
}

export const STATUS_LABEL: Record<Submission["status"] | Step["status"], string> = {
  reviewing: "Being reviewed", needs_changes: "Needs changes", ai_approved: "AI approved", approved: "Approved",
  withdrawn: "Withdrawn", rejected: "Rejected",
}

export const runnable = (s: Pick<Submission, "status">) => s.status === "ai_approved" || s.status === "approved"
