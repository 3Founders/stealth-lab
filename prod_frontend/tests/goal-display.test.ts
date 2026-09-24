import { describe, expect, it } from "vitest";
import {
  evaluationsForProcedure,
  formatRankingState,
  goalResolutionLabel,
  procedureConfidenceLabel,
  procedureLane,
  rankingExplanation,
  splitGoalsByResolution,
} from "@/lib/goal-display";
import { rankedProcedureRowId } from "@/lib/kel-api";
import type { Goal, RankedProcedure } from "@/lib/kel-api";

const ranking = {
  score: 0.73,
  state: "warm_start",
  signals: { evidence: { label: "Evidence", value: 0.73, available: true } },
  explanation: "More independent evidence supports this way.",
};

describe("goal display helpers", () => {
  it("splits goals without reordering either backend-ranked group", () => {
    const goals = [
      { id: "u1", canonical_name: "Unresolved one", resolved_at: null },
      { id: "r1", canonical_name: "Resolved one", resolved_at: "2026-09-24T00:00:00Z" },
      { id: "u2", canonical_name: "Unresolved two" },
      { id: "r2", canonical_name: "Resolved two", resolved_at: "2026-09-23T00:00:00Z" },
    ] as Goal[];
    const split = splitGoalsByResolution(goals);
    expect(split.unresolved.map((goal) => goal.id)).toEqual(["u1", "u2"]);
    expect(split.resolved.map((goal) => goal.id)).toEqual(["r1", "r2"]);
    expect(goalResolutionLabel(goals[1])).toBe("Resolved");
    expect(goalResolutionLabel(goals[0])).toBe("Unresolved");
  });

  it("uses the backend explanation and formats ranking state for display", () => {
    expect(rankingExplanation(ranking)).toBe("More independent evidence supports this way.");
    expect(formatRankingState(ranking.state)).toBe("Warm Start");
  });

  it("prefers a posterior lower credible bound and keeps legacy Wilson compatibility", () => {
    const posterior = {
      credible_lower_bound: 0.61,
      bayesian_lower_bound: 0.61,
      wilson_lower_bound: 0.42,
    } as Pick<RankedProcedure, "credible_lower_bound" | "bayesian_lower_bound" | "wilson_lower_bound">;
    const legacy = { wilson_lower_bound: 0.42 } as Pick<RankedProcedure, "wilson_lower_bound">;
    expect(procedureConfidenceLabel(posterior)).toBe("Posterior lower credible bound 61%");
    expect(procedureConfidenceLabel(legacy)).toBe("Legacy lower bound 42%");
    expect(procedureLane({ cold_start_lane: "cold_start" } as Pick<RankedProcedure, "cold_start_lane">)).toBe("Cold Start");
    expect(procedureLane({ lane: "promising_needs_evidence" } as Pick<RankedProcedure, "lane">)).toBe("Promising Needs Evidence");
  });

  it("resolves a stable solution target to its ranked procedure row", () => {
    const ranked = [
      { procedure_id: "stable-a", procedure_row_id: "row-a" },
      { procedure_id: "stable-b", procedure_row_id: "row-b" },
    ] as Pick<RankedProcedure, "procedure_id" | "procedure_row_id">[];
    expect(rankedProcedureRowId(ranked, "stable-b")).toBe("row-b");
    expect(rankedProcedureRowId(ranked, "missing")).toBeNull();
  });

  it("filters evaluations by stable procedure identity, not the route row identity", () => {
    const evaluations = [
      { procedure_id: "stable-a" },
      { procedure_id: "row-a" },
      { procedure_id: "stable-a" },
    ];
    expect(evaluationsForProcedure(evaluations, "stable-a")).toHaveLength(2);
    expect(evaluationsForProcedure(evaluations, "row-a")).toHaveLength(1);
    expect(evaluationsForProcedure(evaluations, undefined)).toEqual([]);
  });
});
