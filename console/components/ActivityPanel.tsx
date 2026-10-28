"use client";
import { useEffect, useRef, useState } from "react";
import { formatMs } from "@/lib/format";
import type { HelperClient } from "@/lib/helper";
import type { ActivityEvent } from "@/lib/types";
import { HelperError } from "@/lib/words";

const KEEP = 100;
const RETRY_MS = 3000;

/** Adds one event, newest first, dropping a repeat (a reconnect replays what was already shown) and anything past KEEP. */
export function addEvent(list: ActivityEvent[], e: ActivityEvent): ActivityEvent[] {
  if (list.some((x) => x.id === e.id)) return list;
  return [e, ...list].slice(0, KEEP);
}

/** The one-line summary of a call: the arguments that say what it was about. */
export function summary(e: ActivityEvent): string {
  const a = e.args;
  const text = a.query ?? a.prompt ?? a.name ?? Object.values(a)[0] ?? "";
  return text.replace(/\s+/g, " ").slice(0, 140);
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

  useEffect(() => {
    if (paused) return;
    const ac = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      setLink({ state: "connecting" });
      try {
        await client.activity(last.current, (e) => { last.current = Math.max(last.current, e.id); setEvents((p) => addEvent(p, e)); }, ac.signal, () => setLink({ state: "live" }));
        if (!ac.signal.aborted) setLink({ state: "down", message: "The link to the MCP closed." });
      } catch (err) {
        if (!ac.signal.aborted) setLink({ state: "down", message: err instanceof HelperError ? err.message : "The feed could not be read." });
      }
      if (!ac.signal.aborted) timer = window.setTimeout(run, retryMs);
    };
    void run();
    return () => { ac.abort(); window.clearTimeout(timer); };
  }, [client, paused, retryMs]);

  const status = link.state === "down" ? link.message : link.state === "live" ? "Listening" : "Connecting…";

  return (
    <div className="panel" role="tabpanel" aria-labelledby="tab-activity" id="panel-activity">
      <p className="notice">
        <b style={{ fontWeight: 400 }}>Router activity.</b> What kell&rsquo;s tools returned to your agent, as it happens: <code>find_ways</code>, <code>call_model</code>, <code>report_result</code> and <code>use_tool</code>. It is kept in the MCP&rsquo;s memory only, and only the last {KEEP} calls show.
      </p>
      <div className="cluster" role="status" aria-live="polite">
        <span className="status" data-s={link.state === "down" ? "failed" : paused ? "unknown" : "successful"}>{paused ? "Paused" : status}</span>
        <button type="button" className="btn-line" onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
        <button type="button" className="btn-line" onClick={() => setEvents([])} disabled={events.length === 0}>Clear</button>
      </div>
      {events.length === 0 ? (
        <div className="empty"><b>Nothing yet.</b><p>Ask your agent to do a task. Each lookup and model call it makes through kell will appear here.</p></div>
      ) : (
        <div className="stack" aria-label="Calls, newest first">
          {events.map((e) => (
            <article className="pane" key={e.id} data-status={e.ok ? "done" : "error"}>
              <header>
                <span>{e.tool}</span>
                <span className="status" data-s={e.ok ? "executed" : "failed"}>{e.ok ? "returned" : "failed"}</span>
              </header>
              <p className="small dim" style={{ margin: "0 0 8px" }}>{summary(e) || "(no arguments)"}</p>
              <details>
                <summary>Output{e.truncated ? " (cut short)" : ""}</summary>
                <div className="out" role="log" aria-label={`Output of ${e.tool}`}>{e.output || "(empty)"}</div>
              </details>
              <footer><span className="dim">{new Date(e.ts * 1000).toLocaleTimeString()}</span><span>{formatMs(e.ms)}</span></footer>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
