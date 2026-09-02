"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ErrorState, Metric, ProvenanceBlock, SectionHeading } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getImplementation, getImplementationCapability } from "@/lib/api/client";
import type { Implementation } from "@/lib/api/types";

export default function ImplementationPage() {
  const { id } = useParams<{ id: string }>();
  const [impl, setImpl] = useState<Implementation | null>(null);
  const [capability, setCapability] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getImplementation(id)
      .then((i) => {
        if (!alive) return;
        setImpl(i);
        getImplementationCapability(i.id)
          .then((c) => alive && setCapability(c))
          .catch(() => {});
      })
      .catch((err) => alive && setStatus(err?.status ?? 0));
    return () => {
      alive = false;
    };
  }, [id]);

  if (status !== null) {
    return (
      <ErrorState
        title={
          status === 404
            ? "Implementation not found."
            : "Unable to load implementation."
        }
        status={status}
      />
    );
  }

  if (!impl) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-4 w-full max-w-lg" />
        <Skeleton className="h-4 w-52" />
      </div>
    );
  }

  const fields = (
    [
      ["Provider", impl.provider],
      ["Type", impl.kind],
      ["Status", impl.status],
      ["Version", impl.version],
      ["License", impl.license],
      ["Author", impl.author],
      ["Requirements", impl.requirements ? JSON.stringify(impl.requirements) : null],
    ] as [string, unknown][]
  ).filter(([, v]) => v !== null && v !== undefined && v !== "");

  const pEstimate = capability?.p_estimate as number | null | undefined;
  const evidenceCount = capability?.evidence_count as number | null | undefined;

  return (
    <article className="pt-16">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{impl.name ?? impl.id}</h1>
        {impl.description ? (
          <p className="mt-3 max-w-2xl text-sm text-neutral-500">{impl.description}</p>
        ) : null}
        <div className="mt-5 flex items-center gap-4">
          <Metric label="verified success" value={pEstimate != null ? `${Math.round(pEstimate * 1000) / 10}%` : null} />
          <Metric label="executions" value={evidenceCount ?? null} />
        </div>
      </header>

      <section className="mt-14">
        <SectionHeading>Details</SectionHeading>
        <dl className="mt-4 space-y-2">
          {fields.map(([k, v]) => (
            <div key={k} className="flex gap-3 text-sm">
              <dt className="w-32 shrink-0 text-neutral-500">{k}</dt>
              <dd className="min-w-0 break-words text-neutral-800">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </section>

      {impl.source_ref || impl.derived_from ? (
        <div className="mt-14">
          <ProvenanceBlock
            provenance={{
              source: impl.source_ref,
              derived_from: impl.derived_from,
            }}
          />
        </div>
      ) : null}
    </article>
  );
}
