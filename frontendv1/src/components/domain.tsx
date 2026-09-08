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

/**
 * Meaningful match strength — ONLY rendered when the backend's measured
 * relevance gate produced a label. Never invents confidence language and
 * never shows a raw similarity percentage.
 */
export function RelevanceBadge({ label }: { label: "strong" | "relevant" | null | undefined }) {
  if (!label) return null;
  return (
    <Badge variant={label === "strong" ? "accent" : "secondary"}>
      {label === "strong" ? "Strong match" : "Relevant"}
    </Badge>
  );
}

/**
 * Trust label derived from real system state. "Verified" only when the
 * backend says so; otherwise the honest weaker word.
 */
export function VerificationLabel({
  state,
  provenance,
}: {
  state?: string | null;
  provenance?: string | null;
}) {
  if (state === "verified") return <Badge variant="accent">Verified</Badge>;
  if (provenance === "prior_library" || provenance === "company_ingested")
    return <Badge variant="outline">Community reported</Badge>;
  return <Badge variant="secondary">Experimental</Badge>;
}

/** Evidence, stated as a count of real recorded executions — not a probability. */
export function EvidenceLine({
  evidence,
}: {
  evidence: { successes: number; attempts: number; distinct_contexts: number } | null | undefined;
}) {
  if (!evidence || evidence.attempts === 0) {
    return <span className="text-xs text-neutral-400">No executions recorded yet</span>;
  }
  const { successes, attempts, distinct_contexts } = evidence;
  return (
    <span className="text-xs text-neutral-600">
      <span className="font-medium text-neutral-800">{successes}</span> successful
      {" "}of {attempts} recorded execution{attempts === 1 ? "" : "s"}
      {distinct_contexts > 1 ? ` across ${distinct_contexts} contexts` : ""}
    </span>
  );
}

/** "Why this matched" — deterministic backend explanation, shown verbatim or hidden. */
export function WhyMatched({ reason }: { reason?: string | null }) {
  if (!reason) return null;
  return (
    <p className="mt-2 text-sm text-neutral-600">
      <span className="font-medium text-neutral-700">Why this matched: </span>
      {reason}
    </p>
  );
}

/** "Good for" — the procedure's own applicability summary. */
export function GoodFor({ summary }: { summary?: string | null }) {
  if (!summary) return null;
  return (
    <p className="mt-2 text-sm text-neutral-600">
      <span className="font-medium text-neutral-700">Good for: </span>
      {summary}
    </p>
  );
}

/** Known failure modes, from the procedure's own recorded failure conditions. */
export function FailureModes({ modes }: { modes?: string[] | null }) {
  if (!modes || modes.length === 0) return null;
  return (
    <section>
      <SectionHeading>Known failure modes</SectionHeading>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-neutral-800">
        {modes.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ul>
    </section>
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
  provenance: Record<string, unknown> | string | null;
}) {
  if (!provenance) return null;
  if (typeof provenance === "string") {
    return (
      <section>
        <SectionHeading>Source</SectionHeading>
        <p className="mt-3 text-sm capitalize text-neutral-800">
          {provenance.replace(/_/g, " ")}
        </p>
      </section>
    );
  }
  if (Object.keys(provenance).length === 0) return null;
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
