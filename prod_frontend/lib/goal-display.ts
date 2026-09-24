import type { Evaluation, EvidenceRow, Goal, GoalRanking, RankedProcedure } from "@/lib/kel-api";

export type GoalResolution = "resolved" | "unresolved";

export function isGoalResolved(goal: Pick<Goal, "resolved_at">): boolean {
  return Boolean(goal.resolved_at);
}

export function goalResolution(goal: Pick<Goal, "resolved_at">): GoalResolution {
  return isGoalResolved(goal) ? "resolved" : "unresolved";
}

export function goalResolutionLabel(goal: Pick<Goal, "resolved_at">): string {
  return goalResolution(goal) === "resolved" ? "Resolved" : "Unresolved";
}

export function splitGoalsByResolution(goals: readonly Goal[]): { unresolved: Goal[]; resolved: Goal[] } {
  const unresolved: Goal[] = [];
  const resolved: Goal[] = [];
  for (const goal of goals) {
    if (isGoalResolved(goal)) resolved.push(goal);
    else unresolved.push(goal);
  }
  return { unresolved, resolved };
}

export function formatRankingState(state: unknown): string | null {
  if (typeof state !== "string" || !state.trim()) return null;
  return state
    .trim()
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function rankingExplanation(ranking: GoalRanking | null | undefined): string | null {
  if (!ranking || typeof ranking.explanation !== "string") return null;
  const explanation = ranking.explanation.trim();
  return explanation || null;
}

function percentage(value: number): string {
  const amount = Math.abs(value) <= 1 ? value * 100 : value;
  return `${Math.round(amount)}%`;
}

export function rankingSignalSummary(ranking: GoalRanking | null | undefined): string[] {
  if (!ranking?.signals) return [];
  return Object.values(ranking.signals)
    .filter((signal) => signal?.available)
    .map((signal) => signal.value === null ? signal.label : `${signal.label}: ${percentage(signal.value)}`);
}

type ProcedureConfidenceFields = Pick<
  RankedProcedure,
  "credible_lower_bound" | "bayesian_lower_bound" | "credible_lower_95" | "posterior_lower_credible_bound" | "posterior_lower_bound" | "wilson_lower_bound"
>;

export function procedurePosteriorLowerBound(procedure: ProcedureConfidenceFields): number | null {
  const values = [
    procedure.credible_lower_bound,
    procedure.bayesian_lower_bound,
    procedure.credible_lower_95,
    procedure.posterior_lower_credible_bound,
    procedure.posterior_lower_bound,
  ];
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return typeof procedure.wilson_lower_bound === "number" && Number.isFinite(procedure.wilson_lower_bound)
    ? procedure.wilson_lower_bound
    : null;
}

export function procedureConfidenceLabel(procedure: ProcedureConfidenceFields): string | null {
  const posterior = [
    procedure.credible_lower_bound,
    procedure.bayesian_lower_bound,
    procedure.credible_lower_95,
    procedure.posterior_lower_credible_bound,
    procedure.posterior_lower_bound,
  ].find((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (posterior !== undefined) return `Posterior lower credible bound ${percentage(posterior)}`;
  if (typeof procedure.wilson_lower_bound === "number" && Number.isFinite(procedure.wilson_lower_bound)) {
    return `Legacy lower bound ${percentage(procedure.wilson_lower_bound)}`;
  }
  return null;
}

export function procedureLane(procedure: Pick<RankedProcedure, "cold_start_lane" | "lane" | "ranking_lane">): string | null {
  return formatRankingState(procedure.cold_start_lane ?? procedure.lane ?? procedure.ranking_lane);
}

export function procedureRankingExplanation(procedure: { explanation?: string | null }): string | null {
  return procedure.explanation?.trim() || null;
}

export function evaluationsForProcedure<T extends Pick<Evaluation, "procedure_id">>(
  evaluations: readonly T[],
  stableProcedureId: string | null | undefined,
): T[] {
  if (!stableProcedureId) return [];
  return evaluations.filter((evaluation) => evaluation.procedure_id === stableProcedureId);
}

export function evidenceOutcome(evidence: Pick<EvidenceRow, "outcome_status">): string {
  return evidence.outcome_status?.trim() || "unknown";
}

export function evidenceOutcomeLabel(evidence: Pick<EvidenceRow, "outcome_status">): string {
  return formatRankingState(evidenceOutcome(evidence)) ?? "Unknown";
}

export function evidenceTimestamp(evidence: Pick<EvidenceRow, "t_created">): string | null {
  return evidence.t_created ?? null;
}

export function evidenceDescription(evidence: Pick<EvidenceRow, "evidence_type" | "content_ref" | "source_id" | "created_by">): string {
  if (evidence.content_ref) return evidence.content_ref;
  if (evidence.source_id) return evidence.source_id;
  if (evidence.created_by) return `Recorded by ${evidence.created_by}`;
  return evidence.evidence_type?.trim() || "Evidence recorded.";
}
