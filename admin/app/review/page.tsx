"use client";
import { useEffect, useState } from "react";
import { AdminNotice, Hero, str } from "@/components/Admin";
import { listSubmissions, reviewSubmission, type ApiState, type Row, type SubmissionKind, type SubmissionStatus } from "@/lib/admin-api";

const statuses: { key: SubmissionStatus; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "needs_review", label: "Needs review" },
  { key: "accepted", label: "Accepted" },
  { key: "rejected", label: "Rejected" },
  { key: "", label: "All" },
];

const noteStyle = { font: "inherit", fontSize: 15, padding: "6px 10px", border: "1px solid var(--rule-strong)", borderRadius: 3, background: "rgba(255,255,255,.5)", minWidth: 220 } as const;

export default function Review() {
  const [kind, setKind] = useState<SubmissionKind>("procedure");
  const [status, setStatus] = useState<SubmissionStatus>("pending");
  const [state, setState] = useState<ApiState<{ submissions: Row[] }>>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [note, setNote] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const ac = new AbortController();
    setState({ kind: "loading" });
    listSubmissions(kind, status, 100, ac.signal).then(setState);
    return () => ac.abort();
  }, [kind, status, reload]);

  async function decide(id: string, decision: "accepted" | "rejected" | "needs_review") {
    if (decision === "rejected" && !window.confirm("Reject this submission?")) return;
    const r = await reviewSubmission(kind, id, decision, note[id]);
    setMsg(r.kind === "ok" ? `Submission ${id.slice(0, 8)} → ${decision}.` : r.kind === "error" ? r.message : `Not recorded (${r.kind}).`);
    if (r.kind === "ok") setReload((n) => n + 1);
  }

  const rows = state.kind === "ok" ? state.data.submissions : [];
  return (
    <>
      <Hero marker="REVIEW" title="Review" lead="Accepting a new procedure or improvement pays its contributor Credits, so each decision is a ledger event." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <div style={{ gridColumn: "1 / span 12", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px 32px" }}>
          <nav className="tabs" style={{ marginTop: 0 }} aria-label="Submission type">
            {(["procedure", "benchmark"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{k === "procedure" ? "Procedures" : "Benchmarks"}</button>
            ))}
          </nav>
          <nav className="tabs" style={{ marginTop: 0 }} aria-label="Status">
            {statuses.map((s) => (
              <button key={s.label} type="button" aria-pressed={status === s.key} onClick={() => setStatus(s.key)}>{s.label}</button>
            ))}
          </nav>
        </div>
        {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}
        {state.kind !== "ok" ? (
          <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div>
        ) : rows.length === 0 ? (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>Nothing here.</b></div>
        ) : (
          <ul className="list" style={{ gridColumn: "1 / span 12" }} aria-label="Submissions">
            {rows.map((r, i) => {
              const id = str(r.id);
              const open = ["pending", "needs_review"].includes(str(r.status));
              return (
                <li key={id}>
                  <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0 20px", padding: "14px 0" }}>
                    <span className="n">{String(i + 1).padStart(3, "0")}</span>
                    <div>
                      <h3>{str(r.title) || str(r.name) || str(r.goal) || `Submission ${id.slice(0, 8)}`}</h3>
                      <div className="meta">
                        <span>{str(r.status) || "unknown"}</span>
                        {r.submission_type != null && <span>{str(r.submission_type)}</span>}
                        {r.submitted_by != null && <span>by {str(r.submitted_by)}</span>}
                        {r.goal_id != null && <span className="mono">goal {str(r.goal_id).slice(0, 8)}</span>}
                        {r.created_at != null && <span>{str(r.created_at).slice(0, 10)}</span>}
                      </div>
                      {open && (
                        <div className="row-actions" style={{ marginTop: 12 }}>
                          <input aria-label="Reviewer note" placeholder="Note (optional)" value={note[id] ?? ""} onChange={(e) => setNote({ ...note, [id]: e.target.value })} style={noteStyle} />
                          <button type="button" className="btn-line" onClick={() => decide(id, "accepted")}>Accept</button>
                          <button type="button" className="btn-line" onClick={() => decide(id, "needs_review")}>Needs review</button>
                          <button type="button" className="btn-line danger" onClick={() => decide(id, "rejected")}>Reject</button>
                        </div>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}
