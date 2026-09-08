"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, SectionHeading } from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import { getMe } from "@/lib/api/client";
import { getMyProfile } from "@/lib/api/people";
import type { MeResponse } from "@/lib/api/types";

export default function MePage() {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [disclosureNeeded, setDisclosureNeeded] = useState(false);

  useEffect(() => {
    let alive = true;
    getMe()
      .then((m) => alive && setMe(m))
      .catch((err) => alive && setStatus(err?.status ?? 0));
    getMyProfile()
      .then((p) => alive && setDisclosureNeeded(p.disclosure_required))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (status !== null) {
    return (
      <div className="pt-24 text-center">
        <p className="text-sm text-neutral-700">Authentication required.</p>
        <Link href="/auth" className="mt-2 inline-block text-sm underline">
          Sign in
        </Link>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="space-y-4 pt-16">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
    );
  }

  const verifiedExecutions = me.executions.filter(
    (e) => e.outcome === "success"
  ).length;

  return (
    <article className="pt-16">
      <header>
        <div className="flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold tracking-tight">Your contributions</h1>
          <Link href="/me/privacy" className="text-sm text-neutral-500 underline">
            Privacy &amp; Data
          </Link>
        </div>
        <p className="mt-2 text-sm text-neutral-500">
          What you have added to the global capability layer.
        </p>
      </header>

      {disclosureNeeded ? (
        <div className="mt-6 max-w-2xl rounded-xl border border-black/[0.08] bg-black/[0.02] p-4 text-sm">
          <p className="font-medium text-neutral-900">Your profile is private</p>
          <p className="mt-1 text-neutral-600">
            Your name and contribution counts are not shown to anyone. You can
            opt into a public profile — visible in people search and on the
            contributor leaderboard — from{" "}
            <Link href="/me/privacy" className="underline">
              Privacy &amp; Data
            </Link>
            .
          </p>
        </div>
      ) : null}

      <section className="mt-12">
        <SectionHeading>Your procedures</SectionHeading>
        {me.submitted_procedures.length === 0 ? (
          <EmptyState title="No procedures submitted yet." />
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {me.submitted_procedures.map((p) => (
              <li key={p.id} className="flex items-baseline justify-between py-3">
                <Link href={`/procedures/${p.id}`} className="text-sm text-neutral-900">
                  {p.name}
                </Link>
                <span className="text-xs text-neutral-400">{p.verification_state}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <SectionHeading>Your claims</SectionHeading>
        {me.submitted_claims.length === 0 ? (
          <EmptyState title="No claims submitted yet." />
        ) : (
          <ul className="mt-4 max-w-2xl divide-y divide-neutral-100">
            {me.submitted_claims.map((c) => (
              <li key={c.id} className="py-3 text-sm text-neutral-800">
                <Link href={`/claims/${c.id}`}>{c.name}</Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <SectionHeading>Verified executions</SectionHeading>
        <p className="mt-3 text-sm text-neutral-700">
          {verifiedExecutions} of {me.executions.length} executions succeeded.
        </p>
      </section>
    </article>
  );
}
