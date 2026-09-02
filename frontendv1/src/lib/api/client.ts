import type {
  ClaimDetail,
  ClaimDependent,
  Evidence,
  Implementation,
  ImplementationStatus,
  MeResponse,
  ProcedureDetail,
  ProjectKnowledgeResponse,
  RepositoryKnowledgeResponse,
  SearchResponse,
  SolutionDetail,
  SolutionSearchResponse,
  SubgraphResponse,
  TaskDetail,
  RecommendResponse,
  DecomposeResponse,
} from "./types";

import { authHeaders } from "@/lib/auth";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export { API_URL };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// Small in-memory cache for read-only GETs (stable objects only, short TTL).
const CACHE_TTL_MS = 30_000;
const cache = new Map<string, { at: number; data: unknown }>();

async function request<T>(
  path: string,
  init?: RequestInit & { cacheable?: boolean }
): Promise<T> {
  const url = `${API_URL}${path}`;
  if (init?.cacheable) {
    const hit = cache.get(url);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.data as T;
  }
  const res = await fetch(url, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...authHeaders(),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detail);
  }
  const data = (await res.json()) as T;
  if (init?.cacheable) cache.set(url, { at: Date.now(), data });
  return data;
}

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

// ---------- Search ----------

export function searchSolutions(q: string, limit = 20) {
  return request<SolutionSearchResponse>(
    `/v1/solutions/search${qs({ q, limit })}`
  );
}

export function searchAll(
  q: string,
  opts: { object_types?: string; repository_id?: string; project_id?: string; limit?: number } = {}
) {
  return request<SearchResponse>(`/v1/search${qs({ q, ...opts })}`);
}

export function searchProcedures(q: string, limit = 20) {
  return request<SearchResponse>(
    `/v1/procedures/search${qs({ q, limit })}`
  );
}

export function searchTasks(q: string, limit = 20) {
  return request<SearchResponse>(`/v1/tasks/search${qs({ q, limit })}`);
}

// ---------- Solutions ----------

export function getSolution(id: string, implementation_id?: string) {
  return request<SolutionDetail>(
    `/v1/solutions/${id}${qs({ implementation_id })}`
  );
}

export function getSolutionImplementations(id: string) {
  return request<Record<string, unknown>[]>(
    `/v1/solutions/${id}/implementations`
  );
}

// ---------- Procedures ----------

export function getProcedure(id: string) {
  return request<ProcedureDetail>(`/v1/procedures/${id}`, { cacheable: true });
}

export function getProcedureEvidence(id: string) {
  return request<Record<string, unknown>[]>(`/v1/procedures/${id}/evidence`);
}

export function getProcedureClaims(id: string) {
  return request<Record<string, unknown>[]>(`/v1/procedures/${id}/claims`);
}

export function getProcedureGraph(id: string, depth = 2) {
  return request<Record<string, unknown>>(
    `/v1/procedures/${id}/graph${qs({ depth })}`
  );
}

// ---------- Tasks ----------

export function getTask(id: string) {
  return request<TaskDetail>(`/v1/tasks/${id}`, { cacheable: true });
}

export function getTaskImplementations(id: string, status: ImplementationStatus | "all" = "active") {
  return request<Implementation[]>(
    `/v1/tasks/${id}/implementations${qs({ status })}`
  );
}

// ---------- Claims ----------

export function getClaim(id: string) {
  return request<ClaimDetail>(`/v1/claims/${id}`);
}

export function getClaimEvidence(id: string) {
  return request<Evidence[]>(`/v1/claims/${id}/evidence`);
}

export function getClaimDependents(id: string) {
  return request<ClaimDependent[]>(`/v1/claims/${id}/dependents`);
}

// ---------- Implementations ----------

export function getImplementation(id: string) {
  return request<Implementation>(`/v1/implementations/${id}`, { cacheable: true });
}

export function getImplementationEvidence(id: string) {
  return request<Record<string, unknown>[]>(
    `/v1/implementations/${id}/evidence`
  );
}

export function getImplementationCapability(id: string) {
  return request<Record<string, unknown>>(
    `/v1/implementations/${id}/capability`
  );
}

// ---------- Repositories / Projects ----------

export function getRepository(id: string, depth = 2) {
  return request<RepositoryKnowledgeResponse>(
    `/v1/repositories/${id}${qs({ depth })}`
  );
}

export function getProject(id: string, depth = 2) {
  return request<ProjectKnowledgeResponse>(
    `/v1/projects/${id}${qs({ depth })}`
  );
}

// ---------- Me ----------

export function getMe() {
  return request<MeResponse>(`/v1/me`);
}

// ---------- Graph ----------

export function getSubgraph(nodeId: string, depth = 2) {
  return request<SubgraphResponse>(`/v1/graph/${nodeId}${qs({ depth })}`);
}

// ---------- Product model: Problem / Benchmark / Solution / Evaluation ----------

export interface Problem {
  id: string;
  title: string;
  description: string | null;
  objective: string | null;
  constraints: unknown[];
  status: string;
  proposer: string | null;
  provenance: string | null;
  metadata: Record<string, unknown>;
  visibility: string;
  created_at: string;
  updated_at: string;
}

export interface LeaderboardEntry {
  solution_id: string;
  solution_type: string;
  target_id: string;
  run_count: number;
  successes: number;
  verified_successes: number;
  success_rate: number | null;
  verified_success_rate: number | null;
  verified_success_wilson_lower: number;
  verified_success_wilson_upper: number;
  p50_latency_s: number | null;
  p95_latency_s: number | null;
  cost: number | null;
  first_pass_success_rate: number | null;
  evaluations: number;
  incomparable_evaluations: number;
  state: string; // BEST_VERIFIED | PROMISING | INSUFFICIENT_EVIDENCE (backend bands)
}

export interface Leaderboard {
  problem_id: string;
  benchmark_id: string | null;
  leaderboard: LeaderboardEntry[];
  current_best: string[];
  current_best_is_tie: boolean;
  conditional_leaders: Record<string, string | null>;
}

export interface Evaluation {
  id: string;
  problem_id: string;
  benchmark_id: string;
  solution_id: string;
  procedure_id: string | null;
  procedure_version: number | null;
  implementation_id: string | null;
  implementation_version: number | null;
  environment: Record<string, unknown>;
  methodology: Record<string, unknown>;
  status: string;
  run_count?: number | null;
  metrics?: Record<string, unknown> | null;
  verification_summary?: Record<string, unknown> | null;
  provenance: string | null;
}

export interface BestWay {
  goal: string;
  matched_problem: Problem | null;
  benchmark_id?: string | null;
  result: string; // "verified" | "no verified solution yet" | "no matching problem"
  current_best: string[];
  current_best_is_tie?: boolean;
  leaderboard?: LeaderboardEntry[];
  conditional_leaders?: Record<string, string | null>;
  other_matched_problems?: string[];
}

export async function listProblems(limit = 20): Promise<Problem[]> {
  const r = await request<{ problems: Problem[] }>(`/v1/problems?limit=${limit}`, {
    cacheable: true,
  });
  return r.problems;
}

export async function findProblems(q: string, limit = 5): Promise<Problem[]> {
  const r = await request<{ problems: Problem[] }>(
    `/v1/problems/find?q=${encodeURIComponent(q)}&limit=${limit}`
  );
  return r.problems;
}

export async function getProblem(problemId: string): Promise<Problem | null> {
  return request<Problem | null>(`/v1/problems/${encodeURIComponent(problemId)}`, {
    cacheable: true,
  });
}

export async function getProblemSolutions(
  problemId: string
): Promise<Record<string, unknown>[]> {
  const r = await request<{ solutions: Record<string, unknown>[] }>(
    `/v1/problems/${encodeURIComponent(problemId)}/solutions`
  );
  return r.solutions;
}

export async function getProblemBenchmarks(
  problemId: string
): Promise<Record<string, unknown>[]> {
  const r = await request<{ benchmarks: Record<string, unknown>[] }>(
    `/v1/problems/${encodeURIComponent(problemId)}/benchmarks`
  );
  return r.benchmarks;
}

export async function getLeaderboard(
  problemId: string,
  benchmarkId?: string
): Promise<Leaderboard> {
  const suffix = benchmarkId ? `?benchmark_id=${encodeURIComponent(benchmarkId)}` : "";
  return request<Leaderboard>(
    `/v1/problems/${encodeURIComponent(problemId)}/leaderboard${suffix}`,
    { cacheable: true }
  );
}

export async function getProblemEvaluations(
  problemId: string
): Promise<Evaluation[]> {
  const r = await request<{ evaluations: Evaluation[] }>(
    `/v1/problems/${encodeURIComponent(problemId)}/evaluations`
  );
  return r.evaluations;
}

export async function getEvaluation(evaluationId: string): Promise<Evaluation | null> {
  return request<Evaluation | null>(
    `/v1/evaluations/${encodeURIComponent(evaluationId)}`,
    { cacheable: true }
  );
}

export async function getBestWayProductModel(goal: string): Promise<BestWay> {
  return request<BestWay>(`/v1/best-way?goal=${encodeURIComponent(goal)}`);
}

// ---------- Recommend (ranked best way) ----------

export function findBestWay(goal: string, signal?: AbortSignal) {
  return request<RecommendResponse>(`/v1/search/recommend`, {
    method: "POST",
    body: JSON.stringify({ goal }),
    signal,
  });
}

// ---------- Contribute (decompose → quarantined proposal) ----------

export function submitDecomposition(problem: string) {
  return request<DecomposeResponse>(`/v1/decompose`, {
    method: "POST",
    body: JSON.stringify({ problem }),
  });
}


