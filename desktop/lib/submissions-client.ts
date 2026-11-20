// The submission API as the PAGE sees it (2026-11-18).
//
// Same methods and types as lib/submissions-api, but every call is a POST to app/api/submissions, which does the real
// work on the server. Why: the real API goes through lib/mcp.ts, and that reads the sign-in from disk -- importing it
// into a client component makes `next build` fail with "Reading from node:fs is not handled by plugins". This file is
// the seam between the panel (browser) and the API (server), and it is the only thing the panel imports.
import type { Run, RunRow, StepIn, Submission, SubmissionIn, SubmissionRow, SubmissionsApi, Suggestion } from "./submissions-api"
import type { RefineMode } from "./submissions-shared"

// Types only from the server module: `import type` is erased at build time, so nothing from it reaches the bundle.
export type { Run, RunRow, StepIn, Submission, SubmissionIn, SubmissionRow, SubmissionsApi, Suggestion }

async function call<T>(action: string, extra: Record<string, unknown> = {}): Promise<T> {
  const r = await fetch("/api/submissions", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, ...extra }),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(String(j?.error || `the server answered ${r.status}`))
  return j.result as T
}

export const submissionsApi: SubmissionsApi = {
  preview: (s: SubmissionIn) => call("preview", { payload: s }),
  refine: (s: SubmissionIn, mode: RefineMode, instruction?: string) =>
    call("refine", { payload: s, mode, instruction: instruction || "" }),
  submit: (s: SubmissionIn) => call("submit", { payload: s }),
  list: () => call("list"),
  get: (id: string) => call("get", { id }),
  fix: (id: string, order: number, step: StepIn) => call("fix", { id, order, step }),
  removeStep: (id: string, order: number) => call("removeStep", { id, order }),
  suggestions: (id: string) => call("suggestions", { id }),
  run: (id: string, input: string) => call("run", { id, input }),
  getRun: (runId: string) => call("getRun", { runId }),
  done: (runId: string, note: string) => call("done", { runId, note }),
  runs: () => call("runs"),
  cancel: (runId: string) => call("cancel", { runId }),
}
