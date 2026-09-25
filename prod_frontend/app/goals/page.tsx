"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { CATEGORIES, categoryOf, type Category } from "@/lib/mock-adapter";
import { getGoals, type Goal, type GoalPage, type GoalResolution, type GoalView } from "@/lib/kel-api";
import { goalResolutionLabel, rankingExplanation, splitGoalsByResolution } from "@/lib/goal-display";
import type { ApiState } from "@/lib/api";

const PAGE_SIZE = 50;
const resolutionFilters: { key: GoalResolution; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unresolved", label: "Unresolved" },
  { key: "resolved", label: "Resolved" },
];

const viewOptions: { key: GoalView; label: string }[] = [
  { key: "roots", label: "Browse" },
  { key: "all", label: "All goals" },
];

export default function GoalsPage() {
  const [state, setState] = useState<ApiState<GoalPage>>({ kind: "loading" });
  const [resolution, setResolution] = useState<GoalResolution>("all");
  const [offset, setOffset] = useState(0);
  const [cat, setCat] = useState<Category>("All");
  const [view, setView] = useState<GoalView>("roots");

  useEffect(() => {
    const ac = new AbortController();
    let active = true;
    getGoals({ limit: PAGE_SIZE, offset, resolved: resolution, view }, ac.signal).then((response) => {
      if (active) setState(response);
    });
    return () => {
      active = false;
      ac.abort();
    };
  }, [offset, resolution, view]);

  const goals = state.kind === "ok" ? state.data.goals : [];
  const split = useMemo(() => splitGoalsByResolution(goals), [goals]);
  const sections = view === "roots"
    ? [{ key: "browse", label: "Goals", goals }]
    : resolution === "all"
    ? [
        { key: "unresolved", label: "Unresolved goals", goals: split.unresolved },
        { key: "resolved", label: "Resolved goals", goals: split.resolved },
      ]
    : resolution === "unresolved"
      ? [{ key: "unresolved", label: "Unresolved goals", goals: split.unresolved }]
      : [{ key: "resolved", label: "Resolved goals", goals: split.resolved }];

  function visibleGoals(items: Goal[]): Goal[] {
    return items.filter((goal) => cat === "All" || categoryOf(goal) === cat);
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>GOALS</b></div>
        <h1 className="display"><AnimatedHeading>Goals</AnimatedHeading></h1>
        <p className="lead">Discover goals worth accomplishing and the ways people and agents have found to reach them.</p>
        <p className="small dim" style={{ gridColumn: "1 / span 12", marginTop: 8 }}>
          <Link href="/goals/add" style={{ textDecoration: "underline" }}>Add a goal →</Link> if yours isn&rsquo;t here yet, or open one below to contribute a way or a benchmark to it.
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        <div style={{ gridColumn: "1 / span 12", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px 32px" }}>
        <nav className="tabs" style={{ marginTop: 0 }} aria-label="Goal view">
          {viewOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={view === option.key}
              onClick={() => {
                setView(option.key);
                setOffset(0);
              }}
            >
              {option.label}
            </button>
          ))}
        </nav>

        <nav className="tabs" style={{ marginTop: 0 }} aria-label="Goal resolution">
          {resolutionFilters.map((filter) => (
            <button
              key={filter.key}
              type="button"
              aria-pressed={resolution === filter.key}
              onClick={() => {
                setResolution(filter.key);
                setOffset(0);
              }}
            >
              {filter.label}
            </button>
          ))}
        </nav>

        <nav className="tabs" style={{ marginTop: 0 }} aria-label="Category">
          {CATEGORIES.map((category) => (
            <button key={category} type="button" aria-pressed={cat === category} onClick={() => setCat(category)}>
              {category}
            </button>
          ))}
        </nav>
        </div>

        {state.kind !== "ok" ? (
          <StateNotice state={state} />
        ) : (
          <>
            {sections.map((section) => {
              const rows = visibleGoals(section.goals);
              return (
                <div key={section.key} style={{ gridColumn: "1 / span 12" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
                    <h2 className="h3" style={{ marginBottom: 4 }}>{section.label}</h2>
                    <span className="caption dim">{rows.length} shown</span>
                  </div>
                  {rows.length > 0 ? (
                    <ul className="list" aria-label={section.label}>
                      {rows.map((goal, index) => {
                        const explanation = rankingExplanation(goal.ranking);
                        return (
                          <li key={goal.id}>
                            <Link href={`/goals/${goal.id}`}>
                              <span className="n">{String(index + 1).padStart(3, "0")}</span>
                              <div>
                                <h3>{goal.canonical_name}</h3>
                                {goal.description && <p className="desc">{goal.description}</p>}
                                <div className="meta">
                                  {view === "roots" && (
                                    <span>
                                      {goal.browse_kind === "root"
                                        ? `Has ${goal.specific_count ?? 0} specific goal${goal.specific_count === 1 ? "" : "s"}`
                                        : "Standalone"}
                                    </span>
                                  )}
                                  <span>{goalResolutionLabel(goal)}</span>
                                  <span>{categoryOf(goal)}</span>
                                  {goal.created_by && <span>contributed by {goal.created_by}</span>}
                                  {explanation && <span>{explanation}</span>}
                                </div>
                              </div>
                              <span className="caption dim" aria-hidden="true">→</span>
                            </Link>
                            {view === "roots" && goal.browse_kind === "root" && (goal.specifics?.length ?? 0) > 0 && (
                              <ul className="small dim" aria-label={`Specific goals under ${goal.canonical_name}`} style={{ margin: "4px 0 12px 48px", display: "flex", gap: 12, flexWrap: "wrap", listStyle: "none", padding: 0 }}>
                                {goal.specifics!.map((specific) => (
                                  <li key={specific.id}>
                                    <Link href={`/goals/${specific.id}`} style={{ textDecoration: "underline" }}>{specific.canonical_name}</Link>
                                  </li>
                                ))}
                                {(goal.specific_count ?? 0) > goal.specifics!.length && (
                                  <li>
                                    <Link href={`/goals/${goal.id}`} style={{ textDecoration: "underline" }}>
                                      +{(goal.specific_count ?? 0) - goal.specifics!.length} more
                                    </Link>
                                  </li>
                                )}
                              </ul>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <div className="empty" style={{ marginTop: 8 }}>
                      <p>No {section.label.toLowerCase()} match this page.</p>
                    </div>
                  )}
                </div>
              );
            })}

            <div className="pager" aria-label="Goal pages">
              <button
                type="button"
                className="btn-ink"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <span>Previous</span>
              </button>
              <span className="small dim">Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
              <button
                type="button"
                className="btn-ink"
                disabled={!state.data.has_more}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                <span>Next</span>
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}
