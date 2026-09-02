"use client";

import Link from "next/link";

import type { Leaderboard, LeaderboardEntry } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";

const STATE_LABEL: Record<string, { label: string; variant: "default" | "secondary" | "outline" }> = {
  BEST_VERIFIED: { label: "Best verified", variant: "default" },
  PROMISING: { label: "Promising", variant: "secondary" },
  INSUFFICIENT_EVIDENCE: { label: "Insufficient evidence", variant: "outline" },
};

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

function num(v: number | null | undefined, suffix = ""): string {
  return v === null || v === undefined ? "—" : `${v}${suffix}`;
}

function BestEntry({ entry }: { entry: LeaderboardEntry }) {
  return (
    <div className="mt-3">
      <p className="text-lg font-medium text-neutral-900">
        {entry.solution_type === "procedure" ? (
          <Link
            href={`/solutions/${entry.target_id}`}
            className="underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
          >
            {entry.target_id}
          </Link>
        ) : (
          entry.target_id
        )}
      </p>
      <p className="mt-1 text-sm text-neutral-600">
        {pct(entry.verified_success_rate)} verified success · n = {entry.run_count} runs
        {entry.first_pass_success_rate !== null &&
          ` · ${pct(entry.first_pass_success_rate)} first-pass`}
        {entry.cost !== null && ` · ~$${entry.cost}`}
        {entry.p50_latency_s !== null && ` · ${entry.p50_latency_s}s p50`}
      </p>
    </div>
  );
}
/** Backend-ranked leaderboard. No frontend re-ranking, ever. */
export function LeaderboardTable({ board }: { board: Leaderboard }) {
  const entries = board.leaderboard;
  const currentBest = board.current_best ?? [];
  const bestEntries = entries.filter((e) => currentBest.includes(e.solution_id));

  return (
    <div>
      {bestEntries.length > 0 && (
        <section className="mb-8 rounded-lg border border-neutral-200 bg-white p-6">
          <p className="text-xs uppercase tracking-widest text-neutral-400">
            {board.current_best_is_tie
              ? "Current best verified (tie)"
              : "Current best verified"}
          </p>
          {bestEntries.map((e) => (
            <BestEntry key={e.solution_id} entry={e} />
          ))}
          {bestEntries[0].solution_type === "procedure" && (
            <div className="mt-4 flex gap-3">
              <Link
                href={`/solutions/${bestEntries[0].target_id}`}
                className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-neutral-50 hover:bg-neutral-800"
              >
                Inspect solution
              </Link>
            </div>
          )}
        </section>
      )}
      {bestEntries.length === 0 && (
        <section className="mb-8 rounded-lg border border-neutral-200 bg-white p-6">
          <p className="text-xs uppercase tracking-widest text-neutral-400">Open problem</p>
          <p className="mt-2 text-sm text-neutral-600">
            {entries.filter((e) => e.run_count > 0).length} of {entries.length}{" "}
            approaches evaluated. No verified winner yet.
          </p>
        </section>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-xs uppercase tracking-wide text-neutral-400">
              <th className="py-2 pr-4 font-medium">Solution</th>
              <th className="py-2 pr-4 font-medium">Verified success</th>
              <th className="py-2 pr-4 font-medium">First-pass</th>
              <th className="py-2 pr-4 font-medium">Cost</th>
              <th className="py-2 pr-4 font-medium">p50 latency</th>
              <th className="py-2 pr-4 font-medium">Runs</th>
              <th className="py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => {
              const st = STATE_LABEL[e.state] ?? { label: e.state, variant: "outline" as const };
              return (
                <tr key={e.solution_id} className="border-b border-neutral-100">
                  <td className="py-3 pr-4">
                    {e.solution_type === "procedure" ? (
                      <Link
                        href={`/solutions/${e.target_id}`}
                        className="underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
                      >
                        {e.target_id}
                      </Link>
                    ) : (
                      e.target_id
                    )}
                    {e.incomparable_evaluations > 0 && (
                      <span className="ml-2 text-xs text-neutral-400">
                        ({e.incomparable_evaluations} eval not directly comparable)
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4">{pct(e.verified_success_rate)}</td>
                  <td className="py-3 pr-4">{pct(e.first_pass_success_rate)}</td>
                  <td className="py-3 pr-4">
                    {e.cost === null || e.cost === undefined ? "—" : `$${e.cost}`}
                  </td>
                  <td className="py-3 pr-4">{num(e.p50_latency_s, "s")}</td>
                  <td className="py-3 pr-4">{e.run_count}</td>
                  <td className="py-3">
                    <Badge variant={st.variant}>{st.label}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {board.conditional_leaders && (
        <p className="mt-3 text-xs text-neutral-400">
          Conditional leaders:{" "}
          {Object.entries(board.conditional_leaders)
            .filter(([, v]) => v)
            .map(([k, v]) => `${k.replace(/_/g, " ")} → ${v}`)
            .join(" · ") || "none"}
        </p>
      )}
    </div>
  );
}


