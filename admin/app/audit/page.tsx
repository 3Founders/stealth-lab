"use client";
import { Fragment, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { CsvButton, Updated } from "@/components/Widgets";
import { isRange } from "@/lib/range";
import { downloadAuditCsv, getAudit, type ApiState, type AuditPage, type Org } from "@/lib/org-api";


function AuditPanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(7);
  const { since, until } = range;
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<ApiState<AuditPage>>({ kind: "loading" });
  const [events, setEvents] = useState<AuditPage["events"]>([]);
  const [broken, setBroken] = useState<number | null>(null);
  const [intact, setIntact] = useState(true);
  const [next, setNext] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  async function load(after: number, append: boolean, signal?: AbortSignal) {
    if (!append) setState({ kind: "loading" });
    const r = await getAudit(org.organization_id, `${since}T00:00:00Z`, `${until}T00:00:00Z`, after, 200, signal);
    setState(r);
    if (r.kind !== "ok") return;
    setLoadedAt(new Date());
    setEvents((prev) => (append ? [...prev, ...r.data.events] : r.data.events));
    setNext(r.data.next_after_id);
    setIntact((was) => (append ? was && r.data.chain_intact : r.data.chain_intact));
    setBroken((was) => (append && was != null ? was : r.data.first_broken_id));
  }

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    load(0, false, ac.signal);
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [org.organization_id, since, until, ready, nonce]);

  async function exportCsv() {
    setBusy(true);
    const r = await downloadAuditCsv(org.organization_id, `${since}T00:00:00Z`, `${until}T00:00:00Z`);
    setBusy(false);
    setMsg(r.ok ? `${r.message} Chain ${r.chainIntact ? "verified intact." : "did NOT verify. Treat this export as suspect."}` : r.message);
  }

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="audit" />
      <div style={{ gridColumn: "1 / span 12", display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
        <button type="button" className="btn-line" disabled={busy || state.kind !== "ok"} onClick={exportCsv}>{busy ? "Exporting…" : "Export CSV (server, full range)"}</button>
        <CsvButton label="Export loaded rows" filename={`audit-loaded-${since}-${until}.csv`}
          header={["id", "time", "action", "actor", "object_type", "object_id", "row_hash", "prev_hash"]}
          rows={() => events.map((e) => [e.id, e.t_created, e.action, e.actor_subject, e.object_type, e.object_id, e.row_hash, e.prev_hash])} />
        <Updated at={loadedAt} onRefresh={() => setNonce((n) => n + 1)} busy={state.kind === "loading"} />
      </div>
      {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}

      {state.kind !== "ok" && events.length === 0 ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div> : (
        <>
          <div className="stats">
            <Stat label="Hash chain" value={intact ? "Intact" : "BROKEN"} bad={!intact}
              note={intact ? "Every event links to the one before it" : `First broken event: #${broken}. The log was altered or lost a row.`} />
            <Stat label="Events loaded" value={events.length} note={next != null ? "More in this range" : "End of range"} />
          </div>
          {events.length === 0 ? <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No audit events in this range.</b></div> : (
            <div className="panel scroll-x">
              <table className="dtable">
                <thead><tr><th>#</th><th>When</th><th>Action</th><th>Actor</th><th>Object</th><th></th></tr></thead>
                <tbody>
                  {events.map((e) => (
                    <Fragment key={e.id}>
                      <tr style={e.id === broken ? { background: "var(--soft)" } : undefined}>
                        <td className="mono">{e.id}</td>
                        <td>{String(e.t_created).slice(0, 19).replace("T", " ")}</td>
                        <td>{e.action}</td>
                        <td className="mono">{e.actor_subject ? String(e.actor_subject).slice(0, 12) : "system"}</td>
                        <td>{e.object_type ? `${e.object_type}${e.object_id ? ` ${String(e.object_id).slice(0, 8)}` : ""}` : "—"}</td>
                        <td><button type="button" className="btn-line" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>{open === e.id ? "Hide" : "Details"}</button></td>
                      </tr>
                      {open === e.id && (
                        <tr><td colSpan={6}>
                          <pre className="mono small" style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ details: e.details, prev_hash: e.prev_hash, row_hash: e.row_hash }, null, 2)}</pre>
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
              {next != null && <div><button type="button" className="btn-ink" onClick={() => load(next, true)}><span>Load more</span></button></div>}
            </div>
          )}
        </>
      )}
    </>
  );
}

export default function AuditPage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Audit" lead="An append-only, hash-chained record of governance actions, with a check that it hasn't been altered." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <AuditPanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
