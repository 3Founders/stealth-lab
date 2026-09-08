"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { SearchBox } from "@/components/search-box";
import {
  EmptyState,
  ErrorState,
  EvidenceLine,
  GoodFor,
  RelevanceBadge,
  TypeBadge,
  VerificationLabel,
  WhyMatched,
} from "@/components/domain";
import { Skeleton } from "@/components/ui/skeleton";
import {
  findBestWay,
  findProblems,
  searchSolutions,
  type Problem,
} from "@/lib/api/client";
import type { RecommendResponse, SolutionSearchHit } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "problem" | "procedure" | "task";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "problem", label: "Problems" },
  { key: "procedure", label: "Procedures" },
  { key: "task", label: "Tasks" },
];

function ResultRow({
  hit,
  selected,
  innerRef,
}: {
  hit: SolutionSearchHit;
  selected: boolean;
  innerRef?: (el: HTMLAnchorElement | null) => void;
}) {
  const href =
    hit.type === "procedure" ? `/solutions/${hit.id}` : `/tasks/${hit.id}`;
  const isProcedure = hit.type === "procedure";
  return (
    <li>
      <Link
        ref={innerRef}
        href={href}
        data-result-link
        className={cn(
          "block py-4 transition-colors",
          selected && "rounded-lg bg-neutral-50"
        )}
      >
        {/* 1. what it does */}
        <div className="flex flex-wrap items-center gap-2">
          <TypeBadge type={hit.type} />
          <span className="text-base text-neutral-900">{hit.title}</span>
          {/* 2. why it is relevant */}
          {isProcedure ? <RelevanceBadge label={hit.relevance_label} /> : null}
          {/* 3. whether it is verified */}
          {isProcedure ? (
            <VerificationLabel
              state={hit.verification?.verification_state}
              provenance={typeof hit.provenance === "string" ? hit.provenance : null}
            />
          ) : null}
        </div>
        {hit.goal ? (
          <p className="mt-1 text-sm text-neutral-600">{hit.goal}</p>
        ) : null}
        {/* 4. evidence / capability */}
        {isProcedure ? (
          <div className="mt-2">
            <EvidenceLine evidence={hit.evidence_summary} />
          </div>
        ) : null}
        {/* 5. applicability */}
        <GoodFor summary={hit.applicability_summary} />
        {/* why this matched */}
        <WhyMatched reason={hit.relevance_reason} />
      </Link>
    </li>
  );
}

function ProblemResultRow({ problem }: { problem: Problem }) {
  return (
    <li>
      <Link
        href={`/problems/${problem.id}`}
        data-result-link
        className="block py-4 transition-colors"
      >
        <div className="flex items-center gap-3">
          <TypeBadge type="problem" />
          <span className="text-base text-neutral-900">{problem.title}</span>
        </div>
        {problem.objective ? (
          <p className="mt-1 text-sm text-neutral-500">{problem.objective}</p>
        ) : null}
        <div className="mt-2 flex items-center gap-4">
          <span className="text-xs text-neutral-500">{problem.status}</span>
          {problem.id ? (
            <span className="text-xs text-neutral-400">
              Benchmark &amp; leaderboard available
            </span>
          ) : null}
        </div>
      </Link>
    </li>
  );
}

function BestWayCard({ rec }: { rec: NonNullable<RecommendResponse["recommendation"]> }) {
  return (
    <section
      aria-label="Best way"
      className="mt-6 rounded-lg border border-neutral-200 bg-neutral-50/50 p-5"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs font-medium uppercase tracking-wide text-neutral-500">
        Best way
        <RelevanceBadge label={rec.relevance_label} />
        <VerificationLabel state={rec.verification_state} />
      </div>
      <Link
        href={`/solutions/${rec.id}`}
        className="mt-2 block text-lg text-neutral-900 hover:underline"
      >
        {rec.display_name || rec.name}
      </Link>
      {rec.display_description ? (
        <p className="mt-1 text-sm text-neutral-600">{rec.display_description}</p>
      ) : null}
      <GoodFor summary={rec.applicability_summary} />
      <WhyMatched reason={rec.relevance_reason} />
      {rec.capability_note ? (
        <p className="mt-2 text-xs text-neutral-500">{rec.capability_note}</p>
      ) : null}
    </section>
  );
}

function SearchInner() {
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const filter = (params.get("type") as Filter | null) ?? "all";
  const [results, setResults] = useState<SolutionSearchHit[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [best, setBest] = useState<RecommendResponse | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);

  const setParam = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      window.history.replaceState(null, "", `/search?${next.toString()}`);
    },
    [params]
  );

  useEffect(() => {
    if (!q.trim()) {
      setResults(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      setStatus(null);
      setBest(null);
      setProblems([]);
      try {
        // Ranked recommendation, blended search, and problem lookup run in
        // parallel. Problems render above procedures/tasks when matched.
        const [res, rec, probs] = await Promise.all([
          searchSolutions(q.trim()),
          findBestWay(q.trim(), controller.signal).catch(() => null),
          findProblems(q.trim(), 3).catch(() => [] as Problem[]),
        ]);
        setResults(res.results);
        setNote(res.note);
        setBest(rec);
        setProblems(probs);
      } catch (err) {
        setResults([]);
        setStatus(err instanceof Error ? 0 : 500);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [q]);

  const visible = results?.filter(
    (r) => filter === "all" || r.type === filter
  );

  // Arrow-key navigation over results.
  const [selectedIdx, setSelectedIdx] = useState(-1);
  useEffect(() => {
    setSelectedIdx(-1);
  }, [q, filter]);
  const onListKeyDown = (e: React.KeyboardEvent) => {
    if (!visible?.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx((i) => {
        const next =
          e.key === "ArrowDown"
            ? Math.min(i + 1, visible.length - 1)
            : Math.max(i - 1, 0);
        const links = document.querySelectorAll<HTMLAnchorElement>(
          "[data-result-link]"
        );
        links[next]?.focus();
        return next;
      });
    }
  };

  return (
    <div className="pt-10">
      <div className="max-w-2xl">
        <SearchBox initialQuery={q} size="sm" />
      </div>

      <div className="mt-8 flex items-center gap-1 border-b border-neutral-100">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setParam("type", f.key === "all" ? "" : f.key)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              filter === f.key
                ? "border-neutral-900 text-neutral-900"
                : "border-transparent text-neutral-400 hover:text-neutral-700"
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="mt-8 space-y-6">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-5 w-72" />
              <Skeleton className="h-4 w-full max-w-md" />
            </div>
          ))}
        </div>
      ) : status !== null ? (
        <ErrorState title="Search temporarily unavailable." status={status} />
      ) : !q.trim() ? (
        <EmptyState
          title="Describe what you're trying to accomplish."
          hint="Try a broader description."
        />
      ) : visible && visible.length > 0 ? (
        <>
          {best?.recommendation ? <BestWayCard rec={best.recommendation} /> : null}
          <ul
            className="mt-4 divide-y divide-neutral-100"
            onKeyDown={onListKeyDown}
          >
          {visible.map((hit, i) => (
            <ResultRow
              key={`${hit.id}-${i}`}
              hit={hit}
              selected={i === selectedIdx}
            />
          ))}
          </ul>
        </>
      ) : (
        <EmptyState
          title="No verified ways found yet."
          hint="We haven't seen a reliable procedure for this yet. Try a broader description."
        >
          <Link
            href={`/submit?problem=${encodeURIComponent(q)}`}
            className="text-sm text-neutral-900 underline underline-offset-4"
          >
            Propose a way to solve this
          </Link>
        </EmptyState>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchInner />
    </Suspense>
  );
}
