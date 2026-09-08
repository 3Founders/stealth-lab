"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  searchContributors,
  type ContributorSearchResult,
} from "@/lib/api/people";

export default function PeoplePage() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<ContributorSearchResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const term = q.trim();
    if (!term) {
      setResults(null);
      setError(null);
      return;
    }
    timer.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await searchContributors(term);
        setResults(r.results);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Search failed.");
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [q]);

  return (
    <div className="max-w-2xl pt-16">
      <h1 className="text-2xl font-semibold tracking-tight">People</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Contributors who have made their profile public. Names and contribution
        counts only — nothing private.
      </p>

      <input
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search by name"
        aria-label="Search people by name"
        className="mt-6 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
      />

      <div className="mt-6">
        {loading ? (
          <p className="text-sm text-neutral-400">Searching…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </p>
        ) : results === null ? (
          <p className="text-sm text-neutral-400">
            Type a name to search public profiles.
          </p>
        ) : results.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No public profiles match “{q.trim()}”.
          </p>
        ) : (
          <ul className="divide-y divide-neutral-100">
            {results.map((r) => (
              <li key={r.user_id}>
                <Link
                  href={`/contributors/${r.user_id}`}
                  className="flex flex-col gap-0.5 py-3 transition-colors hover:bg-black/[0.02]"
                >
                  <span className="text-sm font-medium text-neutral-900">
                    {r.display_name}
                  </span>
                  {r.tagline ? (
                    <span className="text-xs text-neutral-500">{r.tagline}</span>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
