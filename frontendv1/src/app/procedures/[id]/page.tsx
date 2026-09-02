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
import { getProcedure } from "@/lib/api/client";
import type { ProcedureDetail } from "@/lib/api/types";

function stepLabel(step: { [key: string]: unknown }, i: number): string {
  const text =
    step.name ?? step.title ?? step.description ?? step.goal ?? null;
  return typeof text === "string" ? `${i + 1}. ${text}` : `${i + 1}. Step ${i + 1}`;
}

export default function ProcedurePage() {
  const { id } = useParams<{ id: string }>();
  const [proc, setProc] = useState<ProcedureDetail | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setProc(null);
    setStatus(null);
    getProcedure(id)
      .then((p) => alive && setProc(p))
      .catch((err) => alive && setStatus(err?.status ?? 0));
    return () => {
      alive = false;
    };
  }, [id]);

  if (status !== null) {
    return (
      <ErrorState
        title={status === 404 ? "Procedure not found." : "Unable to load procedure."}
        status={status}
      />
    );
  }

  if (!proc) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-96" />
        <Skeleton className="h-4 w-64" />
        <Skeleton className="h-4 w-40" />
        <div className="pt-8 space-y-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-5 w-full max-w-lg" />
          ))}
        </div>
      </div>
    );
  }

  const es = proc.evidence_summary;

  return (
    <article className="pt-16">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          {proc.name}
        </h1>
        {proc.goal ? (
          <p className="mt-3 max-w-2xl text-base text-neutral-500">{proc.goal}</p>
        ) : null}
        <div className="mt-5 flex items-center gap-4">
          <StatusBadge verificationState={proc.verification_state} />
          {es ? (
            <>
              <Metric label="verified success" value={es.p_estimate !== null && es.p_estimate !== undefined ? `${Math.round(es.p_estimate * 1000) / 10}%` : null} />
              <Metric label="executions" value={es.evidence_count ?? es.total ?? null} />
            </>
          ) : null}
          <Metric label="version" value={proc.version ?? null} />
        </div>
      </header>

      {proc.steps.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Procedure</SectionHeading>
          <ol className="mt-4 max-w-2xl space-y-3">
            {proc.steps.map((step, i) => (
              <li key={i} className="text-sm leading-relaxed text-neutral-800">
                {stepLabel(step, i)}
              </li>
            ))}
          </ol>
        </section>
      ) : (
        <EmptyState title="No steps recorded for this procedure yet." />
      )}

      {proc.claims.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Why this works</SectionHeading>
          <ul className="mt-4 max-w-2xl space-y-6">
            {proc.claims.map((claim, i) => (
              <li key={claim.id ?? i}>
                <p className="text-sm text-neutral-800">{String(claim.name ?? JSON.stringify(claim))}</p>
                {claim.id ? (
                  <Link
                    href={`/claims/${claim.id}`}
                    className="mt-1 inline-block text-xs text-neutral-500 underline"
                  >
                    View evidence
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="mt-14">
        <ProvenanceBlock provenance={proc.provenance} />
      </div>

      <section className="mt-14">
        <Link
          href={`/search?q=${encodeURIComponent(proc.name)}`}
          className="text-sm text-neutral-500 underline"
        >
          Compare alternative approaches
        </Link>
      </section>
    </article>
  );
}
