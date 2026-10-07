"use client";
import { Fragment, useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { CsvButton, Updated } from "@/components/Widgets";
import { formatUsd, toMicros } from "@/lib/money";
import { getCalls, getMembers, type ApiState, type CallFilters, type CallRow, type Member, type Org } from "@/lib/org-api";
import { displayName } from "@/lib/people";
import { fmtMs, tierLabel } from "@/lib/perf";
import { isRange } from "@/lib/range";

const STATUSES = ["", "settled", "failed", "reserved"] as const;
const inputStyle = { font: "inherit", fontSize: 15, padding: "8px 10px", border: "1px solid var(--rule-strong)", borderRadius: 3, background: "rgba(255,255,255,.5)" } as const;

function CallsPanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(1);
  const [filters, setFilters] = useState<CallFilters>({});
  const [draft, setDraft] = useState<CallFilters>({});
  const [state, setState] = useState<ApiState<{ calls: CallRow[]; next_after: string | null }>>({ kind: "loading" });
  const [calls, setCalls] = useState<CallRow[]>([]);
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
    getCalls(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, filters, undefined, 200, ac.signal).then((r) => {
      setState(r);
      if (r.kind === "ok") { setCalls(r.data.calls); setNext(r.data.next_after); setLoadedAt(new Date()); }
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, filters, nonce]);

  async function loadMore() {
    if (!next) return;
    setMore(true);
    const r = await getCalls(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, filters, next, 200);
    setMore(false);
    if (r.kind === "ok") { setCalls((prev) => [...prev, ...r.data.calls]); setNext(r.data.next_after); }
    else setState(r);
  }

  const total = calls.reduce((s, c) => s + toMicros(c.cost_usd), 0n);
  const failed = calls.filter((c) => c.status === "failed").length;

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="calls" />
      <form style={{ gridColumn: "1 / span 12", display: "flex", flexWrap: "wrap", gap: 12, alignItems: "end" }}
        onSubmit={(e) => { e.preventDefault(); setFilters({ model: draft.model?.trim(), tool: draft.tool?.trim(), status: draft.status, user: draft.user }); }}>
        <div className="cfield"><label htmlFor="fm">Model</label><input id="fm" type="text" value={draft.model ?? ""} onChange={(e) => setDraft({ ...draft, model: e.target.value })} style={inputStyle} placeholder="exact model id" /></div>
        <div className="cfield"><label htmlFor="ft">Tool</label><input id="ft" type="text" value={draft.tool ?? ""} onChange={(e) => setDraft({ ...draft, tool: e.target.value })} style={inputStyle} placeholder="e.g. call_model" /></div>
        <div className="cfield"><label htmlFor="fs">Status</label>
          <select id="fs" value={draft.status ?? ""} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>{STATUSES.map((s) => <option key={s} value={s}>{s || "any"}</option>)}</select></div>
        <div className="cfield"><label htmlFor="fu">User</label>
          <select id="fu" value={draft.user ?? ""} onChange={(e) => setDraft({ ...draft, user: e.target.value })}>
            <option value="">anyone</option>{members.map((m) => <option key={m.user_id} value={m.user_id}>{displayName(m.subject, members)}</option>)}
          </select></div>
        <button type="submit" className="btn-ink"><span>Apply filters</span></button>
        <button type="button" className="btn-line" onClick={() => { setDraft({}); setFilters({}); }}>Clear</button>
      </form>
      <div style={{ gridColumn: "1 / span 12" }}><Updated at={loadedAt} onRefresh={refresh} busy={state.kind === "loading"} /></div>

      {state.kind !== "ok" && calls.length === 0 ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div> : calls.length === 0 ? (
        <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No calls match.</b><p>Only <code>call_model</code> calls that policy allowed are listed; refusals are under Denials.</p></div>
      ) : (
        <>
          <div className="stats">
            <Stat label="Calls loaded" value={calls.length} note={next ? "More in this range" : "End of range"} />
            <Stat label="Cost of loaded calls" value={formatUsd(total)} />
            <Stat label="Failed" value={failed} bad={failed > 0} note={calls.length > 0 ? `${((failed / calls.length) * 100).toFixed(1)}% of loaded` : undefined} />
          </div>
          <div className="panel scroll-x">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <h2 className="h3">Calls (oldest first)</h2>
              <CsvButton label="Export loaded calls" filename={`calls-${range.since}-${range.until}.csv`}
                header={["id", "time", "user", "provider", "model", "tool", "tier", "status", "data_class", "tokens_input_fresh", "tokens_cache_read", "tokens_cache_write", "tokens_output", "cost_usd", "cost_source", "provider_ms", "gate_ms", "error_type"]}
                rows={() => calls.map((c) => [c.id, c.created_at, c.user_id, c.provider, c.model, c.tool, c.tier, c.status, c.data_class, c.tokens_input_fresh, c.tokens_cache_read, c.tokens_cache_write, c.tokens_output, c.cost_usd, c.cost_source, c.provider_ms, c.gate_ms, c.error_type])} />
            </div>
            <table className="dtable">
              <thead><tr><th>Time (UTC)</th><th>User</th><th>Model</th><th>Tool</th><th>Status</th><th>Tokens in / out</th><th>Cost</th><th>Provider</th><th>Router</th><th></th></tr></thead>
              <tbody>
                {calls.map((c) => (
                  <Fragment key={c.id}>
                    <tr style={c.status === "failed" ? { background: "var(--soft)" } : undefined}>
                      <td>{String(c.created_at).slice(0, 19).replace("T", " ")}</td>
                      <td>{displayName(c.user_id, members)}</td>
                      <td>{c.provider} / {c.model}</td>
                      <td>{c.tool}</td>
                      <td>{c.status}{c.error_type && <span className="dim"> · {c.error_type}</span>}</td>
                      <td>{c.tokens_input_fresh + c.tokens_cache_read + c.tokens_cache_write} / {c.tokens_output}</td>
                      <td>{c.cost_usd == null ? "—" : formatUsd(toMicros(c.cost_usd))}</td>
                      <td>{fmtMs(c.provider_ms)}</td><td>{fmtMs(c.gate_ms)}</td>
                      <td><button type="button" className="btn-line" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "Hide" : "Details"}</button></td>
                    </tr>
                    {open === c.id && (
                      <tr><td colSpan={10}>
                        <div className="log">
                          <div><span>Call id</span><span className="mono">{c.id}</span></div>
                          <div><span>User id</span><span className="mono">{c.user_id ?? "—"}</span></div>
                          <div><span>Tier</span><span>{tierLabel(c.tier)}</span></div>
                          <div><span>Data class</span><span>{c.data_class ?? "—"}</span></div>
                          <div><span>Policy decision</span><span>{c.policy_decision}</span></div>
                          <div><span>Tokens</span><span>fresh {c.tokens_input_fresh} · cache read {c.tokens_cache_read} · cache write {c.tokens_cache_write} · output {c.tokens_output}</span></div>
                          <div><span>Cost source</span><span>{c.cost_source ?? "—"}</span></div>
                          {c.instance_key && <div><span>Instance</span><span className="mono">{c.instance_key}</span></div>}
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {next && <div><button type="button" className="btn-ink" disabled={more} onClick={loadMore}><span>{more ? "Loading…" : "Load more"}</span></button></div>}
            <p className="small dim">Metadata only: no prompt or reply content is stored anywhere, so none can be shown. A failed call carries only its error type.</p>
          </div>
        </>
      )}
    </>
  );
}

export default function CallsPage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Calls" lead="Every allowed model call: who made it, which model, what it cost and how long it took." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <CallsPanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
