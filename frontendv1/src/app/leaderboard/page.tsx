"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  getContributorLeaderboard,
  LEADERBOARD_METRICS,
  METRIC_LABEL,
  type LeaderboardEntry,
  type LeaderboardMetric,
} from "@/lib/api/people";

export default function LeaderboardPage() {
  const [metric, setMetric] = useState<LeaderboardMetric>("verified_procedures");
  const [entries, setEntries] = useState<LeaderboardEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setEntries(null);
    setError(null);
    getContributorLeaderboard(metric, 50)
      .then((r) => live && setEntries(r.entries))
      .catch(
        (e) =>
          live &&
          setError(e instanceof Error ? e.message : "Couldn’t load the leaderboard.")
      );
    return () => {
      live = false;
    };
  }, [metric]);

  return (
    <div className="max-w-3xl pt-16">
      <h1 className="text-2xl font-semibold tracking-tight">
        Contributor leaderboard
      </h1>
      <p className="mt-2 text-sm text-neutral-500">
        Contributors who’ve made their profile public, ranked by verified
        contribution. For how well a <em>method</em> solves a problem, see the{" "}
        <Link href="/problems" className="underline">
          problem
        </Link>{" "}
        pages.
      </p>

      <div className="mt-6 flex flex-wrap gap-1">
        {LEADERBOARD_METRICS.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMetric(m)}
            aria-pressed={metric === m}
            className={
              "rounded-md px-3 py-1.5 text-sm transition-colors " +
              (metric === m
                ? "bg-neutral-900 text-neutral-50"
                : "border border-neutral-200 text-neutral-600 hover:bg-neutral-50")
            }
          >
            {METRIC_LABEL[m]}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {error ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </p>
        ) : entries === null ? (
          <p className="text-sm text-neutral-400">Loading…</p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No public contributor profiles yet. Make yours public from{" "}
            <Link href="/me/privacy" className="underline">
              Privacy &amp; Data
            </Link>
            .
          </p>
        ) : (
          <ol className="divide-y divide-neutral-100">
            {entries.map((e, i) => (
              <li
                key={e.user_id}
                className="flex items-center gap-4 py-3 transition-colors hover:bg-black/[0.02]"
              >
                <span className="w-6 text-right text-sm tabular-nums text-neutral-400">
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <Link
                    href={`/contributors/${e.user_id}`}
                    className="text-sm font-medium text-neutral-900 hover:underline"
                  >
                    {e.display_name}
                  </Link>
                  {e.tagline ? (
                    <p className="truncate text-xs text-neutral-500">
                      {e.tagline}
                    </p>
                  ) : null}
                </div>
                <span className="text-sm font-semibold tabular-nums text-neutral-900">
                  {e.value}
                </span>
                <span className="hidden text-xs text-neutral-400 sm:inline">
                  {METRIC_LABEL[metric].toLowerCase()}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
