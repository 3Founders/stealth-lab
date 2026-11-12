"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import type { ApiState } from "@/lib/api";
import { routeGoals, type RouteGoal, type RouteResult, type RouteTool } from "@/lib/kel-api";
import "./route.css";

const EXAMPLES = [
  "send a slack message",
  "open the page, log in and screenshot the dashboard",
  "find CVEs in my repository",
  "look up stripe invoices",
];

function tier(t: string) {
  return t === "approved" ? "approved" : t === "proposed" ? "proposed" : "submitted";
}

function ToolCard({ tool }: { tool: RouteTool }) {
  const label = tool.title || tool.name || tool.server;
  return (
    <div className="route-tool">
      <div className="route-tool-head">
        <b>{label}</b>
        <span className="caption">{tool.server || tool.source}</span>
      </div>
      {tool.description && <p>{tool.description}</p>}
      <div className="route-tool-meta">
        {tool.rank_score != null && <span>match {Number(tool.rank_score).toFixed(3)}</span>}
        {Number(tool.score || 0) > 0 && <span>goal fit {Number(tool.score).toFixed(2)}</span>}
        {tool.trial && <span>new · free trial</span>}
      </div>
    </div>
  );
}

function GoalBlock({ goal }: { goal: RouteGoal }) {
  return (
    <article className="route-goal">
      <header>
        <h2>{goal.canonical_name}</h2>
        <span className="caption">
          {goal.matched_by} · score {Number(goal.score || 0).toFixed(3)}
        </span>
      </header>
      {goal.description && <p className="route-lead">{goal.description}</p>}
      <div className="route-cols">
        <section>
          <h3>Ways <span className="caption">the steps</span></h3>
          {goal.ways.length === 0 && <p className="caption">No way is bound to this Goal yet.</p>}
          {goal.ways.map((way) => (
            <div className="route-way" key={way.procedure_id}>
              <div className="route-way-head">
                <b>{way.name}</b>
                <span className="route-tier" data-tier={tier(way.approval_status)}>
                  {way.approval_status}
                </span>
              </div>
              <div className="route-cards">
                {(way.cards || []).map((card, i) => (
                  <code key={`${way.procedure_id}:${i}`}>{String(card.card_id || "")}</code>
                ))}
              </div>
            </div>
          ))}
        </section>
        <section>
          <h3>Tools <span className="caption">what executes a step</span></h3>
          {goal.tools.length === 0 && <p className="caption">No tool is mapped to this Goal yet.</p>}
          {goal.tools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
        </section>
      </div>
    </article>
  );
}

export default function RoutePage() {
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState("");
  const [state, setState] = useState<ApiState<RouteResult>>({ kind: "idle" });
  const controller = useRef<AbortController | undefined>(undefined);

  async function load(term: string) {
    const normalized = term.trim();
    if (!normalized) return;
    controller.current?.abort();
    const active = new AbortController();
    controller.current = active;
    setAsked(normalized);
    setState({ kind: "loading" });
    const response = await routeGoals(normalized, 5, active.signal);
    if (controller.current !== active) return;
    setState(response);
  }

  function run(event: React.FormEvent) {
    event.preventDefault();
    void load(query);
  }

  useEffect(() => () => controller.current?.abort(), []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ROUTE</b></div>
        <h1 className="display"><AnimatedHeading>What are you trying to get done?</AnimatedHeading></h1>
        <p className="lead small">
          A task becomes a Goal, the Goal&apos;s ways (the steps), and the tools that execute them.
          Read-only: nothing is called here.
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <form className="ask" onSubmit={run} role="search">
          <label htmlFor="route-goal" className="skip">Describe a task</label>
          <input
            id="route-goal"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. send a slack message"
            autoComplete="off"
          />
          <button className="btn-ink" type="submit">Route <span className="sq" aria-hidden="true">→</span></button>
        </form>

        <div className="scopes">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => { setQuery(example); void load(example); }}>
              {example}
            </button>
          ))}
        </div>

        {state.kind === "unconfigured" && (
          <p className="route-note">
            The router is not configured. Set <code>NEXT_PUBLIC_KEL_API_URL</code> (the FastAPI app) or{" "}
            <code>NEXT_PUBLIC_KEL_MCP_URL</code> (the MCP server) to a running backend.
          </p>
        )}
        {state.kind === "unauthenticated" && (
          <p className="route-note">Sign in to route a task. <Link href="/sign-in">Sign in</Link>.</p>
        )}
        {state.kind === "forbidden" && <p className="route-note">{state.message}</p>}
        {state.kind === "error" && (
          <p className="route-note">The router answered {state.status ?? "an error"}: {state.message}</p>
        )}
        {state.kind === "loading" && (
          <p className="route-note" role="status">
            Resolving the Goal, then ranking its ways and tools. The first search can take a few seconds.
          </p>
        )}

        {state.kind === "ok" && (
          <div className="route-results" aria-live="polite">
            {state.data.goals.length === 0 && !(state.data.tools && state.data.tools.length) && (
              <p className="route-note">Nothing matched &ldquo;{asked}&rdquo;. Try the task in other words.</p>
            )}

            {state.data.goals.length === 0 && state.data.tools && state.data.tools.length > 0 && (
              <section className="route-fallback">
                <h2>No Goal matched <span className="caption">tools that may help</span></h2>
                <p className="route-lead">
                  The Goal tier has no category for this task yet. These are the tools the catalog
                  found for the request, not a Goal&apos;s ways.
                </p>
                {state.data.tools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
              </section>
            )}

            {state.data.goals.map((goal) => <GoalBlock key={goal.goal_id} goal={goal} />)}
          </div>
        )}
      </section>
    </>
  );
}
