/**
 * Goal submission on the hosted server (backend/app/mcp_server/submission_routes.py, docs/goal_submission.md): a person gives a goal and
 * the steps to reach it, each done by a person or automated (a prompt, a link to a website / file / script, a tool). Every step is
 * reviewed by kell's AI reviewer; a rejected step comes back with the reason and is fixed on its own; an approved Way can be run here.
 * Calls carry the website-session token as a Bearer, to the hosted MCP origin only, no cookies.
 */
import { MCP_ORIGIN } from "@/lib/connector";

export type StepKind = "human" | "automated";
export type LocatorType = "url" | "file" | "script" | "tool";
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
export interface Run {
  id: string; status: "running" | "waiting_human" | "done" | "failed"; next_step: number; steps?: number; error: string | null;
  waiting_on: { kind: "step" | "signin"; order: number; title: string; description?: string; link?: string } | null;
  outputs: { order: number; title: string; actor: "human" | "ai"; output: string }[];
}

export const STATUS_LABEL: Record<Submission["status"] | Step["status"], string> = {
  reviewing: "Being reviewed", needs_changes: "Needs changes", ai_approved: "AI approved", approved: "Approved", withdrawn: "Withdrawn",
  rejected: "Rejected",
};
export const runnable = (s: Pick<Submission, "status">) => s.status === "ai_approved" || s.status === "approved";

export class SubmissionsApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = "SubmissionsApiError"; }
}

export interface SubmissionsDeps { token(): Promise<string | null>; origin?: string; fetchImpl?: typeof fetch }

/** A step as the form holds it, cleaned for the server: empty fields are left out, a locator is sent as typed (the server classifies it). */
export function cleanStep(s: StepIn): StepIn {
  const out: StepIn = { kind: s.kind };
  for (const k of ["title", "description", "prompt"] as const) { const v = (s[k] || "").trim(); if (v) out[k] = v; }
  if (s.kind === "automated") {
    const loc = typeof s.locator === "string" ? s.locator.trim() : s.locator?.value.trim() ? s.locator : undefined;
    if (loc) out.locator = typeof loc === "string" ? loc : { type: loc.type, value: loc.value.trim() };
  } else {
    delete out.prompt;
  }
  return out;
}

export function submissionsApi(deps: SubmissionsDeps) {
  const base = (deps.origin ?? MCP_ORIGIN).replace(/\/+$/, "");
  const doFetch = deps.fetchImpl ?? fetch;
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await deps.token();
    if (!token) throw new SubmissionsApiError("Sign in to submit a Way.", 401);
    let res: Response;
    try {
      res = await doFetch(`${base}/v1/goals${path}`, {
        method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
        headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch { throw new SubmissionsApiError("Couldn't reach the keळ server."); }
    let json: unknown = null;
    try { json = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (json as { error?: unknown } | null)?.error;
      throw new SubmissionsApiError(typeof msg === "string" && msg ? msg : `The server answered ${res.status}.`, res.status);
    }
    return json as T;
  }
  const body = (s: SubmissionIn) => ({ goal: s.goal.trim(), description: (s.description || "").trim(), steps: s.steps.map(cleanStep) });
  return {
    preview: (s: SubmissionIn) => call<Preview>("POST", "/submissions/preview", body(s)),
    submit: (s: SubmissionIn) => call<Submission>("POST", "/submissions", body(s)),
    list: () => call<{ submissions: SubmissionRow[] }>("GET", "/submissions").then((r) => r.submissions),
    get: (id: string) => call<Submission>("GET", `/submissions/${encodeURIComponent(id)}`),
    fix: (id: string, order: number, step: StepIn) => call<Submission>("POST", `/submissions/${encodeURIComponent(id)}/steps/${order}`, cleanStep(step)),
    run: (id: string, input: string) => call<Run>("POST", `/submissions/${encodeURIComponent(id)}/runs`, { input }),
    getRun: (runId: string) => call<Run>("GET", `/runs/${encodeURIComponent(runId)}`),
    done: (runId: string, note: string) => call<Run>("POST", `/runs/${encodeURIComponent(runId)}/done`, { note }),
  };
}
export type SubmissionsApi = ReturnType<typeof submissionsApi>;
