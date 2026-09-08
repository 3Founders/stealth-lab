"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  EmptyState,
  ErrorState,
  EvidenceLine,
  FailureModes,
  Metric,
  ProvenanceBlock,
  SectionHeading,
  VerificationLabel,
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
  const evidence = cap
    ? {
        successes: cap.success_count ?? 0,
        attempts: cap.evidence_count ?? 0,
        distinct_contexts: cap.independent_groups ?? 0,
      }
    : null;

  return (
    <article className="pt-16">
      <header>
        {/* primary identity is the display name, not the machine slug */}
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          {sol.display_name || sol.name}
        </h1>
        {sol.display_description ? (
          <p className="mt-3 max-w-2xl text-base text-neutral-600">
            {sol.display_description}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <VerificationLabel
            state={sol.verification_state}
            provenance={typeof sol.provenance === "string" ? sol.provenance : null}
          />
          <EvidenceLine evidence={evidence} />
          <Metric label="version" value={sol.version} />
          <code className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[11px] text-neutral-500">
            {sol.name}
          </code>
        </div>
      </header>

      {sol.applicability_summary ? (
        <section className="mt-14">
          <SectionHeading>When to use</SectionHeading>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-800">
            {sol.applicability_summary}
          </p>
        </section>
      ) : null}

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

      {/* Claims are the rationale for WHY this works -- kept distinct from
          recorded execution evidence, which is the EvidenceLine in the
          header. */}
      {sol.claims.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Why this works</SectionHeading>
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
        <FailureModes modes={sol.failure_modes} />
      </div>

      <div className="mt-14">
        <ProvenanceBlock provenance={sol.provenance} />
      </div>
    </article>
  );
}
