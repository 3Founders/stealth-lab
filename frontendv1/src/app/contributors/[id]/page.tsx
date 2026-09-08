"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  getContributor,
  METRIC_LABEL,
  type PublicProfile,
} from "@/lib/api/people";

const COUNT_ORDER: (keyof PublicProfile["counts"])[] = [
  "verified_procedures",
  "procedures_authored",
  "claims_authored",
  "commons_publications",
];

export default function ContributorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [profile, setProfile] = useState<PublicProfile | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "notfound" | "error">(
    "loading"
  );

  useEffect(() => {
    let live = true;
    getContributor(id)
      .then((p) => {
        if (!live) return;
        setProfile(p);
        setState("ok");
      })
      .catch((e) => {
        if (!live) return;
        setState(e instanceof ApiError && e.status === 404 ? "notfound" : "error");
      });
    return () => {
      live = false;
    };
  }, [id]);

  if (state === "loading") {
    return <p className="pt-16 text-sm text-neutral-400">Loading…</p>;
  }
  if (state === "notfound") {
    return (
      <div className="pt-16 text-sm text-neutral-600">
        <p>No public profile here.</p>
        <p className="mt-1 text-neutral-400">
          This contributor hasn’t made their profile public, or the link is wrong.
        </p>
        <Link href="/people" className="mt-4 inline-block underline">
          Search people
        </Link>
      </div>
    );
  }
  if (state === "error" || !profile) {
    return (
      <p className="pt-16 text-sm text-neutral-500">
        Couldn’t load this profile.
      </p>
    );
  }

  return (
    <article className="max-w-2xl pt-16">
      <h1 className="text-2xl font-semibold tracking-tight">
        {profile.display_name}
      </h1>
      {profile.tagline ? (
        <p className="mt-2 text-sm text-neutral-500">{profile.tagline}</p>
      ) : null}

      <dl className="mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-black/[0.06] bg-black/[0.06] sm:grid-cols-4">
        {COUNT_ORDER.map((k) => (
          <div key={k} className="bg-[--background] p-4">
            <dt className="text-xs text-neutral-500">{METRIC_LABEL[k]}</dt>
            <dd className="mt-1 text-2xl font-semibold tabular-nums text-neutral-900">
              {profile.counts[k]}
            </dd>
          </div>
        ))}
      </dl>

      <p className="mt-6 text-xs text-neutral-400">
        Counts are computed live from verified provenance. Only aggregate numbers
        are public — no private procedures, traces, or execution detail.
      </p>

      <Link
        href="/leaderboard"
        className="mt-10 inline-block text-sm text-neutral-500 underline"
      >
        Contributor leaderboard
      </Link>
    </article>
  );
}
