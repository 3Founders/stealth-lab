import Link from "next/link";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";

export function StatusBadge({
  verificationState,
  className,
}: {
  verificationState: string;
  className?: string;
}) {
  const variant =
    verificationState === "verified" ? "accent" : "secondary";
  return (
    <Badge variant={variant} className={className}>
      {verificationState}
    </Badge>
  );
}

export function TypeBadge({ type }: { type: string }) {
  return (
    <Badge variant="outline" className="font-mono text-[11px] uppercase tracking-wide">
      {type}
    </Badge>
  );
}

export function Metric({ label, value }: { label: string; value: string | number | null }) {
  if (value === null || value === undefined) return null;
  return (
    <span className="text-xs text-neutral-500">
      <span className="font-medium text-neutral-700">{value}</span> {label}
    </span>
  );
}

export function EmptyState({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="py-16 text-center">
      <p className="text-sm text-neutral-500">{title}</p>
      {hint ? <p className="mt-1 text-sm text-neutral-400">{hint}</p> : null}
      {children}
    </div>
  );
}

export function ErrorState({
  title,
  status,
}: {
  title: string;
  status: number;
}) {
  return (
    <div className="py-16 text-center">
      <p className="text-sm text-neutral-700">{title}</p>
      {status === 401 ? (
        <p className="mt-1 text-sm text-neutral-400">
          Authentication required.{" "}
          <Link href="/auth" className="underline">
            Sign in
          </Link>
        </p>
      ) : null}
    </div>
  );
}

export function SectionHeading({ children }: { children: ReactNode }) {
  return (
    <h2 className="text-lg font-semibold tracking-tight text-neutral-900">
      {children}
    </h2>
  );
}

export function ProvenanceBlock({
  provenance,
}: {
  provenance: Record<string, unknown> | null;
}) {
  if (!provenance || Object.keys(provenance).length === 0) return null;
  return (
    <section>
      <SectionHeading>Provenance</SectionHeading>
      <dl className="mt-3 space-y-1.5">
        {Object.entries(provenance)
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <div key={k} className="flex gap-3 text-sm">
              <dt className="w-32 shrink-0 capitalize text-neutral-500">
                {k.replace(/_/g, " ")}
              </dt>
              <dd className="min-w-0 break-words text-neutral-800">
                {typeof v === "object" ? JSON.stringify(v) : String(v)}
              </dd>
            </div>
          ))}
      </dl>
    </section>
  );
}
