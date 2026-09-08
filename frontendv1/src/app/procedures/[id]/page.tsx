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
import { ScopeBadge } from "@/components/scope-badge";
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
  const evidence = es
    ? {
        successes: es.success_count ?? 0,
        attempts: es.total ?? es.evidence_count ?? 0,
        distinct_contexts: es.independent_groups ?? 0,
      }
    : null;

  return (
    <article className="pt-16">
      <header>
        {/* human-readable title */}
        <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
          {proc.display_name || proc.name}
        </h1>
        {/* concise capability description */}
        {proc.display_description ? (
          <p className="mt-3 max-w-2xl text-base text-neutral-600">
            {proc.display_description}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <ScopeBadge
            visibility={proc.visibility}
            verification_state={proc.verification_state}
            withHint
          />
          <VerificationLabel
            state={proc.verification_state}
            provenance={typeof proc.provenance === "string" ? proc.provenance : null}
          />
          <EvidenceLine evidence={evidence} />
          <Metric label="version" value={proc.version ?? null} />
          {/* internal ontology stays available, secondary */}
          <code className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[11px] text-neutral-500">
            {proc.name}
          </code>
        </div>
      </header>

      {proc.applicability_summary ? (
        <section className="mt-14">
          <SectionHeading>When to use</SectionHeading>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-neutral-800">
            {proc.applicability_summary}
          </p>
        </section>
      ) : null}

      {proc.preconditions.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Prerequisites</SectionHeading>
          <ul className="mt-4 max-w-2xl list-disc space-y-1.5 pl-5 text-sm text-neutral-800">
            {proc.preconditions.map((pc, i) => (
              <li key={i}>
                {typeof pc === "string" ? pc : JSON.stringify(pc)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {proc.steps.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>How it works</SectionHeading>
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

      <div className="mt-14">
        <FailureModes modes={proc.failure_modes} />
      </div>

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
