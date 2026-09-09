"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  getLeaderboard,
  getProblem,
  getProblemBenchmarks,
  getProblemSolutions,
  getProblemEvaluations,
  getProcedure,
  type Leaderboard,
  type Evaluation,
  type Problem,
  ApiError,
} from "@/lib/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { LeaderboardTable } from "@/components/leaderboard";

export default function ProblemPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [problem, setProblem] = useState<Problem | null>(null);
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [benchmarks, setBenchmarks] = useState<Record<string, unknown>[]>([]);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [solutionCount, setSolutionCount] = useState<number | null>(null);
  const [solutions, setSolutions] = useState<
    { id: string; solution_type: string; target_id: string }[]
  >([]);
  const [targetNames, setTargetNames] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<
    "loading" | "ready" | "notfound" | "error"
  >("loading");
  const [errMsg, setErrMsg] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    Promise.all([
      getProblem(id),
      getLeaderboard(id).catch((e) => {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }),
      getProblemBenchmarks(id).catch(() => [] as Record<string, unknown>[]),
      getProblemSolutions(id).catch(() => [] as Record<string, unknown>[]),
      getProblemEvaluations(id).catch(() => [] as Evaluation[]),
    ])
      .then(([p, lb, bs, sols, evs]) => {
        if (!alive) return;
        if (!p) {
          setStatus("notfound");
          return;
        }
        setProblem(p);
        setBoard(lb);
        setBenchmarks(bs);
        setSolutionCount(sols.length);
        setSolutions(sols as { id: string; solution_type: string; target_id: string }[]);
        setEvaluations(evs);
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
  }, [id]);

  // Resolve human-readable titles for the current-best targets only.
  const bestIdKey = (board?.current_best ?? []).join(",");
  useEffect(() => {
    if (!bestIdKey) return;
    const targets = bestIdKey
      .split(",")
      .map((sid) => solutions.find((s) => s.id === sid)?.target_id)
      .filter((t): t is string => !!t && !targetNames[t]);
    for (const tid of targets) {
      getProcedure(tid)
        .then((p) => p?.name && setTargetNames((m) => ({ ...m, [tid]: p.name })))
        .catch(() => setTargetNames((m) => ({ ...m, [tid]: "" })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bestIdKey, solutions]);

  if (status === "loading") {
    return (
      <div className="space-y-6 pt-16">
        <Skeleton className="h-9 w-2/3" />
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (status === "notfound") {
    return <p className="pt-16 text-sm text-neutral-500">Problem not found.</p>;
  }
  if (status === "error") {
    return (
      <p className="pt-16 text-sm text-neutral-500">
        Unable to load problem.{errMsg ? ` (${errMsg})` : ""}
      </p>
    );
  }
  if (!problem) return null;

  const entries = board?.leaderboard ?? [];
  const bySolution = new Map(
    (solutions as { id: string; solution_type: string; target_id: string }[]).map(
      (s) => [s.id, s]
    )
  );
  const evaldCount = entries.filter((e) => e.run_count > 0).length;
  const verifiedCount = entries.filter(
    (e) => e.state === "BEST_VERIFIED" || e.state === "PROMISING"
  ).length;

  // §13 hero: current best derived ENTIRELY from the backend leaderboard.
  const bestIds: string[] = board?.current_best ?? [];
  const bestEntries = bestIds
    .map((sid) => entries.find((e) => e.solution_id === sid))
    .filter(Boolean) as (typeof entries)[number][];
  const bestTarget = (sid: string) => {
    const s = bySolution.get(sid);
    return s ? { type: s.solution_type, targetId: s.target_id } : null;
  };
  const cl = board?.conditional_leaders ?? {};
  const leaders = (
    ["verified_success_wilson_lower", "cost", "p50_latency_s", "first_pass_success_rate"] as const
  )
    .map((k) => ({
      key: k,
      label:
        k === "verified_success_wilson_lower"
          ? "Best reliability"
          : k === "cost"
            ? "Best cost"
            : k === "p50_latency_s"
              ? "Best latency"
              : "Best first-pass success",
      sid: (cl as Record<string, string | null>)[k],
    }))
    .filter((l) => l.sid) as { key: string; label: string; sid: string }[];

  return (
    <div className="pt-12">
      <p className="text-xs uppercase tracking-widest text-neutral-400">Problem</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">
        {problem.title}
      </h1>
      {problem.objective && (
        <p className="mt-3 max-w-2xl text-sm text-neutral-600">{problem.objective}</p>
      )}
      <p className="mt-4 text-sm text-neutral-500">
        {solutionCount ?? "—"} candidate solutions · {verifiedCount} verified ·{" "}
        {evaldCount} evaluated
      </p>

      {/* Benchmark hero */}
      {bestEntries.length > 0 ? (
        <section
          className="mt-8 rounded-lg border border-neutral-200 p-6"
          data-testid="current-best-hero"
        >
          <p className="text-xs uppercase tracking-widest text-neutral-400">
            {bestEntries.length > 1 ? "Current best verified (tie)" : "Current best verified"}
          </p>
          {bestEntries.map((e) => {
            const t = bestTarget(e.solution_id);
            return (
              <div key={e.solution_id} className="mt-3">
                <Link
                  href={t?.type === "task" ? `/tasks/${t.targetId}` : `/solutions/${t?.targetId ?? e.target_id}`}
                  className="text-lg font-medium text-neutral-900 underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
                >
                  {targetNames[t?.targetId ?? ""] ||
                    `${t?.type === "task" ? "Task" : "Solution"} ${(t?.targetId ?? "").slice(0, 8)}…`}
                </Link>
                <p className="mt-2 text-2xl font-semibold tracking-tight text-neutral-900">
                  {e.verified_success_rate != null
                    ? `${(e.verified_success_rate * 100).toFixed(1)}% verified success`
                    : "success rate unavailable"}
                </p>
                <p className="mt-1 text-sm text-neutral-500">
                  {e.run_count != null ? `n = ${e.run_count}` : "run count unavailable"}
                  {e.p50_latency_s != null ? ` · p50 ${e.p50_latency_s}s` : ""}
                  {e.cost != null ? ` · ~$${e.cost}` : ""}
                </p>
              </div>
            );
          })}
          <div className="mt-5 flex gap-3">
            <Link
              href="#leaderboard"
              className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-neutral-50 hover:bg-neutral-800"
            >
              Compare
            </Link>
          </div>
        </section>
      ) : entries.length > 0 ? (
        <section className="mt-8 rounded-lg border border-neutral-200 p-6">
          <p className="text-xs uppercase tracking-widest text-neutral-400">Open problem</p>
          <p className="mt-2 text-lg font-medium text-neutral-900">
            No verified winner yet.
          </p>
          <p className="mt-1 text-sm text-neutral-500">
            {entries.length} approach{entries.length === 1 ? "" : "es"} discovered ·{" "}
            {evaldCount} evaluated, insufficient evidence for a leader.
          </p>
        </section>
      ) : null}

      {leaders.length > 0 && (
        <section className="mt-6">
          <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
            {leaders.map((l) => (
              <div key={l.key}>
                <span className="text-neutral-400">{l.label}: </span>
                <Link
                  href={`/solutions/${(bySolution.get(l.sid) ?? { target_id: l.sid }).target_id}`}
                  className="text-neutral-900 underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
                >
                  {(bySolution.get(l.sid)?.target_id ?? l.sid).slice(0, 8)}…
                </Link>
              </div>
            ))}
          </div>
        </section>
      )}

      {board && entries.length > 0 && (
        <section className="mt-8" id="leaderboard">
          <h2 className="mb-4 text-lg font-medium text-neutral-900">Leaderboard</h2>
          <p className="mb-4 text-xs text-neutral-400">
            Ranked by the backend&apos;s Wilson lower bound of verified success. Only
            mutually comparable evaluations are compared.
          </p>
          <LeaderboardTable board={board} />
        </section>
      )}

      {benchmarks.length > 0 && (
        <section className="mt-10">
          <h2 className="text-lg font-medium text-neutral-900">Benchmark</h2>
          {benchmarks.map((b) => (
            <div key={String(b.id)} className="mt-3 text-sm text-neutral-600">
              <p className="font-medium text-neutral-900">{String(b.name)}</p>
              {b.description ? <p className="mt-1">{String(b.description)}</p> : null}
              {b.version != null && (
                <p className="mt-1 text-xs text-neutral-400">
                  version {String(b.version)}
                  {b.frozen_at ? " · frozen" : ""}
                </p>
              )}
            </div>
          ))}
        </section>
      )}

      {entries.length === 0 && (
        <section className="mt-10">
          <p className="text-sm text-neutral-500">
            No comparable evaluation results yet. This problem has candidates but
            insufficient evidence.
          </p>
        </section>
      )}

      {evaluations.length > 0 && (
        <section className="mt-10">
          <h2 className="text-lg font-medium text-neutral-900">Evaluations</h2>
          <ul className="mt-3 space-y-1 text-sm">
            {evaluations.map((ev) => (
              <li key={ev.id}>
                <Link
                  href={`/evaluations/${ev.id}`}
                  className="underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
                >
                  {ev.status} · {ev.id.slice(0, 8)}…
                </Link>
                <span className="ml-2 text-xs text-neutral-400">
                  {ev.run_count != null ? `n = ${ev.run_count}` : "in progress"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

