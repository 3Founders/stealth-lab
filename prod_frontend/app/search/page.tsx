"use client";
import { useRef, useState } from "react";
import { apiGet, labelOf, type ApiState } from "@/lib/api";
import StateNotice from "@/components/NotConnected";
import { bucket, track } from "@/lib/analytics";

type Row = Record<string, unknown>;
type Groups = { name: string; rows: Row[] }[];

// What the backend can search today: /v1/search covers procedures, tasks and claims; /v1/problems/find covers problems.
const scopes = ["Problems", "Procedures", "Claims", "Tasks"];

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [state, setState] = useState<ApiState<Groups>>({ kind: "idle" });
  const ac = useRef<AbortController | undefined>(undefined);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const term = q.trim();
    if (!term) return;
    ac.current?.abort();
    ac.current = new AbortController();
    setAsked(term); setState({ kind: "loading" });
    track("search_submit");                                   // never the query text
    const enc = encodeURIComponent(term);
    const [s, p] = await Promise.all([
      apiGet<{ results: Record<string, Row[]> }>(`/v1/search?q=${enc}&limit=10`, ac.current.signal),
      apiGet<{ problems: Row[] }>(`/v1/problems/find?q=${enc}&limit=10`, ac.current.signal),
    ]);
    if (s.kind === "unconfigured") return setState(s);
    if (s.kind === "error" && p.kind === "error") return setState(s);
    const groups: Groups = [];
    if (p.kind === "ok") groups.push({ name: "problems", rows: p.data.problems ?? [] });
    if (s.kind === "ok") for (const [name, rows] of Object.entries(s.data.results ?? {})) groups.push({ name, rows });
    setState({ kind: "ok", data: groups });
    track("search_result", { results: bucket(groups.reduce((n, g) => n + g.rows.length, 0)) });
  }

  const total = state.kind === "ok" ? state.data.reduce((n, g) => n + g.rows.length, 0) : 0;

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SEARCH</b></div>
        <h1 className="display">What are you trying to get done?</h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <form className="ask" onSubmit={run} role="search">
          <label htmlFor="goal" className="skip">Describe a goal</label>
          <input id="goal" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. deploy a service to staging" autoComplete="off" />
          <button className="btn-ink" type="submit">Find ways <span className="sq" aria-hidden="true">→</span></button>
        </form>
        <div className="scopes" aria-label="Searchable today">{scopes.map((s) => <span key={s}>{s}</span>)}</div>
        <p className="small dim" style={{ gridColumn: "1 / -1", marginTop: 12 }}>Searches what the connected backend can search today. Evidence and runs are reached through the procedures and problems they belong to.</p>

        <div className="hits" aria-live="polite">
          {state.kind === "idle" && null}
          {state.kind === "ok" && total > 0 && state.data.filter((g) => g.rows.length).map((g) => (
            <div key={g.name} style={{ marginBottom: 32 }}>
              <div className="caption dim" style={{ marginBottom: 8 }}>{g.name} · {g.rows.length}</div>
              <ul className="list" style={{ gridColumn: "auto" }}>
                {g.rows.map((r, i) => (<li key={String(r.id ?? i)}><span className="n">{String(i + 1).padStart(2, "0")}</span><h3>{labelOf(r)}</h3><span className="caption dim">{typeof r.status === "string" ? r.status : ""}</span></li>))}
              </ul>
            </div>
          ))}
          {state.kind !== "idle" && !(state.kind === "ok" && total > 0) && (
            <StateNotice state={state} empty={state.kind === "ok" ? `No recorded ways found for “${asked}”.` : undefined} />
          )}
        </div>
      </section>
    </>
  );
}
