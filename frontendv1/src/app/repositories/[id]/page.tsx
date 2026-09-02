"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, SectionHeading } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getRepository } from "@/lib/api/client";
import type { RepositoryKnowledgeResponse } from "@/lib/api/types";

export default function RepositoryPage() {
  const { id } = useParams<{ id: string }>();
  const [repo, setRepo] = useState<RepositoryKnowledgeResponse | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getRepository(id)
      .then((r) => alive && setRepo(r))
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
            ? "Repository not found."
            : "Unable to load repository knowledge."
        }
        status={status}
      />
    );
  }

  if (!repo) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-4 w-full max-w-lg" />
        <Skeleton className="h-4 w-full max-w-md" />
      </div>
    );
  }

  return (
    <article className="pt-16">
      <header>
        <h1 className="font-mono text-xl font-semibold tracking-tight">
          {repo.repository_id}
        </h1>
        {repo.confidence_summary ? (
          <p className="mt-3 text-sm text-neutral-500">
            {repo.confidence_summary.total_claims ?? 0} claims ·{" "}
            {repo.confidence_summary.total_procedures ?? 0} procedures
          </p>
        ) : null}
      </header>

      <section className="mt-14">
        <SectionHeading>Current understanding</SectionHeading>
        {repo.claims.length === 0 ? (
          <EmptyState title="No claims recorded for this repository yet." />
        ) : (
          <ul className="mt-4 max-w-2xl space-y-6">
            {repo.claims.map((c) => (
              <li key={c.id}>
                <p className="text-sm text-neutral-800">
                  {c.statement ?? c.name}
                </p>
                <p className="mt-1 text-xs text-neutral-400">
                  Confidence: {c.confidence ?? c.truth_state ?? "unknown"}
                </p>
                <Link
                  href={`/claims/${c.id}`}
                  className="mt-1 inline-block text-xs text-neutral-500 underline"
                >
                  View evidence
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {repo.conflicts.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Conflicts</SectionHeading>
          <ul className="mt-4 max-w-2xl space-y-4">
            {repo.conflicts.map((c) => (
              <li key={c.id} className="text-sm text-neutral-700">
                {c.statement ?? c.name}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="mt-14">
        <SectionHeading>Relevant procedures</SectionHeading>
        {repo.relevant_procedures.length === 0 ? (
          <EmptyState title="No relevant procedures yet." />
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {repo.relevant_procedures.map((p) => (
              <li key={p.id} className="py-3">
                <Link
                  href={`/procedures/${p.id}`}
                  className="flex items-baseline justify-between transition-colors"
                >
                  <span className="text-sm text-neutral-900">{p.name}</span>
                  <span className="text-xs text-neutral-400">
                    {p.verification_state}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
