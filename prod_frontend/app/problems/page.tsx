"use client";
import { useEffect, useState } from "react";
import { apiGet, labelOf, type ApiState } from "@/lib/api";
import StateNotice from "@/components/NotConnected";
import StatusLabel from "@/components/StatusLabel";

type Row = Record<string, unknown>;

const facets = [
  ["Goals", "The family’s shared aim, stated once."],
  ["Procedures", "Known methods for reaching it."],
  ["Implementations", "The tools and systems that carry them out."],
  ["Benchmarks", "Defined tests to compare candidates."],
  ["Runs", "What actually happened when people tried."],
];

export default function ProblemsPage() {
  const [state, setState] = useState<ApiState<Row[]>>({ kind: "loading" });

  useEffect(() => {
    const ac = new AbortController();
    apiGet<{ problems: Row[] }>("/v1/problems?limit=100", ac.signal).then((r) =>
      setState(r.kind === "ok" ? { kind: "ok", data: r.data.problems ?? [] } : (r as ApiState<Row[]>)),
    );
    return () => ac.abort();
  }, []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>PROBLEMS</b></div>
        <h1 className="display">A family of goals.</h1>
        <p className="lead">A collection of known ways. A place to see what has actually worked.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        <div className="family">
          {facets.map(([t, d], i) => (
            <div className="cell" key={t}><div className="n"><span>{String(i + 1).padStart(2, "0")}</span></div><div><h3 className="h3">{t}</h3><p>{d}</p></div></div>
          ))}
        </div>

        {state.kind === "ok" && state.data.length > 0 ? (
          <ul className="list" aria-label="Problems">
            {state.data.map((p, i) => (
              <li key={String(p.id ?? i)}>
                <span className="n">{String(i + 1).padStart(3, "0")}</span>
                <h3>{labelOf(p)}</h3>
                {typeof p.status === "string" && ["candidate", "verified", "unknown"].includes(p.status) ? <StatusLabel s={p.status as "candidate"} /> : <span className="caption dim">{typeof p.status === "string" ? p.status : ""}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <StateNotice state={state} empty={state.kind === "ok" ? "No problems are recorded yet." : undefined} />
        )}
      </section>
    </>
  );
}
