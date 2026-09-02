/**
 * WebMCP tool handlers. Each handler reuses the exact same typed API client as
 * the human UI (one backend, one contract) and returns concise, structured,
 * agent-optimized output with a `url` for human handoff. No fabricated fields.
 */
import * as api from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type {
  Capability,
  ProcedureDetail,
  SolutionSearchHit,
  TaskDetail,
} from "@/lib/api/types";
import {
  sanitizeFocus,
  sanitizeId,
  sanitizeQuery,
  sanitizeScope,
} from "./validate";

export type ToolName =
  | "search_stealth"
  | "find_best_way"
  | "find_problem"
  | "inspect_problem"
  | "list_problem_solutions"
  | "compare_solutions"
  | "inspect_procedure"
  | "inspect_task"
  | "inspect_solution"
  | "inspect_evidence"
  | "inspect_evaluation"
  | "inspect_repository_knowledge"
  | "compare_implementations";

type Args = Record<string, unknown>;

function pct(p: number | null | undefined): number | null {
  return p === null || p === undefined ? null : Math.round(p * 1000) / 10;
}

function url(path: string): string {
  if (typeof window === "undefined") return path;
  return new URL(path, window.location.origin).toString();
}

function capabilitySummary(cap: Capability | null | undefined) {
  if (!cap) return null;
  return {
    verified_success: pct(cap.p_estimate),
    runs: cap.evidence_count,
    independent_groups: cap.independent_groups,
    band: cap.band ?? undefined,
  };
}

function provenanceSummary(p: Record<string, unknown> | null | undefined) {
  if (!p || Object.keys(p).length === 0) return null;
  const keys = ["source", "author", "repository", "license", "version", "url"];
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    if (p[k] !== null && p[k] !== undefined) out[k] = p[k];
  }
  return Object.keys(out).length ? out : p;
}

// ---------- search_stealth ----------

function searchHit(hit: SolutionSearchHit) {
  const isProcedure = hit.type === "procedure";
  return {
    type: hit.type,
    id: hit.id,
    title: hit.title,
    goal: hit.goal ?? undefined,
    verified: isProcedure ? hit.verification === "verified" : null,
    verification: capabilitySummary(hit.capability),
    url: url(isProcedure ? `/solutions/${hit.id}` : `/tasks/${hit.id}`),
  };
}

async function searchStealth(args: Args) {
  const query = sanitizeQuery(args.query, "query");
  sanitizeScope(args.repository_id);
  sanitizeScope(args.project_id);
  const res = await api.searchSolutions(query, 10);
  return {
    query: res.query,
    results: res.results.map((hit) => searchHit(hit)),
    note: res.note || undefined,
  };
}
// ---------- find_best_way ----------

async function findBestWay(args: Args) {
  const goal = sanitizeQuery(args.goal, "goal");
  sanitizeScope(args.repository_id);
  // Primary: the backend's own find_best_way (product model). It returns the
  // matched problem, current best verified solution(s), and their URLs —
  // agents never rank or scrape.
  try {
    const bw = await api.getBestWayProductModel(goal);
    const board = bw.leaderboard ?? [];
    const bestEntries = board.filter((e) => bw.current_best.includes(e.solution_id));
    return {
      goal,
      result: bw.result,
      problem: bw.matched_problem
        ? {
            id: bw.matched_problem.id,
            title: bw.matched_problem.title,
            status: bw.matched_problem.status,
            url: url(`/problems/${bw.matched_problem.id}`),
          }
        : null,
      current_best: bestEntries.map((e) => ({
        solution_id: e.solution_id,
        solution_type: e.solution_type,
        target_id: e.target_id,
        verified_success: e.verified_success_rate,
        runs: e.run_count,
        state: e.state,
        url:
          e.solution_type === "procedure"
            ? url(`/solutions/${e.target_id}`)
            : bw.matched_problem
              ? url(`/problems/${bw.matched_problem.id}`)
              : undefined,
      })),
      no_verified_solution: bw.current_best.length === 0,
      conditional_leaders: bw.conditional_leaders ?? undefined,
      url: bw.matched_problem
        ? url(`/problems/${bw.matched_problem.id}`)
        : undefined,
    };
  } catch {
    // Product model unavailable — fall back to the recommend endpoint.
  }
  const rec = await fetch(`${api.API_URL}/v1/search/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal }),
  });
  if (rec.ok) {
    const data = (await rec.json()) as {
      goal: string;
      recommendation: Record<string, unknown> | null;
      alternatives: Record<string, unknown>[];
      confidence: string;
      reason: string;
    };
    if (data.recommendation) {
      const recId = (data.recommendation.id ?? data.recommendation.procedure_id) as
        | string
        | undefined;
      return {
        goal: data.goal,
        recommended: data.recommendation,
        why: data.reason,
        confidence: data.confidence,
        alternatives: data.alternatives,
        url: recId ? url(`/solutions/${recId}`) : undefined,
      };
    }
    // Honest empty state from the backend — fall back to search hits.
    const search = await api.searchSolutions(goal, 5);
    return {
      goal: data.goal,
      recommended: null,
      why: data.reason,
      close_matches: search.results.map(searchHit),
    };
  }
  if ([402, 404, 429, 501].includes(rec.status)) {
    // Recommend endpoint unavailable/governed — use solution search ranking.
    const search = await api.searchSolutions(goal, 5);
    const top = search.results[0];
    return {
      goal,
      recommended: top ? searchHit(top) : null,
      why: top
        ? "Top-ranked result from solution search."
        : search.reason ?? "No matching solution found.",
      close_matches: search.results.slice(1, 5).map(searchHit),
    };
  }
  throw new ApiError(rec.status, "recommendation unavailable");
}

// ---------- inspect_procedure / inspect_task ----------

function procedureSummary(p: ProcedureDetail) {
  return {
    type: "procedure" as const,
    id: p.id,
    title: p.name,
    goal: p.goal,
    version: p.version,
    verified: p.verification_state === "verified",
    verification_state: p.verification_state,
    steps: p.steps.map((s) =>
      String(s.name ?? s.title ?? s.description ?? "step")
    ),
    claims: p.claims.map((c) => String(c.name ?? "")),
    verification_summary: p.evidence_summary
      ? {
          runs: p.evidence_summary.evidence_count ?? p.evidence_summary.total ?? null,
          verified_success: pct(p.evidence_summary.p_estimate),
          successes: p.evidence_summary.success_count ?? null,
        }
      : null,
    provenance: provenanceSummary(p.provenance),
    url: url(`/procedures/${p.id}`),
  };
}

function taskSummary(t: TaskDetail) {
  return {
    type: "task" as const,
    id: t.id,
    title: t.name,
    description: t.description,
    inputs: t.io_schema ?? undefined,
    success_criteria: t.success_criteria ?? undefined,
    latency_estimate_ms: t.latency_estimate_ms,
    capability_statistics: t.capability_statistics.map((c) => ({
      procedure_id: c.procedure_id,
      verified_success: pct(c.p_estimate),
      runs: c.evidence_count,
    })),
    known_failure_modes: t.known_failure_modes,
    dependent_procedures: t.dependent_procedures.map((p) => ({
      id: p.id,
      title: p.name,
      url: url(`/procedures/${p.id}`),
    })),
    url: url(`/tasks/${t.id}`),
  };
}

async function inspectProcedure(args: Args) {
  const id = sanitizeId(args.procedure_id, "procedure_id");
  return procedureSummary(await api.getProcedure(id));
}

async function inspectTask(args: Args) {
  const id = sanitizeId(args.task_id, "task_id");
  return taskSummary(await api.getTask(id));
}

// ---------- inspect_solution / inspect_evidence ----------

async function inspectSolution(args: Args) {
  const id = sanitizeId(args.solution_id, "solution_id");
  const s = await api.getSolution(id);
  return {
    type: "solution" as const,
    id: s.procedure_row_id,
    title: s.name,
    goal: s.goal,
    version: s.version,
    verification_state: s.verification_state,
    verification: capabilitySummary(s.capability),
    implementations: Object.values(s.implementations).map((i) => ({
      kind: i.kind,
      supported: i.supported,
      strategy: i.strategy ?? undefined,
    })),
    claims: s.claims.map((c) => String(c.name ?? "")),
    provenance: provenanceSummary(s.provenance),
    url: url(`/solutions/${s.procedure_row_id}`),
  };
}

async function inspectEvidence(args: Args) {
  const objectType = args.object_type;
  if (
    objectType !== "procedure" &&
    objectType !== "task" &&
    objectType !== "implementation"
  ) {
    throw new Error("object_type must be procedure | task | implementation");
  }
  const rawId = typeof args.object_id === "string" ? args.object_id.trim() : "";
  if (!rawId) throw new Error("object_id is required");

  if (objectType === "procedure") {
    const id = sanitizeId(rawId, "object_id");
    const [p, evidence] = await Promise.all([
      api.getProcedure(id),
      api.getProcedureEvidence(id).catch(() => []),
    ]);
    return {
      object_type: objectType,
      object_id: id,
      title: p.name,
      verification_state: p.verification_state,
      verification_summary: p.evidence_summary
        ? {
            runs: p.evidence_summary.evidence_count ?? null,
            verified_success: pct(p.evidence_summary.p_estimate),
            successes: p.evidence_summary.success_count ?? null,
            failures: p.evidence_summary.failure_count ?? null,
          }
        : null,
      claims: p.claims.map((c) => String(c.name ?? "")),
      evidence: evidence.slice(0, 20),
      url: url(`/procedures/${id}`),
    };
  }
  if (objectType === "task") {
    const id = sanitizeId(rawId, "object_id");
    const t = await api.getTask(id);
    return {
      object_type: objectType,
      object_id: id,
      title: t.name,
      capability_statistics: t.capability_statistics.map((c) => ({
        procedure_id: c.procedure_id,
        verified_success: pct(c.p_estimate),
        runs: c.evidence_count,
      })),
      known_failure_modes: t.known_failure_modes,
      url: url(`/tasks/${id}`),
    };
  }
  const id = sanitizeId(rawId, "object_id");
  const [impl, capability, evidence] = await Promise.all([
    api.getImplementation(id),
    api.getImplementationCapability(id).catch(() => null),
    api.getImplementationEvidence(id).catch(() => []),
  ]);
  return {
    object_type: objectType,
    object_id: id,
    title: impl.name,
    status: impl.status,
    verification_status: impl.verification_status,
    verification_summary: capability
      ? {
          runs:
            (capability as { evidence_count?: number }).evidence_count ?? null,
          verified_success: pct(
            (capability as { p_estimate?: number }).p_estimate ?? null
          ),
        }
      : null,
    evidence: evidence.slice(0, 20),
    url: url(`/implementations/${id}`),
  };
}

// ---------- inspect_repository_knowledge ----------

function filterClaims(
  claims: {
    statement: string | null;
    name: string;
    subject: string | null;
    predicate: string | null;
    object: string | null;
  }[],
  focus: string
) {
  const needle = focus.toLowerCase();
  return claims.filter((c) =>
    [c.statement, c.name, c.subject, c.predicate, c.object]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(needle))
  );
}

async function inspectRepositoryKnowledge(args: Args) {
  const id = sanitizeId(args.repository_id, "repository_id");
  const focus = sanitizeFocus(args.focus);
  const repo = await api.getRepository(id, focus ? 4 : 2);
  const claims = (focus ? filterClaims(repo.claims, focus) : repo.claims).slice(0, 15);
  return {
    repository_id: repo.repository_id,
    focus: focus ?? undefined,
    claims: claims.map((c) => ({
      statement: c.statement ?? c.name,
      id: (c as { id?: string }).id,
      confidence:
        (c as { confidence?: string; truth_state?: string }).confidence ??
        (c as { truth_state?: string }).truth_state ??
        undefined,
    })),
    conflicts: repo.conflicts.map((c) => c.statement ?? c.name),
    relevant_procedures: repo.relevant_procedures.map((p) => ({
      id: p.id,
      title: p.name,
      verification_state: p.verification_state,
      url: url(`/procedures/${p.id}`),
    })),
    summary: repo.confidence_summary,
    url: url(`/repositories/${repo.repository_id}`),
  };
}

// ---------- compare_implementations ----------

async function compareImplementations(args: Args) {
  const taskId = sanitizeId(args.task_id, "task_id");
  const subset = Array.isArray(args.implementation_ids)
    ? (args.implementation_ids as unknown[]).map((x) =>
        sanitizeId(x, "implementation_ids[]")
      )
    : null;
  const impls = await api.getTaskImplementations(taskId, "active");
  const selected = subset ? impls.filter((i) => subset.includes(i.id)) : impls;
  const rows = await Promise.all(
    selected.map(async (impl) => {
      const cap = await api
        .getImplementationCapability(impl.id)
        .catch(() => null);
      return {
        id: impl.id,
        name: impl.name,
        provider: impl.provider,
        kind: impl.kind,
        status: impl.status,
        verification_status: impl.verification_status,
        version: impl.version ?? undefined,
        requirements: impl.requirements ?? undefined,
        license: impl.license ?? undefined,
        verified_success: cap
          ? pct((cap as { p_estimate?: number }).p_estimate ?? null)
          : null,
        runs: cap
          ? ((cap as { evidence_count?: number }).evidence_count ?? null)
          : null,
        url: url(`/implementations/${impl.id}`),
      };
    })
  );
  return { task_id: taskId, implementations: rows, url: url(`/tasks/${taskId}`) };
}

// ---------- find_problem / inspect_problem / list_problem_solutions / ---------- //
// ---------- compare_solutions / inspect_evaluation ---------------------------- //

async function findProblem(args: Args) {
  const query = sanitizeQuery(args.query, "query");
  const res = await api.findProblems(query, 5);
  return {
    query,
    problems: res.map((p) => ({
      id: p.id,
      title: p.title,
      status: p.status,
      objective: p.objective ?? undefined,
      url: url(`/problems/${p.id}`),
    })),
  };
}

async function inspectProblem(args: Args) {
  const id = sanitizeId(args.problem_id, "problem_id");
  const [p, board, sols] = await Promise.all([
    api.getProblem(id),
    api.getLeaderboard(id).catch(() => null),
    api.getProblemSolutions(id).catch(() => [] as { id: string }[]),
  ]);
  if (!p) throw new Error("problem not found");
  const bestIds: string[] =
    board && Array.isArray(board.current_best) ? board.current_best : [];
  const bestEntries = board
    ? board.leaderboard.filter((e) => bestIds.includes(e.solution_id))
    : [];
  return {
    problem: {
      id: p.id,
      title: p.title,
      description: p.description ?? undefined,
      objective: p.objective ?? undefined,
      status: p.status,
    },
    candidate_solutions: sols.length,
    current_best: bestEntries.length
      ? bestEntries.map((e) => ({
          solution_id: e.solution_id,
          solution_type: e.solution_type,
          target_id: e.target_id,
          verified_success: e.verified_success_rate,
          runs: e.run_count,
          state: e.state,
          url: url(
            e.solution_type === "procedure"
              ? `/solutions/${e.target_id}`
              : `/problems/${id}`
          ),
        }))
      : [],
    no_verified_solution: bestEntries.length === 0,
    benchmark_id: board?.benchmark_id ?? undefined,
    url: url(`/problems/${id}`),
  };
}

async function listProblemSolutions(args: Args) {
  const id = sanitizeId(args.problem_id, "problem_id");
  const sols = await api.getProblemSolutions(id);
  return {
    problem_id: id,
    solutions: sols.map((s) => ({
      id: s.id,
      solution_type: s.solution_type,
      target_id: s.target_id,
      status: s.status ?? undefined,
      url:
        s.solution_type === "procedure"
          ? url(`/solutions/${s.target_id}`)
          : url(`/problems/${id}`),
    })),
    url: url(`/problems/${id}`),
  };
}

async function compareSolutions(args: Args) {
  const id = sanitizeId(args.problem_id, "problem_id");
  let benchmarkId: string | undefined;
  if (args.benchmark_id !== undefined && args.benchmark_id !== null) {
    benchmarkId = sanitizeId(args.benchmark_id, "benchmark_id");
  }
  const board = await api.getLeaderboard(id, benchmarkId);
  return {
    problem_id: board.problem_id,
    benchmark_id: board.benchmark_id,
    current_best: board.current_best,
    current_best_is_tie: board.current_best_is_tie,
    leaderboard: board.leaderboard.map((e) => ({
      solution_id: e.solution_id,
      solution_type: e.solution_type,
      target_id: e.target_id,
      state: e.state,
      runs: e.run_count,
      verified_success: e.verified_success_rate,
      first_pass_success: e.first_pass_success_rate ?? undefined,
      p50_latency_s: e.p50_latency_s ?? undefined,
      cost: e.cost ?? undefined,
      incomparable_evaluations: e.incomparable_evaluations,
      url:
        e.solution_type === "procedure"
          ? url(`/solutions/${e.target_id}`)
          : url(`/problems/${board.problem_id}`),
    })),
    url: url(`/problems/${board.problem_id}`),
  };
}

async function inspectEvaluation(args: Args) {
  const id = sanitizeId(args.evaluation_id, "evaluation_id");
  const e = await api.getEvaluation(id);
  if (!e) throw new Error("evaluation not found");
  return {
    evaluation: {
      id: e.id,
      status: e.status,
      problem_id: e.problem_id,
      benchmark_id: e.benchmark_id,
      solution_id: e.solution_id,
      run_count: e.run_count ?? undefined,
      metrics: e.metrics ?? undefined,
      verification_summary: e.verification_summary ?? undefined,
      methodology: e.methodology ?? undefined,
      environment: e.environment ?? undefined,
    },
    url: url(`/evaluations/${e.id}`),
  };
}

export const TOOL_HANDLERS: Record<ToolName, (args: Args) => Promise<unknown>> = {
  search_stealth: searchStealth,
  find_best_way: findBestWay,
  find_problem: findProblem,
  inspect_problem: inspectProblem,
  list_problem_solutions: listProblemSolutions,
  compare_solutions: compareSolutions,
  inspect_procedure: inspectProcedure,
  inspect_task: inspectTask,
  inspect_solution: inspectSolution,
  inspect_evidence: inspectEvidence,
  inspect_evaluation: inspectEvaluation,
  inspect_repository_knowledge: inspectRepositoryKnowledge,
  compare_implementations: compareImplementations,
};

