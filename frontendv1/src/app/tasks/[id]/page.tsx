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
} from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getTask, getTaskImplementations } from "@/lib/api/client";
import type { Implementation, TaskDetail } from "@/lib/api/types";

export default function TaskPage() {
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [impls, setImpls] = useState<Implementation[] | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus(null);
    getTask(id)
      .then((t) => {
        if (!alive) return;
        setTask(t);
        getTaskImplementations(t.id)
          .then((r) => alive && setImpls(r))
          .catch(() => alive && setImpls([]));
      })
      .catch((err) => alive && setStatus(err?.status ?? 0));
    return () => {
      alive = false;
    };
  }, [id]);

  if (status !== null) {
    return (
      <ErrorState
        title={status === 404 ? "Task not found." : "Unable to load task."}
        status={status}
      />
    );
  }

  if (!task) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-4 w-full max-w-lg" />
        <Skeleton className="h-4 w-52" />
      </div>
    );
  }

  return (
    <article className="pt-16">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight text-neutral-900">
          {task.name}
        </h1>
        {task.description ? (
          <p className="mt-3 max-w-2xl text-base text-neutral-500">
            {task.description}
          </p>
        ) : null}
        <div className="mt-5 flex items-center gap-4">
          <Metric label="est. latency" value={task.latency_estimate_ms !== null ? `${task.latency_estimate_ms}ms` : null} />
          <Metric label="est. cost" value={task.cost_estimate !== null ? String(task.cost_estimate) : null} />
        </div>
      </header>

      <section className="mt-14">
        <SectionHeading>Available implementations</SectionHeading>
        {impls === null ? (
          <Skeleton className="mt-4 h-16 w-full max-w-lg" />
        ) : impls.length === 0 ? (
          <EmptyState
            title="No implementations verified yet."
            hint="Be the first to contribute one."
          />
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {impls.map((impl) => (
              <li key={impl.id} className="py-3">
                <Link
                  href={`/implementations/${impl.id}`}
                  className="flex items-baseline justify-between transition-colors"
                >
                  <span className="text-sm text-neutral-900">
                    {impl.name ?? impl.kind ?? impl.id}
                  </span>
                  <span className="text-xs text-neutral-400">
                    {[impl.provider, impl.kind, impl.status]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {task.success_criteria !== null && task.success_criteria !== undefined ? (
        <section className="mt-14">
          <SectionHeading>Success criteria</SectionHeading>
          <p className="mt-3 max-w-2xl text-sm text-neutral-700">
            {typeof task.success_criteria === "string"
              ? task.success_criteria
              : JSON.stringify(task.success_criteria)}
          </p>
        </section>
      ) : null}

      {task.dependent_procedures.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Supporting procedures</SectionHeading>
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {task.dependent_procedures.map((p) => (
              <li key={p.id} className="py-3">
                <Link
                  href={`/procedures/${p.id}`}
                  className="flex items-baseline justify-between transition-colors"
                >
                  <span className="text-sm text-neutral-900">{p.name}</span>
                  <StatusInline state={p.verification_state} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {task.known_failure_modes.length > 0 ? (
        <section className="mt-14">
          <SectionHeading>Known failures</SectionHeading>
          <ul className="mt-3 max-w-2xl space-y-1.5">
            {task.known_failure_modes.map((f) => (
              <li key={f} className="font-mono text-xs text-neutral-600">
                {f}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="mt-14">
        <ProvenanceBlock provenance={task.provenance} />
      </div>
    </article>
  );
}

function StatusInline({ state }: { state: string }) {
  return <span className="text-xs text-neutral-400">{state}</span>;
}
