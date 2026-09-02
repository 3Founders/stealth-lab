"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ErrorState, SectionHeading } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getClaim, getClaimDependents, getClaimEvidence } from "@/lib/api/client";
import type { ClaimDependent, ClaimDetail, Evidence } from "@/lib/api/types";

export default function ClaimPage() {
  const { id } = useParams<{ id: string }>();
  const [claim, setClaim] = useState<ClaimDetail | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [dependents, setDependents] = useState<ClaimDependent[]>([]);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getClaim(id)
      .then((c) => {
        if (!alive) return;
        setClaim(c);
        getClaimEvidence(c.id)
          .then((e) => alive && setEvidence(e))
          .catch(() => {});
        getClaimDependents(c.id)
          .then((d) => alive && setDependents(d))
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
        title={status === 404 ? "Claim not found." : "Unable to load claim."}
        status={status}
      />
    );
  }

  if (!claim) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-6 w-96" />
        <Skeleton className="h-4 w-52" />
      </div>
    );
  }

  return (
    <article className="pt-16">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">{claim.name}</h1>
        {claim.t_valid ? (
          <p className="mt-2 font-mono text-xs text-neutral-400">
            valid since {claim.t_valid}
          </p>
        ) : null}
      </header>

      <section className="mt-12">
        <SectionHeading>Evidence</SectionHeading>
        {evidence.length === 0 ? (
          <p className="mt-3 text-sm text-neutral-400">No evidence recorded.</p>
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {evidence.map((e) => (
              <li key={e.id} className="flex items-baseline justify-between py-3 text-sm">
                <span className="text-neutral-800">
                  {e.evidence_type ?? "observation"}
                  {e.outcome_status ? ` — ${e.outcome_status}` : ""}
                </span>
                <span className="text-xs text-neutral-400">
                  {[e.target_type, e.strength_score !== null ? `strength ${e.strength_score}` : null]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {dependents.length > 0 ? (
        <section className="mt-12">
          <SectionHeading>Dependent procedures</SectionHeading>
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {dependents.map((d) => (
              <li key={d.id} className="py-3">
                <Link href={`/procedures/${d.id}`} className="text-sm text-neutral-800">
                  {d.name}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
