// Goal submission, from the desktop (the same server routes the website uses: backend/app/mcp_server/submission_routes.py,
// docs/goal_submission.md). A person gives a goal and the steps to reach it, each done by a person or automated (a prompt,
// a link to a website / file / script, a tool). Every step is reviewed by kell's AI reviewer; a rejected step comes back with
// its reason and is fixed on its own; an approved Way can be run from here.
//
// This is the website's lib/submissions-api.ts, unchanged in shape: the answers are the same objects, so a submission
// written here is the submission the rooms show. Only the transport differs: there it carries the website session, here
// it goes through lib/mcp.ts's callWebRoute, which carries the same Rooms session (the coding sign-in is an MCP-app
// token and the website API refuses it on purpose).
import { callWebRoute } from "./mcp"
import type { RefineMode, RefineResult } from "./submissions-shared"

export type StepKind = "human" | "automated"
export type LocatorType = "url" | "file" | "script" | "tool"
export interface StepIn { kind: StepKind; title?: string; description?: string; prompt?: string; locator?: string | { type: LocatorType; value: string } }
export interface SubmissionIn { goal: string; description?: string; steps: StepIn[] }

export interface Preview { goal: string; mode: string; mode_label: string; message: string; steps: StepIn[] }
export interface Step {
  order: number; kind: StepKind; title: string; description: string; prompt: string;
  locator: { type: LocatorType; value: string } | null; status: "reviewing" | "ai_approved" | "approved" | "rejected";
  reason: string; reviewer: string | null; revision: number;
  goal_match?: { decision?: string; goal?: string | null } | null;
}
export interface Submission {
  id: string; goal_name: string; description: string; mode: string; mode_label: string;
  status: "reviewing" | "needs_changes" | "ai_approved" | "approved" | "withdrawn"; parent_procedure_id: string | null;
  steps: Step[]; created_at?: string; message?: string;
}
export interface SubmissionRow { id: string; goal_name: string; mode: string; status: Submission["status"]; created_at: string; steps: number; rejected: number }
/** A Way kell found for a rejected step. Searched when the panel asks, never stored (review.suggest_for_submission). */
export interface Suggestion { name: string; goal: string; way_id?: string; why?: string; steps?: string[] }
export interface Run {
  id: string; status: "running" | "waiting_human" | "waiting_session" | "done" | "failed" | "cancelled"; next_step: number;
  steps?: number; error: string | null; mode?: "session" | "headless";
  waiting_on: { kind: "step" | "signin"; order: number; title: string; description?: string; link?: string } | null;
  outputs: { order: number; title: string; actor: "human" | "ai"; output: string }[];
}
/** One of the person's runs, wherever it was started (the website, the desktop, a chat). */
export interface RunRow {
  run_id: string; status: Run["status"]; mode: "session" | "headless"; way_id: string; way_name: string; input: string;
  step: number; of: number; step_title: string; started_at: string | null; updated_at: string | null;
}

export const STATUS_LABEL: Record<Submission["status"] | Step["status"], string> = {
  reviewing: "Being reviewed", needs_changes: "Needs changes", ai_approved: "AI approved", approved: "Approved", withdrawn: "Withdrawn",
  rejected: "Rejected",
}
export const runnable = (s: Pick<Submission, "status">) => s.status === "ai_approved" || s.status === "approved"

export class SubmissionsApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "SubmissionsApiError" }
}

/** A step as the form holds it, cleaned for the server: empty fields are left out, a locator is sent as typed (the server classifies it). */
export function cleanStep(s: StepIn): StepIn {
  const out: StepIn = { kind: s.kind }
  for (const k of ["title", "description", "prompt"] as const) { const v = (s[k] || "").trim(); if (v) out[k] = v }
  if (s.kind === "automated") {
    const loc = typeof s.locator === "string" ? s.locator.trim() : s.locator?.value.trim() ? s.locator : undefined
    if (loc) out.locator = typeof loc === "string" ? loc : { type: loc.type, value: loc.value.trim() }
  } else {
    delete out.prompt
  }
  return out
}

const body = (s: SubmissionIn) => ({ goal: s.goal.trim(), description: (s.description || "").trim(), steps: s.steps.map(cleanStep) })

/** The same calls the website makes, through this app's own sign-in. Paths are rooted at /v1/goals, as there. */
export const submissionsApi = {
  preview: (s: SubmissionIn) => callWebRoute("/v1/goals/submissions/preview", body(s), 30_000) as Promise<Preview>,
  // Improve / specialize / abstract: one model call that stores nothing; the person submits the result themselves.
  refine: (s: SubmissionIn, mode: RefineMode, instruction?: string) =>
    callWebRoute("/v1/goals/submissions/refine", { ...body(s), mode, instruction: (instruction || "").trim() }, 120_000) as Promise<RefineResult>,
  submit: (s: SubmissionIn) => callWebRoute("/v1/goals/submissions", body(s), 120_000) as Promise<Submission>,
  list: () => callWebRoute("/v1/goals/submissions", undefined, 20_000, "GET").then((r: any) => (r?.submissions || []) as SubmissionRow[]),
  get: (id: string) => callWebRoute(`/v1/goals/submissions/${encodeURIComponent(id)}`, undefined, 20_000, "GET") as Promise<Submission>,
  fix: (id: string, order: number, step: StepIn) => callWebRoute(`/v1/goals/submissions/${encodeURIComponent(id)}/steps/${order}`, cleanStep(step), 120_000) as Promise<Submission>,
  removeStep: (id: string, order: number) => callWebRoute(`/v1/goals/submissions/${encodeURIComponent(id)}/steps/${order}`, undefined, 120_000, "DELETE") as Promise<Submission>,
  // Searched when asked, never stored: a remembered suggestion goes stale when a Way is renamed or deleted.
  suggestions: (id: string) =>
    callWebRoute(`/v1/goals/submissions/${encodeURIComponent(id)}/suggestions`, undefined, 60_000, "GET")
      .then((r: any) => (r?.suggestions || {}) as Record<string, Suggestion[]>),
  run: (id: string, input: string) => callWebRoute(`/v1/goals/submissions/${encodeURIComponent(id)}/runs`, { input }, 60_000) as Promise<Run>,
  getRun: (runId: string) => callWebRoute(`/v1/goals/runs/${encodeURIComponent(runId)}`, undefined, 20_000, "GET") as Promise<Run>,
  done: (runId: string, note: string) => callWebRoute(`/v1/goals/runs/${encodeURIComponent(runId)}/done`, { note }, 30_000) as Promise<Run>,
  runs: () => callWebRoute("/v1/goals/runs", undefined, 20_000, "GET").then((r: any) => (r?.runs || []) as RunRow[]),
  cancel: (runId: string) => callWebRoute(`/v1/goals/runs/${encodeURIComponent(runId)}/cancel`, undefined, 30_000) as Promise<Run>,
}
export type SubmissionsApi = typeof submissionsApi
