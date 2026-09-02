"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { getEvaluation, type Evaluation } from "@/lib/api/client";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

function pct(v: unknown): string {
  if (typeof v !== "number") return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-neutral-400">{label}</p>
      <p className="mt-0.5 text-sm text-neutral-900">{value}</p>
    </div>
  );
}

export default function EvaluationPage() {
  const params = useParams<{ id: string }>();
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "notfound" | "error">("loading");

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    getEvaluation(params.id)
      .then((e) => {
        if (!alive) return;
        if (e) {
          setEvaluation(e);
          setStatus("ready");
        } else setStatus("notfound");
      })
      .catch(() => alive && setStatus("error"));
    return () => {
      alive = false;
    };
  }, [params.id]);

  if (status === "loading")
    return (
      <div className="space-y-4 pt-10">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (status === "notfound")
    return <p className="pt-16 text-neutral-500">Evaluation not found.</p>;
  if (status === "error" || !evaluation)
    return <p className="pt-16 text-neutral-500">Unable to load evaluation.</p>;

  const metrics = (evaluation.metrics ?? {}) as Record<string, unknown>;
  const summary = (evaluation.verification_summary ?? {}) as Record<string, unknown>;
  const methodology = (evaluation.methodology ?? {}) as Record<string, unknown>;
  const environment = (evaluation.environment ?? {}) as Record<string, unknown>;

  return (
    <article className="pt-10">
      <p className="text-xs uppercase tracking-wide text-neutral-400">Evaluation</p>
      <h1 className="mt-1 text-3xl font-medium tracking-tight text-neutral-900">
        How this result was measured
      </h1>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{evaluation.status}</Badge>
        <span className="text-sm text-neutral-500">
          {typeof summary.run_count === "number"
            ? `n = ${summary.run_count}`
            : typeof evaluation.run_count === "number"
              ? `n = ${evaluation.run_count}`
              : "run count unavailable"}
        </span>
      </div>

      <section className="mt-8">
        <h2 className="text-lg font-medium text-neutral-900">What was measured</h2>
        <dl className="mt-3 grid grid-cols-2 gap-6 sm:grid-cols-3">
          <Metric label="Success" value={pct(metrics.success_rate ?? summary.success_rate)} />
          <Metric
            label="Verified success"
            value={pct(metrics.verified_success_rate ?? summary.verified_success_rate)}
          />
          <Metric
            label="First-pass success"
            value={pct(metrics.first_pass_success_rate)}
          />
          <Metric
            label="Cost"
            value={typeof metrics.cost === "number" ? String(metrics.cost) : "—"}
          />
          <Metric
            label="p50 latency"
            value={
              typeof metrics.p50_latency_s === "number"
                ? `${metrics.p50_latency_s}s`
                : "—"
            }
          />
          <Metric
            label="p95 latency"
            value={
              typeof metrics.p95_latency_s === "number"
                ? `${metrics.p95_latency_s}s`
                : "—"
            }
          />
        </dl>
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-medium text-neutral-900">Methodology</h2>
        {Object.keys(methodology).length ? (
          <pre className="mt-2 overflow-x-auto rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-xs text-neutral-700">
            {JSON.stringify(methodology, null, 2)}
          </pre>
        ) : (
          <p className="mt-2 text-sm text-neutral-500">Methodology details not recorded.</p>
        )}
      </section>

      <section className="mt-8">
        <h2 className="text-lg font-medium text-neutral-900">Environment</h2>
        {Object.keys(environment).length ? (
          <pre className="mt-2 overflow-x-auto rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-xs text-neutral-700">
            {JSON.stringify(environment, null, 2)}
          </pre>
        ) : (
          <p className="mt-2 text-sm text-neutral-500">Environment details not recorded.</p>
        )}
      </section>

      {evaluation.problem_id && (
        <section className="mt-8">
          <h2 className="text-lg font-medium text-neutral-900">Context</h2>
          <p className="mt-2 text-sm text-neutral-600">
            Benchmark result for{" "}
            <Link
              href={`/problems/${evaluation.problem_id}`}
              className="underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-900"
            >
              this problem
            </Link>
            . Benchmark id:{" "}
            <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs">
              {evaluation.benchmark_id ?? "—"}
            </code>
          </p>
        </section>
      )}
    </article>
  );
}
