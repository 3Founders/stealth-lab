import { apiGet, type ApiState } from "@/lib/api";

/**
 * Typed reads for the Problems → Goal → Procedure surface. Every shape here mirrors what
 * the backend actually returns today (see backend/db/35_product_model.sql, 83_goals.sql,
 * app/api/problems.py, app/api/procedures.py) — loosely typed with `Record<string, unknown>`
 * as the base, same convention as lib/api.ts's `labelOf`, because these are raw table rows
 * whose exact column set the backend itself says "may vary." Nothing here is fabricated:
 * a field that isn't returned is simply absent, and callers must treat it as such.
 */

export type Row = Record<string, unknown>;

export interface Problem extends Row {
  id: string;
  title: string;
  description?: string | null;
  objective?: string | null;
  constraints?: unknown[];
  status?: string;
  proposer?: string | null;
  metadata?: Row;
}

export interface Benchmark extends Row {
  id: string;
  problem_id: string;
  name: string;
  description?: string | null;
  version?: number;
  evaluation_protocol?: Row;
  environment_specification?: Row;
  success_criteria?: Row;
  comparison_policy?: Row;
  status?: string;
  frozen_at?: string | null;
}

export interface Solution extends Row {
  id: string;
  problem_id: string;
  solution_type: "procedure" | "task_graph" | "task";
  target_id: string;
  target_table: string;
  status?: string;
  proposer?: string | null;
}

export interface Evaluation extends Row {
  id: string;
  problem_id: string;
  benchmark_id: string;
  solution_id: string;
  procedure_id?: string | null;
  run_count?: number;
  aggregate_result?: "pass" | "fail" | "partial" | "inconclusive" | null;
  status?: string;
  created_at?: string;
  completed_at?: string | null;
}

export interface EvidenceSummary {
  total?: number;
  success_count?: number;
  failure_count?: number;
}

export interface ProcedureDetail extends Row {
  id: string;
  procedure_id?: string;
  version?: number;
  name?: string;
  goal?: string | null;
  display_name?: string | null;
  display_description?: string | null;
  applicability_summary?: string | null;
  failure_modes?: unknown[];
  steps?: unknown[];
  preconditions?: unknown[];
  invariants?: unknown[];
  verification_state?: string;
  staleness?: string | null;
  availability?: string;
  approval_status?: string;
  provenance?: string | null;
  domain?: string | null;
  created_by?: string | null;
  claims?: Row[];
  evidence_summary?: EvidenceSummary;
  executor_kinds?: string[];
  t_created?: string;
}

export interface ProcedureVersionRow extends Row {
  id: string;
  version?: number;
  name?: string;
  t_created?: string;
  created_by?: string | null;
}

export interface EvidenceRow extends Row {
  id: string;
  outcome?: string;
  created_at?: string;
  summary?: string;
  description?: string;
}

const j = (v: unknown) => encodeURIComponent(String(v));

export const getProblems = (limit = 100, signal?: AbortSignal) =>
  apiGet<{ problems: Problem[] }>(`/v1/problems?limit=${limit}`, signal);

export const getProblem = (id: string, signal?: AbortSignal) => apiGet<Problem>(`/v1/problems/${j(id)}`, signal);

export const getProblemSolutions = (id: string, signal?: AbortSignal) =>
  apiGet<{ solutions: Solution[] }>(`/v1/problems/${j(id)}/solutions`, signal);

export const getProblemBenchmarks = (id: string, signal?: AbortSignal) =>
  apiGet<{ benchmarks: Benchmark[] }>(`/v1/problems/${j(id)}/benchmarks`, signal);

export const getProblemEvaluations = (id: string, signal?: AbortSignal) =>
  apiGet<{ evaluations: Evaluation[] }>(`/v1/problems/${j(id)}/evaluations`, signal);

export const getProcedure = (id: string, signal?: AbortSignal) => apiGet<ProcedureDetail>(`/v1/procedures/${j(id)}`, signal);

export const getProcedureVersions = (id: string, signal?: AbortSignal) =>
  apiGet<ProcedureVersionRow[]>(`/v1/procedures/${j(id)}/versions`, signal);

export const getProcedureEvidence = (id: string, signal?: AbortSignal) =>
  apiGet<EvidenceRow[]>(`/v1/procedures/${j(id)}/evidence`, signal);

/** True once every ApiState in the list has resolved (not idle/loading). */
export function settled(states: ApiState<unknown>[]): boolean {
  return states.every((s) => s.kind !== "idle" && s.kind !== "loading");
}

/**
 * Contextual rank + verification bucket for a Procedure within one Goal's comparison list.
 * Derived entirely from real fields already on ProcedureDetail (verification_state,
 * evidence_summary) — never a claim of universal ranking, only "best fit we can see for
 * this goal, from what's recorded so far."
 */
export function verificationBucket(p: ProcedureDetail): "Verified" | "Candidate" | "Needs evidence" {
  if (p.verification_state === "verified") return "Verified";
  const total = p.evidence_summary?.total ?? 0;
  if (total === 0) return "Needs evidence";
  return "Candidate";
}

export function rankScore(p: ProcedureDetail): number {
  const ev = p.evidence_summary;
  const success = ev?.success_count ?? 0;
  const failure = ev?.failure_count ?? 0;
  const verified = p.verification_state === "verified" ? 1000 : 0;
  return verified + success * 10 - failure * 4;
}

/** Coarse, honest relative time. Never invents a date: no input → no output. */
export function timeAgo(iso?: string | null): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? "s" : ""} ago`;
  const years = Math.floor(months / 12);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}

/** Flattens a JSONB spec object into short, human-readable "label — value" pairs. Never prints raw JSON. */
export function humanize(obj: Row | undefined | null): [string, string][] {
  if (!obj) return [];
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v == null) continue;
    const label = k.replace(/_/g, " ");
    let val: string;
    if (Array.isArray(v)) val = v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
    else if (typeof v === "object") val = Object.entries(v as Row).map(([kk, vv]) => `${kk}: ${vv}`).join(", ");
    else val = String(v);
    if (val.trim()) out.push([label, val.length > 160 ? val.slice(0, 157) + "…" : val]);
  }
  return out;
}

export interface ProblemStats {
  ways: number;
  verifiedRuns: number;
  lastActivity: string | null;
}

/** One real-data rollup per Problem, built from /solutions and /evaluations. No number here is invented. */
export async function getProblemStats(id: string, signal?: AbortSignal): Promise<ProblemStats | null> {
  const [sol, ev] = await Promise.all([getProblemSolutions(id, signal), getProblemEvaluations(id, signal)]);
  if (sol.kind !== "ok" && ev.kind !== "ok") return null;
  const ways = sol.kind === "ok" ? sol.data.solutions.filter((s) => s.target_table === "procedures").length : 0;
  const evals = ev.kind === "ok" ? ev.data.evaluations : [];
  const verifiedRuns = evals.filter((e) => e.aggregate_result === "pass").reduce((n, e) => n + (e.run_count ?? 0), 0);
  const stamps = evals.map((e) => e.completed_at ?? e.created_at).filter(Boolean) as string[];
  const lastActivity = stamps.length ? stamps.sort().at(-1)! : null;
  return { ways, verifiedRuns, lastActivity };
}
