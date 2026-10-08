"use client";
import { Fragment, useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { CsvButton, Updated } from "@/components/Widgets";
import { getDenials, getMembers, type ApiState, type DenialEvent, type Member, type Org } from "@/lib/org-api";
import { displayName } from "@/lib/people";
import { isRange } from "@/lib/range";

type Payload = { counts_by_reason: { reason_code: string; count: number }[]; events: DenialEvent[]; next_after: string | null };

import { reasonText } from "@/lib/denials";

function DenialsPanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(7);
  const [state, setState] = useState<ApiState<Payload>>({ kind: "loading" });
  const [events, setEvents] = useState<DenialEvent[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const [more, setMore] = useState(false);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const ac = new AbortController();
    getMembers(org.organization_id, ac.signal).then((r) => r.kind === "ok" && setMembers(r.data.members));
    return () => ac.abort();
  }, [org.organization_id]);

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    setState({ kind: "loading" });
    getDenials(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, undefined, 200, ac.signal).then((r) => {
      setState(r);
      if (r.kind === "ok") { setEvents(r.data.events); setNext(r.data.next_after); setLoadedAt(new Date()); }
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, nonce]);

  async function loadMore() {
    if (!next) return;
    setMore(true);
    const r = await getDenials(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, next, 200);
    setMore(false);
    if (r.kind === "ok") { setEvents((prev) => [...prev, ...r.data.events]); setNext(r.data.next_after); }
    else setState(r);
  }

  const counts = state.kind === "ok" ? [...state.data.counts_by_reason].sort((a, b) => b.count - a.count) : [];
  const totalDenied = counts.reduce((s, c) => s + c.count, 0);
  const max = counts.reduce((m, c) => Math.max(m, c.count), 0);

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="denials" />
      <div style={{ gridColumn: "1 / span 12" }}><Updated at={loadedAt} onRefresh={refresh} busy={state.kind === "loading"} /></div>
      <p className="notice-ok" style={{ gridColumn: "1 / span 12" }}>
        <b>Scope:</b> this lists refusals made by the organisation&rsquo;s policy and budget on model calls only. It is <b>not</b> every security event: sign-in failures, cross-tenant denials, content-screen rejections and some connection-level checks are not recorded here yet.
      </p>

      {state.kind !== "ok" && events.length === 0 ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div> : (
        <>
          <div className="stats">
            <Stat label="Refusals in range" value={totalDenied} bad={totalDenied > 0} note={`${counts.length} reason${counts.length === 1 ? "" : "s"}`} />
            {counts[0] && <Stat label="Top reason" value={counts[0].count} note={reasonText(counts[0].reason_code)} />}
          </div>

          {counts.length === 0 ? <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No refusals in this range.</b></div> : (
            <div className="panel">
              <h2 className="h3">By reason</h2>
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 10 }} aria-label="Refusals by reason">
                {counts.map((c) => (
                  <li key={c.reason_code} style={{ display: "grid", gridTemplateColumns: "minmax(160px, 260px) 1fr auto", gap: 12, alignItems: "center" }}>
                    <span title={c.reason_code}>{reasonText(c.reason_code)}</span>
                    <span aria-hidden="true" style={{ background: "var(--rule)", height: 14 }}><span style={{ display: "block", height: "100%", width: `${max ? (c.count / max) * 100 : 0}%`, background: "var(--ink)" }} /></span>
                    <b>{c.count}</b>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {events.length > 0 && (
            <div className="panel scroll-x">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                <h2 className="h3">Events</h2>
                <CsvButton label="Export loaded events" filename={`denials-${range.since}-${range.until}.csv`}
                  header={["id", "time", "user", "reason_code", "tool", "provider", "model", "unit", "data_class", "detail"]}
                  rows={() => events.map((e) => [String(e.id), e.created_at, e.user_id, e.reason_code, e.tool, e.provider, e.model, e.unit, e.data_class, e.detail == null ? "" : JSON.stringify(e.detail)])} />
              </div>
              <table className="dtable">
                <thead><tr><th>Time (UTC)</th><th>Reason</th><th>User</th><th>Tool</th><th>Model</th><th>Data class</th><th></th></tr></thead>
                <tbody>
                  {events.map((e) => (
                    <Fragment key={String(e.id)}>
                      <tr>
                        <td>{String(e.created_at).slice(0, 19).replace("T", " ")}</td>
                        <td title={e.reason_code}>{reasonText(e.reason_code)}</td>
                        <td>{displayName(e.user_id, members)}</td>
                        <td>{e.tool ?? "—"}</td>
                        <td>{e.model ? `${e.provider ?? ""} / ${e.model}` : "—"}</td>
                        <td>{e.data_class ?? "—"}</td>
                        <td><button type="button" className="btn-line" aria-expanded={open === String(e.id)} onClick={() => setOpen(open === String(e.id) ? null : String(e.id))}>{open === String(e.id) ? "Hide" : "Details"}</button></td>
                      </tr>
                      {open === String(e.id) && (
                        <tr><td colSpan={7}>
                          <pre className="mono small" style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ reason_code: e.reason_code, unit: e.unit, user_id: e.user_id, detail: e.detail }, null, 2)}</pre>
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
              {next && <div><button type="button" className="btn-ink" disabled={more} onClick={loadMore}><span>{more ? "Loading…" : "Load more"}</span></button></div>}
            </div>
          )}
        </>
      )}
    </>
  );
}

export default function DenialsPage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Denials" lead="Model calls the organisation's policy or budget refused, and why." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="person">{(org) => <DenialsPanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
