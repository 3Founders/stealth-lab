"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listProblems, type Problem } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { displayTitle } from "@/lib/text";

export default function ProblemsPage() {
  const [problems, setProblems] = useState<Problem[] | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    listProblems(50)
      .then((rows) => {
        if (!alive) return;
        setProblems(rows);
        setStatus("ready");
      })
      .catch((e) => {
        if (!alive) return;
        setErrMsg(e instanceof Error ? e.message : null);
        setStatus("error");
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="pt-24">
      <h1 className="text-2xl font-semibold tracking-tight">Problems</h1>
      <p className="mt-2 max-w-md text-sm text-neutral-500">
        Open problems with candidate solutions, benchmarks, and an
        evidence-derived leaderboard. Ranking and current-best are computed by
        the backend.
      </p>

      {status === "loading" && (
        <div className="mt-10 space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}

      {status === "error" && (
        <div className="mt-10 rounded-lg border border-dashed border-neutral-200 py-16 text-center">
          <p className="text-sm text-neutral-500">
            Unable to load problems.{errMsg ? ` (${errMsg})` : ""}
          </p>
        </div>
      )}

      {status === "ready" && problems && problems.length === 0 && (
        <div className="mt-10 rounded-lg border border-dashed border-neutral-200 py-16 text-center">
          <p className="text-sm text-neutral-500">No problems yet.</p>
        </div>
      )}

      {status === "ready" && problems && problems.length > 0 && (
        <ul className="mt-10 divide-y divide-neutral-100" data-testid="problem-list">
          {problems.map((p) => (
            <li key={p.id} className="py-4">
              <Link
                href={`/problems/${p.id}`}
                className="text-base font-medium text-neutral-900 underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
              >
                {displayTitle(p.title)}
              </Link>
              {p.objective && (
                <p className="mt-1 max-w-2xl text-sm text-neutral-500">
                  {p.objective}
                </p>
              )}
              {p.status && (
                <p className="mt-1 text-xs uppercase tracking-widest text-neutral-400">
                  {p.status}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
