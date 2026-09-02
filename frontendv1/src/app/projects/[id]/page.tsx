"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, SectionHeading } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getProject } from "@/lib/api/client";
import type { ProjectKnowledgeResponse } from "@/lib/api/types";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectKnowledgeResponse | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getProject(id)
      .then((p) => alive && setProject(p))
      .catch((err) => alive && setStatus(err?.status ?? 0));
    return () => {
      alive = false;
    };
  }, [id]);

  if (status !== null) {
    return (
      <ErrorState
        title={status === 404 ? "Project not found." : "Unable to load project knowledge."}
        status={status}
      />
    );
  }

  if (!project) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-4 w-full max-w-lg" />
      </div>
    );
  }

  return (
    <article className="pt-16">
      <header>
        <h1 className="font-mono text-xl font-semibold tracking-tight">
          {project.project_id}
        </h1>
        {project.confidence_summary ? (
          <p className="mt-3 text-sm text-neutral-500">
            {project.confidence_summary.total_claims ?? 0} claims ·{" "}
            {project.confidence_summary.total_procedures ?? 0} procedures
          </p>
        ) : null}
      </header>

      <section className="mt-14">
        <SectionHeading>Project knowledge</SectionHeading>
        {project.claims.length === 0 ? (
          <EmptyState title="No claims recorded for this project yet." />
        ) : (
          <ul className="mt-4 max-w-2xl space-y-6">
            {project.claims.map((c) => (
              <li key={c.id}>
                <p className="text-sm text-neutral-800">{c.statement ?? c.name}</p>
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

      <section className="mt-14">
        <SectionHeading>Relevant procedures</SectionHeading>
        {project.relevant_procedures.length === 0 ? (
          <EmptyState title="No relevant procedures yet." />
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {project.relevant_procedures.map((p) => (
              <li key={p.id} className="py-3">
                <Link href={`/procedures/${p.id}`} className="text-sm text-neutral-900">
                  {p.name}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
