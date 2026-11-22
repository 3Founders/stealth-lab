"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import type { ApiState } from "@/lib/api";
import { routeGoals, routeWays, type GoalRankResult, type RouteGoal, type RouteTool } from "@/lib/kel-api";
import { listPublicWays, type PublicWay } from "@/lib/public-ways";
import { priceLabel } from "@/lib/way-rate";
import "./route.css";

const EXAMPLES = [
  "send a slack message",
  "open the page, log in and screenshot the dashboard",
  "find CVEs in my repository",
  "look up stripe invoices",
];

function tier(t: string) {
  return t === "approved" || t === "ai_approved" || t === "proposed" ? t : "submitted";
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
                  {way.approval_label || way.approval_status}
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
          {goal.tools.length === 0 && <p className="caption">Tools are the next tier; not loaded in this view.</p>}
          {goal.tools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
        </section>
      </div>
    </article>
  );
}

function RankedGoal({ goal, busy, onLoad }: { goal: RouteGoal; busy: boolean; onLoad: () => void }) {
  return (
    <article className="route-goal">
      <header>
        <h2>{goal.canonical_name}</h2>
        <span className="caption">
          {goal.matched_by} · score {Number(goal.score || 0).toFixed(3)}
        </span>
      </header>
      {goal.description && <p className="route-lead">{goal.description}</p>}
      <div className="route-actions">
        <button type="button" className="btn-line" onClick={onLoad} disabled={busy}>
          {busy ? "Loading ways…" : "Show ways"}
        </button>
      </div>
    </article>
  );
}

export default function TaskPage() {
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState("");
  const [state, setState] = useState<ApiState<GoalRankResult>>({ kind: "idle" });
  const [details, setDetails] = useState<Record<string, RouteGoal>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [detailError, setDetailError] = useState("");
  const [fallbackTools, setFallbackTools] = useState<RouteTool[] | null>(null);
  const [ways, setWays] = useState<PublicWay[]>([]);
  const [waysState, setWaysState] = useState<"idle" | "loading" | "ok" | "error">("idle");
  const controller = useRef<AbortController | undefined>(undefined);

  async function loadWays(term: string) {
    setWaysState("loading");
    const result = await listPublicWays(term, 12);
    if (result.kind === "ok") {
      setWays(result.data);
      setWaysState("ok");
    } else {
      setWaysState("error");
    }
  }

  async function load(term: string) {
    const normalized = term.trim();
    if (!normalized) return;
    controller.current?.abort();
    const active = new AbortController();
    controller.current = active;
    setAsked(normalized);
    setState({ kind: "loading" });
    setDetails({});
    setDetailError("");
    setFallbackTools(null);
    void loadWays(normalized);
    // `/v1/goals/rank` is not registered on the server (only mentioned in comments in server.py), so the fast
    // first tier was a 404 on every search — the page reported "the router answered an error" and never found a
    // Goal. `/v1/goals/route` IS registered (app/mcp_server/room_agent_routes.py) and returns the same `goals`
    // shape plus `tools`, so it serves the first tier correctly. `include_tools=false` keeps it to the cheap
    // answer; tools load only if no Goal matches (searchTools below).
    const response = await routeWays(normalized, 5, active.signal);
    if (controller.current !== active) return;
    setState(response.kind === "ok" ? { kind: "ok", data: { query: response.data.query, goals: response.data.goals } } : response);
  }

  async function loadDetails(goalId: string) {
    setBusy(goalId);
    setDetailError("");
    const response = await routeWays(asked, 5);
    if (response.kind === "ok") {
      const goal = response.data.goals.find((g) => g.goal_id === goalId);
      if (goal) setDetails((prev) => ({ ...prev, [goalId]: goal }));
      else setDetailError("That Goal is no longer in the top results. Search again.");
    } else if (response.kind === "error") {
      setDetailError(response.message);
    }
    setBusy(null);
  }

  async function searchTools() {
    setBusy("tools");
    setDetailError("");
    const response = await routeGoals(asked, 5);
    if (response.kind === "ok") setFallbackTools(response.data.tools ?? []);
    else if (response.kind === "error") setDetailError(response.message);
    setBusy(null);
  }

  function run(event: React.FormEvent) {
    event.preventDefault();
    void load(query);
  }

  useEffect(() => {
    // /task?q=... is where the homepage and the task chips land. Load both the public Ways and the
    // Goal routing for the same term, so the page answers even when the Goal tier has no match.
    const q = new URLSearchParams(window.location.search).get("q") || "";
    if (q) {
      setQuery(q);
      void load(q);
    } else {
      void loadWays("");
    }
  }, []);
  useEffect(() => () => controller.current?.abort(), []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>TASK</b></div>
        <h1 className="display"><AnimatedHeading>Find a Way to get it done.</AnimatedHeading></h1>
        <p className="lead small">
          Search published Ways by what you want to accomplish, or route a task to a Goal. A Way is a
          reviewed procedure with its steps, evidence and a sample output: open one to read it, or buy
          a licence to run it.
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
          <button className="btn-ink" type="submit">Search <span className="sq" aria-hidden="true">→</span></button>
        </form>

        <div className="scopes">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => { setQuery(example); void load(example); }}>
              {example}
            </button>
          ))}
        </div>

        <section aria-labelledby="h-ways" style={{ marginTop: 32 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
            <h2 id="h-ways" className="h2" style={{ margin: 0 }}>Ways</h2>
            <span className="caption">{waysState === "loading" ? "searching…" : ways.length ? `${ways.length} shown` : ""}</span>
          </div>
          {waysState === "loading" && <p className="route-note" role="status">Searching the published Ways…</p>}
          {waysState === "error" && <p className="route-note">The catalogue could not be reached.</p>}
          {waysState === "ok" && ways.length === 0 && (
            <p className="route-note">
              No published Way matches {asked ? `“${asked}”` : "that"} yet. The Goal tier below may still help.
            </p>
          )}
          {ways.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16, marginTop: 16 }}>
              {ways.map((way) => {
                const price = way.price_paise && way.price_paise > 0 ? priceLabel(way.price_paise, way.price_currency) : null;
                return (
                  <Link
                    key={way.id}
                    href={`/way/${encodeURIComponent(way.id)}`}
                    className="route-goal"
                    style={{ display: "block", textDecoration: "none", color: "inherit" }}
                  >
                    <header>
                      <h2>{way.goal_name}</h2>
                      <span className="caption">{way.mode_label || way.mode}</span>
                    </header>
                    <p className="route-lead" style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                      {way.description || "A published Way."}
                    </p>
                    <div className="route-tool-meta">
                      <span>{way.step_count} step{way.step_count === 1 ? "" : "s"}</span>
                      <span>{price ?? "Free"}</span>
                      <span>{way.status === "approved" ? "Approved" : "AI approved"}</span>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </section>

        <h2 className="h2" style={{ marginTop: 48 }}>Goals</h2>

        {state.kind === "unconfigured" && (
          <p className="route-note">
            The router is not configured. Set <code>NEXT_PUBLIC_KEL_API_URL</code> to the backend&apos;s base URL
            (for example <code>https://api.example.com</code>, without <code>/v1</code>).
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
            Ranking the Goals. This is local and usually answers in well under a second.
          </p>
        )}

        {state.kind === "ok" && (
          <div className="route-results" aria-live="polite">
            {state.data.goals.length === 0 && (
              <section className="route-fallback">
                <h2>No Goal matched <span className="caption">the tool tier can still answer</span></h2>
                <p className="route-lead">
                  The Goal tier has no category for this task yet. Search the tool catalog instead;
                  it is a separate, slower path.
                </p>
                <button type="button" className="btn-line" onClick={() => void searchTools()} disabled={busy === "tools"}>
                  {busy === "tools" ? "Searching the tools…" : "Search the tools instead"}
                </button>
                {fallbackTools && fallbackTools.map((tool) => <ToolCard key={tool.card_id} tool={tool} />)}
              </section>
            )}

            {state.data.goals.map((goal) => details[goal.goal_id]
              ? <GoalBlock key={goal.goal_id} goal={details[goal.goal_id]} />
              : <RankedGoal
                  key={goal.goal_id}
                  goal={goal}
                  busy={busy === goal.goal_id}
                  onLoad={() => void loadDetails(goal.goal_id)}
                />
            )}

            {detailError && <p className="route-note">{detailError}</p>}
          </div>
        )}
      </section>
    </>
  );
}
