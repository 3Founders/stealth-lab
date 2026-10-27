"use client";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { formatCost, formatMs, formatTokens } from "@/lib/format";
import type { HelperClient } from "@/lib/helper";
import type { ChatEvent, ModelInfo } from "@/lib/types";
import { HelperError } from "@/lib/words";

const MAX_COMPARE = 3;

export interface PaneState {
  text: string;
  status: "waiting" | "streaming" | "done" | "error" | "stopped";
  error?: string;
  ttft_ms?: number | null;
  total_ms?: number;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | string | null;
}

/** Folds one stream event into the panes. Pure, so the streaming behaviour is testable without a browser. */
export function applyEvent(panes: Record<string, PaneState>, e: ChatEvent): Record<string, PaneState> {
  const cur = panes[e.model] ?? { text: "", status: "waiting" as const };
  if ("delta" in e) return { ...panes, [e.model]: { ...cur, text: cur.text + e.delta, status: "streaming" } };
  if ("error" in e) return { ...panes, [e.model]: { ...cur, status: "error", error: e.error } };
  return { ...panes, [e.model]: { ...cur, status: "done", ttft_ms: e.ttft_ms, total_ms: e.total_ms, tokens_in: e.tokens_in, tokens_out: e.tokens_out, cost_usd: e.cost_usd } };
}

export default function PlaygroundPanel({ client }: { client: HelperClient }) {
  const uid = useId();
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [panes, setPanes] = useState<Record<string, PaneState>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    let live = true;
    client.models().then((m) => { if (live) { setModels(m); setPicked(m[0] ? [m[0].name] : []); } })
      .catch((e) => { if (live) { setLoadError(e instanceof HelperError ? e.message : "The model list could not be read."); setModels([]); } });
    return () => { live = false; abort.current?.abort(); };
  }, [client]);

  const toggle = (name: string) =>
    setPicked((p) => (p.includes(name) ? p.filter((x) => x !== name) : p.length >= MAX_COMPARE ? p : [...p, name]));

  const send = useCallback(async () => {
    const text = prompt.trim();
    if (!text || picked.length === 0 || running) return;
    const ac = new AbortController();
    abort.current = ac;
    setRunning(true); setError(null);
    setPanes(Object.fromEntries(picked.map((n) => [n, { text: "", status: "waiting" } as PaneState])));
    try {
      await client.chat(picked, [{ role: "user", content: text }], (e) => setPanes((p) => applyEvent(p, e)), ac.signal);
    } catch (e) {
      setError(e instanceof HelperError ? e.message : "The request failed.");
    } finally {
      // Anything still open when the request ends was stopped (by Stop) or cut short; say so rather than leave it spinning.
      setPanes((p) => Object.fromEntries(Object.entries(p).map(([k, v]) => [k, v.status === "waiting" || v.status === "streaming" ? { ...v, status: "stopped" as const } : v])));
      setRunning(false);
      abort.current = null;
    }
  }, [client, picked, prompt, running]);

  const stop = () => abort.current?.abort();

  return (
    <div className="panel" role="tabpanel" aria-labelledby="tab-playground" id="panel-playground">
      <p className="notice">
        <b style={{ fontWeight: 400 }}>Playground.</b> A reply here is not checked and not verified. Your prompt goes from this machine straight to the provider of each model you pick, with your key; it does not go through the kell server.
      </p>

      <section className="card" aria-labelledby={`${uid}-h`}>
        <h2 id={`${uid}-h`}>Try a model</h2>
        {loadError && <p className="notice" role="alert" data-tone="bad">{loadError}</p>}
        {models === null && <p className="small dim">Loading…</p>}
        {models && models.length === 0 && !loadError && (
          <div className="empty"><b>No models to try.</b><p>Add one under Keys &amp; models first.</p></div>
        )}
        {models && models.length > 0 && (
          <form className="stack" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="label" style={{ fontSize: 14, fontWeight: 400, marginBottom: 6 }}>Models (pick one, or up to {MAX_COMPARE} to compare side by side)</legend>
              <div className="picker">
                {models.map((m) => (
                  <label key={m.name}>
                    <input type="checkbox" checked={picked.includes(m.name)} onChange={() => toggle(m.name)} disabled={running || (!picked.includes(m.name) && picked.length >= MAX_COMPARE)} />
                    {m.name}
                    {!m.local && !m.key_set && <span className="status" data-s="failed">no key</span>}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="field">
              <label htmlFor={`${uid}-p`}>Prompt</label>
              <textarea id={`${uid}-p`} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void send(); } }} />
              <span className="hint">Ctrl+Enter (or Cmd+Enter) sends.</span>
            </div>
            <div className="cluster">
              <button type="submit" className="btn-ink" disabled={running || !prompt.trim() || picked.length === 0}>{running ? "Streaming…" : "Send"}</button>
              <button type="button" className="btn-line" onClick={stop} disabled={!running}>Stop</button>
            </div>
            {error && <p className="notice" role="alert" data-tone="bad">{error}</p>}
          </form>
        )}
      </section>

      {Object.keys(panes).length > 0 && (
        <div className="compare" aria-label="Replies">
          {Object.entries(panes).map(([name, p]) => (
            <article className="pane" key={name} data-model={name} data-status={p.status} aria-busy={p.status === "streaming" || p.status === "waiting"}>
              <header>
                <span>{name}</span>
                <span className="status" data-s={p.status === "done" ? "executed" : p.status === "error" ? "failed" : "unknown"}>
                  {p.status === "waiting" ? "waiting" : p.status === "streaming" ? "streaming" : p.status === "done" ? "finished" : p.status === "stopped" ? "stopped" : "error"}
                </span>
              </header>
              <div className="out" role="log" aria-live="polite" aria-label={`Reply from ${name}`} data-empty={p.text === ""}>
                {p.status === "error" ? <span role="alert">{p.error}</span> : p.text || (p.status === "waiting" ? "Waiting for the first words…" : "")}
              </div>
              <footer>
                {p.status === "done" ? (
                  <>
                    <span>First token {formatMs(p.ttft_ms)}</span>
                    <span>Total {formatMs(p.total_ms)}</span>
                    <span>{formatTokens(p.tokens_in)} in / {formatTokens(p.tokens_out)} out</span>
                    <span>{formatCost(p.cost_usd)}</span>
                  </>
                ) : (
                  <span className="dim">{p.status === "stopped" ? "Stopped before it finished." : p.status === "error" ? "No reply." : "Numbers appear when it finishes."}</span>
                )}
              </footer>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
