"use client";
import { useState } from "react";
import StatusLabel, { type Status } from "./StatusLabel";

// `out` marks a route as disqualified once compared (struck through) -- a hard constraint
// violation, not just "not chosen." `note` is a softer, always-visible bit of context for a
// route that's still a real candidate, just not the best fit (no strikethrough).
type Route = { id: string; procedure: string; impl: string; status: Status; out?: string; note?: string };
type Step = { label: string; system: string; routes?: "all" | "narrowed" | "picked"; log: [string, string][] };

const routes: Route[] = [
  { id: "A", procedure: "Direct kubectl redeploy", impl: "staging cluster", status: "candidate", note: "would work, but skips the team's existing CI/CD path" },
  { id: "B", procedure: "Full infra script", impl: "includes a DB migration step", status: "candidate", out: "changes database configuration, not allowed here" },
  { id: "C", procedure: "GitHub Actions → Helm pipeline", impl: "existing deploy path", status: "observed" },
];

const steps: Step[] = [
  { label: "Understand the goal", system: "Reads the request as a Goal: deploy the service to staging, without touching the existing database configuration.", log: [["goal", "deploy service → staging"], ["repo", "payments-service"], ["environment", "staging (existing)"]] },
  { label: "Read the context", system: "Looks at the environment this Goal has to run in: what's already there, and what's available to use.", log: [["runtime", "Node 20, existing Helm chart"], ["deploy method", "GitHub Actions → Helm"], ["available", "kubectl, Helm, CI credentials"]] },
  { label: "Note the constraints", system: "Constraints narrow which ways can even apply here, not just which is preferred.", log: [["must not", "change database configuration"], ["must", "use the existing deployment method"], ["access", "no direct production access"]] },
  { label: "Find known ways", system: "Looks up ways that have been recorded for this kind of goal, and what each one needs to run.", routes: "all", log: [["found", "3 candidate ways"], ["note", "found ≠ applicable"]] },
  { label: "Compare routes", system: "Checks each candidate against this goal's exact context and constraints, not just the goal in general.", routes: "all", log: [["check", "context, constraints, evidence"], ["result", "Route B touches database config: ruled out"]] },
  { label: "Select", system: "Picks a route and says why. The choice can be overridden.", routes: "picked", log: [["select", "Route C: fits the constraints, matches the environment"], ["human", "override available"]] },
  { label: "Run", system: "Puts the selected route into practice. A person is pulled in only where a decision is theirs.", routes: "picked", log: [["run", "started"], ["step", "1 of 4 … 4 of 4"]] },
  { label: "Observe", system: "Records what actually happened: events, artifacts, tool calls, outputs.", routes: "picked", log: [["event", "deploy triggered → rollout → health check"], ["artifact", "deployment log, rollout status"]] },
  { label: "Verify", system: "Checks the outcome against the goal itself, not against what the run reports about itself.", routes: "picked", log: [["verify", "is the service actually running in staging?"], ["database config", "unchanged, as required"]] },
  { label: "Update what's known", system: "The outcome attaches as evidence to the way that was used. Confidence moves only as far as the evidence allows.", routes: "picked", log: [["evidence", "attached to this way, for this kind of context"], ["reuse", "available next time this goal comes up"]] },
];

export default function ActionWalkthrough() {
  const [i, setI] = useState(0);
  const s = steps[i];
  const showRoutes = !!s.routes;
  return (
    <div className="action">
      <ol className="action-steps" aria-label="Steps">
        {steps.map((st, k) => (
          <li key={st.label}>
            <button aria-current={k === i} onClick={() => setI(k)}>
              <span>{String(k + 1).padStart(2, "0")}</span>{st.label}
            </button>
          </li>
        ))}
      </ol>
      <div className="action-view" aria-live="polite">
        <div className="bubble"><small>Goal</small>Deploy this service to our staging environment without changing the existing database configuration.</div>
        <div><small className="caption dim">keळ · step {i + 1}</small><p style={{ fontSize: 19, marginTop: 6, maxWidth: "34em" }}>{s.system}</p></div>
        {showRoutes && (
          <div className="routes" role="list">
            {routes.map((r) => {
              const out = i >= 4 && !!r.out;
              const picked = (s.routes === "picked") && r.id === "C";
              return (
                <div className="route" role="listitem" key={r.id} data-out={out} data-picked={picked}>
                  <span className="id">{r.id}</span>
                  <span>{r.procedure} → {r.impl}<small>{out ? r.out : (r.note ?? "candidate route")}</small></span>
                  <StatusLabel s={picked && i >= 9 ? "evidenced" : r.status} />
                </div>
              );
            })}
          </div>
        )}
        <div className="log">{s.log.map(([k, v]) => (<div key={k + v}><span>{k}</span><span>{v}</span></div>))}</div>
        <p className="action-note">Illustrative example with mock data, not a live run. Routes and outcomes shown are for demonstration.</p>
      </div>
    </div>
  );
}
