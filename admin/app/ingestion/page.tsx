"use client";
import { useEffect, useState } from "react";
import { AdminNotice, Hero, Stat, str } from "@/components/Admin";
import { getIndexLag, getIngestionStatus, processFailureRoutes, timeAgo, type ApiState, type IndexLag, type IngestionAutoStatus } from "@/lib/admin-api";

export default function Ingestion() {
  const [status, setStatus] = useState<ApiState<IngestionAutoStatus>>({ kind: "loading" });
  const [lag, setLag] = useState<ApiState<IndexLag>>({ kind: "loading" });
  const [routes, setRoutes] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const ac = new AbortController();
    getIngestionStatus(ac.signal).then(setStatus);
    getIndexLag(50, ac.signal).then(setLag);
    return () => ac.abort();
  }, []);

  async function runRoutes() {
    if (!window.confirm("Process the failure-route queue now? This updates procedures from recorded failures (idempotent).")) return;
    setBusy(true);
    const r = await processFailureRoutes();
    setBusy(false);
    setRoutes(r.kind === "ok" ? `Applied: ${JSON.stringify(r.data.applied)}` : r.kind === "error" ? r.message : `Not run (${r.kind}).`);
  }

  const s = status.kind === "ok" ? status.data : null;
  const sampleKeys = lag.kind === "ok" && lag.data.sample.length > 0 ? Object.keys(lag.data.sample[0]) : [];
  return (
    <>
      <Hero marker="INGESTION" title="Ingestion" lead="Is the background loop alive, what did it last do, and is the retrieval index fresh." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        {!s ? (
          <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={status} /></div>
        ) : (
          <>
            <div className="stats">
              <Stat label="Loop" value={s.enabled ? "Enabled" : "Disabled"} note={`mode ${s.mode} · every ${s.interval_seconds}s`} />
              <Stat label="Sweeps run" value={s.run_count} note={`last finished ${timeAgo(s.last_run_completed_at)}`} />
              <Stat label="Limits" value={`${s.max_sessions}/${s.promote_limit}/${s.extract_limit}/${s.job_limit}`} note="sessions / promote / extract / jobs" />
              <Stat label="Last error" value={s.last_error ? timeAgo(s.last_error_at) : "None"} note={s.last_error ?? undefined} bad={!!s.last_error} />
            </div>
            <div className="panel">
              <h2 className="h3">Last sweep result</h2>
              {s.last_result ? (
                <div className="log">
                  {Object.entries(s.last_result).map(([k, v]) => (<div key={k}><span>{k.replace(/_/g, " ")}</span><span>{str(v)}</span></div>))}
                </div>
              ) : <div className="empty"><b>No sweep has completed yet.</b></div>}
            </div>
          </>
        )}

        <div className="panel">
          <h2 className="h3">Index freshness</h2>
          {lag.kind !== "ok" ? <AdminNotice state={lag} /> : (
            <>
              <div className="stats">
                <Stat label="Current recipe" value={<span className="mono" style={{ fontSize: 18 }}>{lag.data.current_recipe}</span>} />
                <Stat label="Lagging" value={lag.data.lag_count} />
                <Stat label="Recipe drift" value={lag.data.recipe_drift_count} />
                <Stat label="Total stale" value={lag.data.total_stale} bad={lag.data.total_stale > 0} />
              </div>
              <p className="small dim">Fixing drift spends embedding-API calls, so it stays a deliberate CLI step (<code>scripts/backfill_procedure_embeddings.py</code>); this page only reports it.</p>
              {sampleKeys.length > 0 && (
                <div className="scroll-x">
                  <table className="dtable">
                    <thead><tr>{sampleKeys.map((k) => <th key={k}>{k.replace(/_/g, " ")}</th>)}</tr></thead>
                    <tbody>{lag.data.sample.map((r, i) => <tr key={i}>{sampleKeys.map((k) => <td key={k}>{str(r[k])}</td>)}</tr>)}</tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>

        <div className="panel">
          <h2 className="h3">Failure routes</h2>
          <p className="small dim">Executes the queued update for every recorded failed outcome. No model spend; safe to run twice.</p>
          <div className="row-actions">
            <button type="button" className="btn-line" disabled={busy} onClick={runRoutes}>{busy ? "Running…" : "Process failure routes"}</button>
          </div>
          {routes && <p className="notice-ok" role="status">{routes}</p>}
        </div>
      </section>
    </>
  );
}
