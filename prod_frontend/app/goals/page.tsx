"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { CATEGORIES, categoryOf, type Category } from "@/lib/mock-adapter";
import { getGoals, getGoalStats, timeAgo, type Goal, type GoalStats } from "@/lib/kel-api";
import type { ApiState } from "@/lib/api";

export default function GoalsPage() {
  const [state, setState] = useState<ApiState<Goal[]>>({ kind: "loading" });
  const [stats, setStats] = useState<Record<string, GoalStats | null>>({});
  const [cat, setCat] = useState<Category>("All");

  useEffect(() => {
    const ac = new AbortController();
    getGoals(100, ac.signal).then((r) => setState(r.kind === "ok" ? { kind: "ok", data: r.data.goals ?? [] } : (r as ApiState<Goal[]>)));
    return () => ac.abort();
  }, []);

  // Per-row stats are real (solutions + evaluations), not mocked — fetched once the goal
  // list itself is in, one small pair of calls per row. See lib/kel-api.ts#getGoalStats.
  useEffect(() => {
    if (state.kind !== "ok") return;
    const ac = new AbortController();
    Promise.all(state.data.map(async (p) => [p.id, await getGoalStats(p.id, ac.signal)] as const)).then((pairs) => {
      setStats(Object.fromEntries(pairs));
    });
    return () => ac.abort();
  }, [state]);

  const rows =
    state.kind === "ok"
      ? state.data
          .filter((p) => cat === "All" || categoryOf(p) === cat)
          .slice()
          .sort((a, b) => {
            const sa = stats[a.id], sb = stats[b.id];
            const score = (s: GoalStats | null | undefined) => (s ? s.verifiedRuns * 2 + s.ways : 0);
            return score(sb) - score(sa);
          })
      : [];

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>GOALS</b></div>
        <h1 className="display"><AnimatedHeading>Goals</AnimatedHeading></h1>
        <p className="lead">Discover goals worth accomplishing, and the ways people and agents have found to reach them.</p>
        <p className="small dim" style={{ gridColumn: "1 / span 12", marginTop: 8 }}>
          <Link href="/goals/add" style={{ textDecoration: "underline" }}>Add a goal →</Link> if yours isn&rsquo;t here yet, or open one below to contribute a way or a benchmark to it.
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        <nav className="tabs" aria-label="Category">
          {CATEGORIES.map((c) => (
            <button key={c} type="button" aria-pressed={cat === c} onClick={() => setCat(c)}>{c}</button>
          ))}
        </nav>

        <div style={{ gridColumn: "1 / span 12" }}>
          <h2 className="h3" style={{ marginBottom: 4 }}>Hot goals</h2>
        </div>

        {state.kind === "ok" && rows.length > 0 ? (
          <ul className="list" aria-label="Goals">
            {rows.map((p, i) => {
              const s = stats[p.id];
              const recent = s?.lastActivity ? timeAgo(s.lastActivity) : null;
              return (
                <li key={p.id}>
                  <Link href={`/goals/${p.id}`}>
                    <span className="n">{String(i + 1).padStart(3, "0")}</span>
                    <div>
                      <h3>{p.canonical_name}</h3>
                      {p.description && <p className="desc">{p.description}</p>}
                      <div className="meta">
                        <span>{categoryOf(p)}</span>
                        <span>{s ? `${s.ways} way${s.ways === 1 ? "" : "s"}` : "…"}</span>
                        <span>{s ? `${s.verifiedRuns} verified run${s.verifiedRuns === 1 ? "" : "s"}` : "…"}</span>
                        <span>{recent ? `updated ${recent}` : "no recorded activity yet"}</span>
                      </div>
                    </div>
                    <span className="caption dim" aria-hidden="true">→</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : (
          <StateNotice state={state} empty={state.kind === "ok" ? "No goals are recorded yet." : undefined} />
        )}
      </section>
    </>
  );
}
