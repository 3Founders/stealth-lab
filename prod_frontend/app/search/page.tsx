"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { findGoals, type GoalPage, type GoalResolution } from "@/lib/kel-api";
import { goalResolutionLabel, rankingExplanation } from "@/lib/goal-display";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { bucket, track } from "@/lib/analytics";
import type { ApiState } from "@/lib/api";

const PAGE_SIZE = 10;
const filters: { key: GoalResolution; label: string }[] = [
  { key: "all", label: "All" },
  { key: "unresolved", label: "Unresolved" },
  { key: "resolved", label: "Resolved" },
];

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState("");
  const [resolution, setResolution] = useState<GoalResolution>("all");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<ApiState<GoalPage>>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);

  async function load(term: string, nextResolution: GoalResolution, nextOffset: number) {
    const normalized = term.trim();
    if (!normalized) return;
    controller.current?.abort();
    const activeController = new AbortController();
    controller.current = activeController;
    setAsked(normalized);
    setState({ kind: "loading" });
    const response = await findGoals(
      normalized,
      { limit: PAGE_SIZE, offset: nextOffset, resolved: nextResolution },
      activeController.signal,
    );
    if (controller.current !== activeController) return;
    setState(response);
    if (response.kind === "ok") {
      track("search_result", { results: bucket(response.data.goals.length) });
    }
  }

  function run(event: React.FormEvent) {
    event.preventDefault();
    track("search_submit");
    setOffset(0);
    void load(query, resolution, 0);
  }

  function changeResolution(nextResolution: GoalResolution) {
    setResolution(nextResolution);
    setOffset(0);
    const term = asked || query;
    if (term.trim()) void load(term, nextResolution, 0);
  }

  function changeOffset(nextOffset: number) {
    setOffset(Math.max(0, nextOffset));
    if (asked) void load(asked, resolution, Math.max(0, nextOffset));
  }

  useEffect(() => () => controller.current?.abort(), []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SEARCH</b></div>
        <h1 className="display"><AnimatedHeading>What are you trying to get done?</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <form className="ask" onSubmit={run} role="search">
          <label htmlFor="goal" className="skip">Describe a goal</label>
          <input id="goal" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. deploy a service to staging" autoComplete="off" />
          <button className="btn-ink" type="submit">Find ways <span className="sq" aria-hidden="true">→</span></button>
        </form>
        <div className="scopes" aria-label="Goal resolution" role="tablist">
          {filters.map((filter) => (
            <button key={filter.key} type="button" role="tab" aria-pressed={resolution === filter.key} onClick={() => changeResolution(filter.key)}>
              {filter.label}
            </button>
          ))}
        </div>
        <div className="hits" aria-live="polite">
          {state.kind === "ok" && state.data.goals.length > 0 ? (
            <ul className="list" aria-label="Search results">
              {state.data.goals.map((goal, index) => {
                const explanation = rankingExplanation(goal.ranking);
                return (
                  <li key={goal.id}>
                    <Link href={`/goals/${goal.id}`}>
                      <span className="n">{String(index + 1).padStart(2, "0")}</span>
                      <div>
                        <h3>{goal.canonical_name}</h3>
                        {goal.description && <p className="desc">{goal.description}</p>}
                        <div className="meta">
                          <span>{goalResolutionLabel(goal)}</span>
                          {explanation && <span>{explanation}</span>}
                        </div>
                      </div>
                      <span className="caption dim" aria-hidden="true">→</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : (
            <StateNotice
              state={state}
              empty={state.kind === "ok" ? `No recorded goals found for “${asked}”.` : undefined}
            />
          )}
          {state.kind === "ok" && (
            <div className="pager" aria-label="Search result pages">
              <button type="button" className="btn-ink" disabled={offset === 0} onClick={() => changeOffset(offset - PAGE_SIZE)}>
                <span>Previous</span>
              </button>
              <span className="small dim">Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
              <button type="button" className="btn-ink" disabled={!state.data.has_more} onClick={() => changeOffset(offset + PAGE_SIZE)}>
                <span>Next</span>
              </button>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
