"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { submitDecomposition } from "@/lib/api/client";
import type { DecomposeResponse } from "@/lib/api/types";

function SubmitInner() {
  const params = useSearchParams();
  const [problem, setProblem] = useState(params.get("problem") ?? "");
  const [result, setResult] = useState<DecomposeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const p = problem.trim();
    if (!p) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      // The backend runs a generate → critique panel; expect a slow call.
      setResult(await submitDecomposition(p));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pt-10">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-medium tracking-tight text-neutral-900">
          Propose a way
        </h1>
        <p className="mt-2 text-sm text-neutral-500">
          Describe a problem or a method you know works. The backend drafts a
          structured procedure proposal, which stays quarantined until a human
          approves it — it never enters search automatically.
        </p>

        <form onSubmit={onSubmit} className="mt-6 space-y-3">
          <textarea
            value={problem}
            onChange={(e) => setProblem(e.target.value)}
            rows={5}
            required
            maxLength={20000}
            aria-label="Problem or method description"
            placeholder="e.g. How do I safely rename a Postgres column in a large table without downtime?"
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-900 placeholder:text-neutral-400 outline-none transition-colors focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={loading || !problem.trim()}
              className="h-9 rounded-lg bg-neutral-900 px-5 text-sm font-medium text-neutral-50 transition-colors hover:bg-neutral-800 disabled:opacity-50"
            >
              {loading ? "Analyzing…" : "Submit proposal"}
            </button>
            {loading ? (
              <span className="text-sm text-neutral-400">
                This can take up to a minute.
              </span>
            ) : null}
          </div>
        </form>

        {error ? (
          <p
            role="alert"
            className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          >
            {error}
          </p>
        ) : null}

        {result ? (
          <section aria-label="Proposal result" className="mt-8 space-y-4">
            <div className="rounded-lg border border-neutral-200 p-5">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span
                  className={
                    result.feasible
                      ? "font-medium text-green-700"
                      : "font-medium text-amber-700"
                  }
                >
                  {result.feasible ? "Feasible" : "Not feasible"}
                </span>
                <span className="text-neutral-400">
                  {result.node_count} task nodes proposed
                </span>
                {result.is_novel ? (
                  <span className="text-neutral-400">Novel contribution</span>
                ) : null}
                <span className="text-xs text-neutral-400">
                  Status: quarantined, awaiting approval
                </span>
              </div>
              <p className="mt-3 text-sm text-neutral-600">{result.reasoning}</p>
              {result.structural_problems.length > 0 ? (
                <ul className="mt-3 list-inside list-disc text-sm text-amber-700">
                  {result.structural_problems.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              ) : null}
              {result.objections.length > 0 ? (
                <ul className="mt-3 list-inside list-disc text-sm text-amber-700">
                  {result.objections.map((o, i) => (
                    <li key={i}>{o}</li>
                  ))}
                </ul>
              ) : null}
              {result.related_existing.length > 0 ? (
                <div className="mt-3 text-sm text-neutral-500">
                  Related existing knowledge:{" "}
                  <span className="font-mono text-xs">
                    {result.related_existing.slice(0, 5).join(", ")}
                  </span>
                </div>
              ) : null}
            </div>

            {result.ops.length > 0 ? (
              <div className="rounded-lg border border-neutral-200 p-5">
                <h2 className="text-sm font-medium text-neutral-900">
                  Proposed steps
                </h2>
                <ol className="mt-3 space-y-3">
                  {result.ops.map((op, i) => (
                    <li key={i} className="text-sm">
                      <span className="mr-2 text-neutral-400">{i + 1}.</span>
                      <span className="text-neutral-900">
                        {String(op.name ?? "")}
                      </span>
                      {op.description ? (
                        <span className="block pl-6 text-neutral-500">
                          {String(op.description)}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}

            <p className="text-sm text-neutral-400">
              Proposal id{" "}
              <span className="font-mono text-xs">{result.id}</span> — approved
              or rejected via the backend review flow
              (<span className="font-mono text-xs">POST /v1/decompose/{result.id}/decide</span>).
            </p>
            <Link
              href="/"
              className="inline-block text-sm text-neutral-900 underline underline-offset-4"
            >
              Back to search
            </Link>
          </section>
        ) : null}
      </div>
    </div>
  );
}

export default function SubmitPage() {
  return (
    <Suspense fallback={null}>
      <SubmitInner />
    </Suspense>
  );
}
