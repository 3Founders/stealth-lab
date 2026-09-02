"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  EmptyState,
  ErrorState,
  Metric,
  ProvenanceBlock,
  SectionHeading,
  StatusBadge,
} from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getSolution } from "@/lib/api/client";
import type { SolutionDetail } from "@/lib/api/types";

export default function SolutionPage() {
  const { id } = useParams<{ id: string }>();
  const [sol, setSol] = useState<SolutionDetail | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getSolution(id)
      .then((s) => alive && setSol(s))
      .catch((err) => alive && setStatus(err?.status ?? 0));
    return () => {
      alive = false;
    };
  }, [id]);

  if (status !== null) {
    return (
      <ErrorState
        title={status === 404 ? "Solution not found." : "Unable to load solution."}
        status={status}
      />
    );
  }

  if (!sol) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-96" />
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-24 w-full max-w-lg" />
      </div>
    );
  }

  const cap = sol.capability;

  return (
    <article className="pt-16">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          {sol.name}
        </h1>
        {sol.goal ? (
          <p className="mt-3 max-w-2xl text-base text-neutral-500">{sol.goal}</p>
        ) : null}
        <div className="mt-5 flex items-center gap-4">
          <StatusBadge verificationState={sol.verification_state} />
          {cap ? (
            <>
              <Metric
                label="verified success"
                value={
                  cap.p_estimate !== null
                    ? `${Math.round(cap.p_estimate * 1000) / 10}%`
                    : null
                }
              />
              <Metric label="executions" value={cap.evidence_count} />
            </>
          ) : null}
          <Metric label="version" value={sol.version} />
        </div>
      </header>

      <section className="mt-14">
        <SectionHeading>How it works</SectionHeading>
        <p className="mt-3 max-w-2xl text-sm text-neutral-700">
          This solution is executed as a procedure.{" "}
          <Link href={`/procedures/${sol.procedure_row_id}`} className="underline">
            Open the full procedure
          </Link>
          .
        </p>
        {Object.keys(sol.implementations).length > 0 ? (
          <ul className="mt-6 max-w-2xl divide-y divide-neutral-100">
            {Object.values(sol.implementations).map((impl) => (
              <li key={impl.kind} className="flex items-baseline justify-between py-3">
                <span className="text-sm text-neutral-900">{impl.kind}</span>
                <span className="text-xs text-neutral-400">
                  {impl.supported ? impl.strategy ?? "supported" : "not supported"}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No implementations verified yet." hint="Be the first to contribute one." />
        )}
      </section>

      {sol.claims.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Evidence</SectionHeading>
          <ul className="mt-4 max-w-2xl space-y-5">
            {sol.claims.map((claim, i) => (
              <li key={claim.id ?? i} className="text-sm text-neutral-800">
                {String(claim.name ?? JSON.stringify(claim))}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="mt-14">
        <ProvenanceBlock provenance={sol.provenance} />
      </div>
    </article>
  );
}
