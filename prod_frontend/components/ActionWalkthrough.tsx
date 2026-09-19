"use client";
import { useState } from "react";
import StatusLabel, { type Status } from "./StatusLabel";

type Route = { id: string; procedure: string; impl: string; status: Status; out?: string };
type Step = { label: string; system: string; routes?: "all" | "narrowed" | "picked"; log: [string, string][] };

const routes: Route[] = [
  { id: "R1", procedure: "Procedure A", impl: "Implementation 1", status: "candidate" },
  { id: "R2", procedure: "Procedure A", impl: "Implementation 2", status: "candidate", out: "needs a credential the request does not grant" },
  { id: "R3", procedure: "Procedure B", impl: "Implementation 3", status: "observed" },
];

const steps: Step[] = [
  { label: "Understand the goal", system: "Reads the request as a Goal: deploy a named service to a named environment. Anything missing is asked for — not guessed.", log: [["goal", "deploy X → environment Y"], ["unknown", "current version of X in Y"]] },
  { label: "Find known ways", system: "Looks up Procedures that have been recorded for this kind of Goal, and the Implementations that can carry them out.", routes: "all", log: [["found", "2 procedures, 3 implementations"], ["note", "found ≠ verified"]] },
  { label: "Compare routes", system: "Builds candidate Routes — Goal → Procedure → Implementation — and checks each against the constraints of this request.", routes: "all", log: [["check", "applicability, constraints, evidence, cost"], ["result", "R2 not applicable here"]] },
  { label: "Select", system: "Picks a Route and says why. The person can override it.", routes: "picked", log: [["select", "R3 — strongest evidence for this context"], ["human", "route override available"]] },
  { label: "Estimate", system: "States what it expects the Run to cost and touch before it starts. An estimate, not a promise.", routes: "picked", log: [["estimate", "steps, tools, approximate cost"], ["human", "approval requested"]] },
  { label: "Execute", system: "Runs the chosen Route. A human is pulled in for credentials, approvals, or missing information.", routes: "picked", log: [["run", "started"], ["event", "node 1 … node n"]] },
  { label: "Observe", system: "Records what actually happened: events, artifacts, tool calls, outputs.", routes: "picked", log: [["event", "recorded as it happens"], ["artifact", "stored with the Run"]] },
  { label: "Verify", system: "Checks the outcome against the Goal, independently of the Run's own claim of success.", routes: "picked", log: [["verify", "is Y actually running X?"], ["status", "verified | failed | unknown"]] },
  { label: "Record the outcome", system: "The Run is kept as a Run. A failure stays a failure — it becomes evidence, never a Procedure.", routes: "picked", log: [["run", "closed with its outcome"], ["rule", "failed ≠ silently successful"]] },
  { label: "Update knowledge", system: "Evidence attaches to the Procedure and Implementation it concerns. Confidence moves only as far as the evidence allows.", routes: "picked", log: [["evidence", "attached"], ["reuse", "available to the next request"]] },
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
        <div className="bubble"><small>Request</small>Deploy X to environment Y.</div>
        <div><small className="caption dim">keळ · step {i + 1}</small><p style={{ fontSize: 19, marginTop: 6, maxWidth: "34em" }}>{s.system}</p></div>
        {showRoutes && (
          <div className="routes" role="list">
            {routes.map((r) => {
              const out = i >= 2 && !!r.out;
              const picked = (s.routes === "picked") && r.id === "R3";
              return (
                <div className="route" role="listitem" key={r.id} data-out={out} data-picked={picked}>
                  <span className="id">{r.id}</span>
                  <span>{r.procedure} → {r.impl}<small>{out ? r.out : "candidate route"}</small></span>
                  <StatusLabel s={picked && i >= 8 ? "evidenced" : r.status} />
                </div>
              );
            })}
          </div>
        )}
        <div className="log">{s.log.map(([k, v]) => (<div key={k + v}><span>{k}</span><span>{v}</span></div>))}</div>
        <p className="action-note">Illustrative walkthrough of the model. Not a recorded Run; routes shown are placeholders.</p>
      </div>
    </div>
  );
}
