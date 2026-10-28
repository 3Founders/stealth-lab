"use client";
import { useEffect, useRef, useState } from "react";
import { formatMs } from "@/lib/format";
import type { HelperClient } from "@/lib/helper";
import type { ActivityEvent, RunSnapshot } from "@/lib/types";
import { HelperError } from "@/lib/words";

const KEEP = 100;
const RETRY_MS = 3000;

/**
 * Adds one event, newest first, and anything past KEEP is dropped. A call arrives twice, when it starts and when it
 * returns: the later one replaces the earlier in place, and a replay of what is already shown changes nothing.
 */
export function addEvent(list: ActivityEvent[], e: ActivityEvent): ActivityEvent[] {
  const i = list.findIndex((x) => x.id === e.id);
  if (i < 0) return [e, ...list].slice(0, KEEP);
  if (e.seq <= list[i].seq) return list;
  return list.map((x, j) => (j === i ? e : x));
}

/** The one-line summary of a call: the arguments that say what it was about. */
export function summary(e: ActivityEvent): string {
  const a = e.args;
  const text = a.query ?? a.prompt ?? a.name ?? Object.values(a)[0] ?? "";
  return text.replace(/\s+/g, " ").slice(0, 140);
}

/** Replaces a run's earlier snapshot (the helper re-sends a run as it changes), newest run first, capped. */
export function upsertRun(list: RunSnapshot[], r: RunSnapshot): RunSnapshot[] {
  return [r, ...list.filter((x) => x.run_id !== r.run_id)].sort((a, b) => (a.run_id < b.run_id ? 1 : -1)).slice(0, 30);
}

type Link = { state: "connecting" } | { state: "live" } | { state: "down"; message: string };

/**
 * The router's work as it happens: every find_ways / call_model / report_result / use_tool call the MCP handles, with
 * the exact text it returned to the agent. It reconnects on its own and asks only for what it has not seen.
 */
export default function ActivityPanel({ client, retryMs = RETRY_MS }: { client: HelperClient; retryMs?: number }) {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [link, setLink] = useState<Link>({ state: "connecting" });
  const [paused, setPaused] = useState(false);
  const last = useRef(0);
  const [runs, setRuns] = useState<RunSnapshot[]>([]);

  useEffect(() => {
    if (paused) return;
    const ac = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      setLink({ state: "connecting" });
      try {
        await client.activity(last.current, (e) => { last.current = Math.max(last.current, e.seq); setEvents((p) => addEvent(p, e)); }, ac.signal, () => setLink({ state: "live" }));
        if (!ac.signal.aborted) setLink({ state: "down", message: "The link to the MCP closed." });
      } catch (err) {
        if (!ac.signal.aborted) setLink({ state: "down", message: err instanceof HelperError ? err.message : "The feed could not be read." });
      }
      if (!ac.signal.aborted) timer = window.setTimeout(run, retryMs);
    };
    void run();
    return () => { ac.abort(); window.clearTimeout(timer); };
  }, [client, paused, retryMs]);

  // Runs started by the hooks or the CLI never pass through the MCP; their output is read from disk by the helper.
  useEffect(() => {
    if (paused) return;
    const ac = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      try { await client.runs((r) => setRuns((p) => upsertRun(p, r)), ac.signal); } catch { /* the calls feed above already says when the helper is unreachable */ }
      if (!ac.signal.aborted) timer = window.setTimeout(run, retryMs);
    };
    void run();
    return () => { ac.abort(); window.clearTimeout(timer); };
  }, [client, paused, retryMs]);

  const status = link.state === "down" ? link.message : link.state === "live" ? "Listening" : "Connecting…";

  return (
    <div className="panel" role="tabpanel" aria-labelledby="tab-activity" id="panel-activity">
      <p className="notice">
        <b style={{ fontWeight: 400 }}>Router activity.</b> What kell&rsquo;s tools returned to your agent, as it happens: <code>find_ways</code>, <code>call_model</code>, <code>report_result</code> and <code>use_tool</code>. That feed is kept in the MCP&rsquo;s memory only, and only the last {KEEP} calls show. Runs your hooks or the CLI start (the cheaper-model attempts) are read from <code>~/.stealthlab/runs</code> and shown with each model&rsquo;s output as it is written.
      </p>
      <div className="cluster" role="status" aria-live="polite">
        <span className="status" data-s={link.state === "down" ? "failed" : paused ? "unknown" : "successful"}>{paused ? "Paused" : status}</span>
        <button type="button" className="btn-line" onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
        <button type="button" className="btn-line" onClick={() => { setEvents([]); setRuns([]); }} disabled={events.length === 0 && runs.length === 0}>Clear</button>
      </div>
      {runs.length > 0 && (
        <section className="stack" aria-label="Runs from your hooks and the CLI, newest first">
          <h2 className="small dim" style={{ fontWeight: 400 }}>Runs started by your hooks or the CLI</h2>
          {runs.map((r) => (
            <article className="pane" key={r.run_id} data-status={r.state === "running" || r.state === "queued" ? "running" : r.state === "verified" ? "done" : "error"}>
              <header><span>{r.run_id}</span><span className="status" data-s={r.state === "verified" ? "verified" : r.state === "running" || r.state === "queued" ? "executed" : "unknown"}>{r.state}</span></header>
              <p className="small dim" style={{ margin: "0 0 8px" }}>{r.task || "(no task text)"}</p>
              {r.attempts.length === 0 && <p className="small dim">Waiting for a model to start.</p>}
              {r.attempts.map((a) => (
                <details key={a.n} open={a.state === "running"}>
                  <summary>{a.model ?? a.executor ?? "model"} &middot; attempt {a.n} &middot; {a.state}{a.verified === true ? " (checks passed)" : ""}{a.duration_s != null ? ` · ${Math.round(a.duration_s)}s` : ""}</summary>
                  <div className="out" role="log" aria-label={`Output of ${a.model ?? "model"}, attempt ${a.n}`}>{a.output || "(no output yet)"}</div>
                  {a.files && a.files.length > 0 && <p className="small dim">Changed: {a.files.join(", ")}</p>}
                </details>
              ))}
            </article>
          ))}
        </section>
      )}
      {events.length === 0 ? (
        <div className="empty"><b>Nothing yet.</b><p>Ask your agent to do a task. Each lookup and model call it makes through kell will appear here.</p></div>
      ) : (
        <div className="stack" aria-label="Calls, newest first">
          {events.map((e) => (
            <article className="pane" key={e.id} data-status={e.pending ? "running" : e.ok ? "done" : "error"}>
              <header>
                <span>{e.tool}</span>
                <span className="status" data-s={e.pending ? "executed" : e.ok ? "successful" : "failed"}>{e.pending ? "running" : e.ok ? "returned" : "failed"}</span>
              </header>
              <p className="small dim" style={{ margin: "0 0 8px" }}>{summary(e) || "(no arguments)"}</p>
              <details open={e.pending}>
                <summary>Output{e.truncated ? " (cut short)" : ""}</summary>
                <div className="out" role="log" aria-label={`Output of ${e.tool}`}>{e.pending ? "(waiting for the result)" : e.output || "(empty)"}</div>
              </details>
              <footer><span className="dim">{new Date(e.ts * 1000).toLocaleTimeString()}</span><span>{e.pending ? "…" : formatMs(e.ms)}</span></footer>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
