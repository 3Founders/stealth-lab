"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { apiGet, labelOf, type ApiState } from "@/lib/api";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { bucket, track } from "@/lib/analytics";

type Row = Record<string, unknown>;
type Groups = { name: string; rows: Row[] }[];

type Scope = "goals" | "claims";
const scopes: { key: Scope; label: string }[] = [
  { key: "goals", label: "Goals" },
  { key: "claims", label: "Claims" },
];

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [state, setState] = useState<ApiState<Groups>>({ kind: "idle" });
  const [scope, setScope] = useState<Scope>("goals");
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
    const [s, g] = await Promise.all([
      apiGet<{ results: Record<string, Row[]> }>(`/v1/search?q=${enc}&limit=10`, ac.current.signal),
      apiGet<{ goals: Row[] }>(`/v1/goals/find?q=${enc}&limit=10`, ac.current.signal),
    ]);
    if (s.kind === "unconfigured") return setState(s);
    if (s.kind === "error" && g.kind === "error") return setState(s);
    const groups: Groups = [];
    if (g.kind === "ok") groups.push({ name: "goals", rows: g.data.goals ?? [] });
    if (s.kind === "ok") for (const [name, rows] of Object.entries(s.data.results ?? {})) groups.push({ name, rows });
    setState({ kind: "ok", data: groups });
    track("search_result", { results: bucket(groups.reduce((n, g) => n + g.rows.length, 0)) });
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>SEARCH</b></div>
        <h1 className="display"><AnimatedHeading>What are you trying to get done?</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <form className="ask" onSubmit={run} role="search">
          <label htmlFor="goal" className="skip">Describe a goal</label>
          <input id="goal" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. deploy a service to staging" autoComplete="off" />
          <button className="btn-ink" type="submit">Find ways <span className="sq" aria-hidden="true">→</span></button>
        </form>
        <div className="scopes" aria-label="Search in" role="tablist">
          {scopes.map((s) => (
            <button key={s.key} type="button" role="tab" aria-pressed={scope === s.key} onClick={() => setScope(s.key)}>{s.label}</button>
          ))}
        </div>
        <div className="hits" aria-live="polite">
          {scope === "claims" ? (
            <div className="empty"><p>Claims search is coming soon.</p></div>
          ) : (
            <>
              {state.kind === "idle" && null}
              {state.kind === "ok" && state.data.some((g) => g.name === "goals" && g.rows.length > 0) && (
                <div style={{ marginBottom: 32 }}>
                  {state.data.filter((g) => g.name === "goals").map((g) => (
                    <div key={g.name}>
                      <div className="caption dim" style={{ marginBottom: 8 }}>{g.name} · {g.rows.length}</div>
                      <ul className="list" style={{ gridColumn: "auto" }}>
                        {g.rows.map((r, i) => (
                          <li key={String(r.id ?? i)}>
                            <Link href={`/goals/${r.id}`}>
                              <span className="n">{String(i + 1).padStart(2, "0")}</span>
                              <h3>{labelOf(r)}</h3>
                              <span className="caption dim">{typeof r.status === "string" ? r.status : ""}</span>
                            </Link>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
              {state.kind !== "idle" && !(state.kind === "ok" && state.data.some((g) => g.name === "goals" && g.rows.length > 0)) && (
                <StateNotice state={state} empty={state.kind === "ok" ? `No recorded goals found for “${asked}”.` : undefined} />
              )}
            </>
          )}
        </div>
      </section>
    </>
  );
}
