/**
 * Typed client for the plat_v1 backend. Every shape here mirrors a real
 * Pydantic model or SQL row — see app/models/run.py, app/api/run.py, and
 * app/api/proposals.py for the source of truth. If the backend schema
 * changes, this is the one file to update.
 *
 * Same structure as the frontend_v2 client it was copied from, so the two
 * reconcile easily when the backends merge.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8001";

export type GraphNode = {
  id: string;
  table: "knowledge_nodes" | "task_nodes";
  label: string;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
};

export type PlanEdge = {
  type: "REQUIRES" | "PRODUCES" | "DECOMPOSES_TO";
  source_ref: string;
  target_ref: string;
};

export type PlanNode = {
  ref: string;
  name: string;
  description: string;
  kind: "leaf" | "composite";
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  existing_task_id: string | null;
  implementations: { name: string; kind: string; cost_estimate: number }[];
  expansion: { nodes: PlanNode[]; edges: PlanEdge[] } | null;
};

export type Plan = {
  nodes: PlanNode[];
  edges: PlanEdge[];
  external_inputs: string[];
  feasible: boolean;
  reasoning: string;
};

export type TypecheckProblem = {
  rule: string;
  message: string;
  refs: string[];
};

// `proposals.typecheck` is JSONB with a `{}` default, so a row written by
// anything other than the normal insert path can arrive without these keys.
// Optional here rather than required, so the compiler forces the call sites
// to cope instead of throwing on `.map` of undefined.
export type Typecheck = {
  ok?: boolean;
  problems?: TypecheckProblem[];
  messages?: string[];
};

export type Stage = {
  node_ref: string;
  task_name: string;
  implementation_name: string;
  implementation_kind: string;
  outcome: "success" | "failure";
  attempts: number;
  cache_hit: boolean;
  cost: number;
  latency_ms: number;
  error: string | null;
};

export type MatchedRun = {
  route: "match";
  run_id: string;
  status: string;
  matched_task: string;
  match_score: number;
  outputs: Record<string, unknown>;
  error: string | null;
  stages: Stage[];
  total_cost: number;
  total_latency_ms: number;
};

export type ProposedRun = {
  route: "decompose";
  proposal_id: string;
  feasible: boolean;
  reasoning: string;
  match_reason: string;
  candidates: { id: string; name: string; score: number }[];
  typecheck: Typecheck;
  approvable: boolean;
  plan: Plan;
};

export type RunResponse = MatchedRun | ProposedRun;

export type Proposal = {
  id: string;
  request_text: string;
  inputs: Record<string, unknown>;
  plan: Plan;
  typecheck: Typecheck;
  approvable: boolean;
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  run_id: string | null;
};

export type DecisionResponse = {
  id: string;
  decision: "approved" | "rejected";
  run_id?: string;
  status?: string;
  outputs?: Record<string, unknown>;
  error?: string | null;
  stages?: Stage[];
  total_cost?: number;
  total_latency_ms?: number;
};

export type RunListItem = {
  id: string;
  request_text: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  total_cost: number;
  total_latency_ms: number;
  stage_count: number;
};

export type RunDetail = {
  id: string;
  request_text: string;
  status: string;
  plan: Plan;
  outputs: Record<string, unknown>;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  stages: Stage[];
  total_cost: number;
  total_latency_ms: number;
};

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(body || res.statusText, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // 202 for the decompose path and 200 for the match path are both `ok`, so
  // one call covers both. Discriminate on `route`, not on the status code.
  run: (body: { prompt: string; inputs: Record<string, unknown> }) =>
    request<RunResponse>("/v1/run", { method: "POST", body: JSON.stringify(body) }),

  listProposals: (status = "pending") =>
    request<Proposal[]>(`/v1/proposals?status=${status}`),

  getProposal: (id: string) => request<Proposal>(`/v1/proposals/${id}`),

  decide: (id: string, decision: "approve" | "reject", decidedBy: string) =>
    request<DecisionResponse>(`/v1/proposals/${id}`, {
      method: "POST",
      body: JSON.stringify({ decision, decided_by: decidedBy }),
    }),

  listRuns: () => request<RunListItem[]>("/v1/runs"),

  getRun: (id: string) => request<RunDetail>(`/v1/runs/${id}`),
};

export { ApiError };
