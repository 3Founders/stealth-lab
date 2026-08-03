"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, RunDetail, RunListItem } from "@/lib/api";
import TraceStrip from "@/components/TraceStrip";

/**
 * Run history, with the per-stage trace expanded inline.
 *
 * Inline rather than on a `/archive/[id]` route: the trace strip is the only
 * new surface plat_v1 needs, and it belongs inside the run view rather than
 * behind another navigation step.
 */
export default function RunsPage() {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);

  const load = useCallback(async () => {
    try {
      setRuns(await api.listRuns());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the API.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function toggle(id: string) {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    try {
      setDetail(await api.getRun(id));
    } catch {
      setDetail(null);
    }
  }

  return (
    <main className="shell">
      <div className="masthead">
        <div>
          <div className="masthead-title">Runs</div>
          <div className="masthead-sub">what executed, on what, and what it cost</div>
        </div>
      </div>

      <div className="nav-tabs">
        <Link href="/workbench" className="nav-tab">
          Workbench
        </Link>
        <Link href="/approvals" className="nav-tab">
          Proposals
        </Link>
        <Link href="/archive" className="nav-tab active">
          Runs
        </Link>
      </div>

      {error && (
        <div className="empty-state">
          Could not reach the API at the configured address.
          <br />
          Check NEXT_PUBLIC_API_BASE_URL and that the backend is running.
        </div>
      )}

      {!error && runs === null && <p style={{ color: "var(--ink-text-dim)" }}>Loading…</p>}

      {!error && runs?.length === 0 && (
        <div className="empty-state">Nothing has run yet.</div>
      )}

      {runs?.map((run) => (
        <div key={run.id}>
          <button
            onClick={() => toggle(run.id)}
            className={`docket-item ${run.status === "succeeded" ? "clean" : "flagged"}`}
            style={{
              width: "100%",
              textAlign: "left",
              background: "none",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            <div className="docket-eyebrow">
              {run.status} · {new Date(run.created_at).toLocaleString()}
            </div>
            <p className="docket-summary">{run.request_text}</p>
            {/* Requirement 3: cost and latency totals on the run view. */}
            <div className="docket-meta">
              <span>${run.total_cost.toFixed(4)}</span>
              <span>{(run.total_latency_ms / 1000).toFixed(1)}s</span>
              <span>
                {run.stage_count} stage attempt{run.stage_count === 1 ? "" : "s"}
              </span>
            </div>
          </button>

          {openId === run.id && (
            <div className="case-file" style={{ marginTop: "-0.5rem" }}>
              {detail === null ? (
                <p style={{ color: "var(--ink-text-dim)" }}>Loading trace…</p>
              ) : (
                <>
                  {detail.error && <div className="objection">{detail.error}</div>}

                  <div className="case-section">
                    <div className="case-label">Per-stage trace</div>
                    <TraceStrip stages={detail.stages} />
                  </div>

                  {Object.keys(detail.outputs).length > 0 && (
                    <div className="case-section">
                      <div className="case-label">Outputs</div>
                      <pre className="change-op">
                        {JSON.stringify(detail.outputs, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </main>
  );
}
