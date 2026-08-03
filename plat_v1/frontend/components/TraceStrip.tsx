"use client";

import { Stage } from "@/lib/api";

/**
 * One row per stage attempt: which implementation ran, what happened, what it
 * cost, how long it took. This is the only genuinely new surface plat_v1
 * needs, and it is the one that answers "why did this run cost that much".
 *
 * Failed attempts are shown, not filtered. A stage that succeeded on its
 * third implementation is a different fact from one that succeeded first
 * time, and the escalation is exactly what a reader wants to see.
 *
 * Reuses the existing metrics-table styling rather than introducing any —
 * this is a table of numbers, which is what that class is for.
 */
export default function TraceStrip({ stages }: { stages: Stage[] }) {
  if (stages.length === 0) {
    return (
      <p className="case-body" style={{ fontStyle: "italic" }}>
        No stages ran.
      </p>
    );
  }

  const totalCost = stages.reduce((sum, s) => sum + s.cost, 0);
  const totalLatency = stages.reduce((sum, s) => sum + s.latency_ms, 0);

  return (
    <>
      <table className="metrics-table">
        <thead>
          <tr>
            <th>stage</th>
            <th>implementation</th>
            <th>outcome</th>
            <th>cost</th>
            <th>latency</th>
          </tr>
        </thead>
        <tbody>
          {stages.map((stage, i) => (
            <tr key={`${stage.node_ref}-${i}`}>
              <td>
                {stage.task_name || stage.node_ref}
                {stage.attempts > 1 && (
                  <span className="ci-cell"> · attempt {stage.attempts}</span>
                )}
              </td>
              <td>
                {stage.implementation_name || "—"}
                {stage.implementation_kind && (
                  <span className="ci-cell"> ({stage.implementation_kind})</span>
                )}
                {stage.cache_hit && <span className="ci-cell"> · cached</span>}
              </td>
              <td className={stage.outcome === "success" ? "delta-better" : "delta-worse"}>
                {stage.outcome}
                {stage.error && <div className="ci-cell">{stage.error}</div>}
              </td>
              <td>{stage.cost > 0 ? `$${stage.cost.toFixed(4)}` : "—"}</td>
              <td>{stage.latency_ms} ms</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="metrics-row" style={{ marginTop: "1rem" }}>
        <div className="metric">
          <span className="metric-value">${totalCost.toFixed(4)}</span>
          <span className="metric-label">total cost</span>
        </div>
        <div className="metric">
          <span className="metric-value">{(totalLatency / 1000).toFixed(1)}s</span>
          <span className="metric-label">total latency</span>
        </div>
        <div className="metric">
          <span className="metric-value">{stages.length}</span>
          <span className="metric-label">stage attempts</span>
        </div>
      </div>
    </>
  );
}
