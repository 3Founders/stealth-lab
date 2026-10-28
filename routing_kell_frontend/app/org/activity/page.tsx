"use client";
import { useCallback, useEffect, useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { REASON_TEXT, lastDays, nameFor, usd, type CallRow, type CallStatus, type DenialsPage } from "@/lib/org-admin";

const RANGES = [{ days: 1, label: "Today" }, { days: 7, label: "7 days" }, { days: 30, label: "30 days" }];
const when = (iso: string) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const tokens = (c: CallRow) => ["tokens_input_fresh", "tokens_cache_read", "tokens_cache_write", "tokens_output"].reduce((n, k) => n + Number((c as unknown as Record<string, number | null>)[k] ?? 0), 0);

/** What was called and what was refused, filterable. Metadata only: there is no prompt or reply in this data. */
export default function ActivityPage() {
  const { admin } = useOrg();
  const [tab, setTab] = useState<"calls" | "denials">("calls");
  const [days, setDays] = useState(7);
  if (!admin) return <div className="empty"><b>Activity is visible to owners and admins.</b></div>;
  return (
    <div className="org-stack">
      <div className="org-filters">
        <div role="tablist" aria-label="Activity" className="org-tabs">
          <button type="button" role="tab" aria-selected={tab === "calls"} onClick={() => setTab("calls")}>Calls</button>
          <button type="button" role="tab" aria-selected={tab === "denials"} onClick={() => setTab("denials")}>Refused</button>
        </div>
        <label className="org-picker">Period
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{RANGES.map((r) => <option key={r.days} value={r.days}>{r.label}</option>)}</select>
        </label>
      </div>
      {tab === "calls" ? <Calls days={days} /> : <Denials days={days} />}
      <p className="small dim">Times are your local time. Only metadata is kept here: who, which model, how many tokens, what it cost. Never the prompt or the reply.</p>
    </div>
  );
}

function Calls({ days }: { days: number }) {
  const { api, org } = useOrg();
  const id = org.organization_id;
  const members = useLoad(() => api.members(id), `m${id}`);
  const [model, setModel] = useState("");
  const [tool, setTool] = useState("");
  const [status, setStatus] = useState<"" | CallStatus>("");
  const [user, setUser] = useState("");
  const [rows, setRows] = useState<CallRow[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [message, setMessage] = useState("");
  const [more, setMore] = useState(false);

  const names = members.loaded.state === "ok" ? members.loaded.data : [];
  const fetchPage = useCallback(async (after?: string) => {
    const r = lastDays(days);
    return api.calls(id, r.since, r.until, { model: model.trim() || undefined, tool: tool.trim() || undefined, status: status || undefined, user: user || undefined, after, limit: 100 });
  }, [api, id, days, model, tool, status, user]);

  useEffect(() => {
    let live = true;
    setState("loading");
    const t = setTimeout(() => { // typing in a filter box doesn't send a request per keystroke
      fetchPage().then((p) => { if (live) { setRows(p.calls); setNext(p.next_after); setState("ok"); } })
        .catch((e) => { if (live) { setState("error"); setMessage(e instanceof Error ? e.message : "Something went wrong."); } });
    }, 300);
    return () => { live = false; clearTimeout(t); };
  }, [fetchPage]);

  async function loadMore() {
    if (!next) return;
    setMore(true);
    try { const p = await fetchPage(next); setRows((r) => [...r, ...p.calls]); setNext(p.next_after); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Something went wrong."); setState("error"); }
    finally { setMore(false); }
  }

  return (
    <div className="profile-card">
      <h2 className="h3">Calls</h2>
      <div className="org-filters" role="group" aria-label="Filter calls">
        <label className="org-picker">Person
          <select value={user} onChange={(e) => setUser(e.target.value)}>
            <option value="">Everyone</option>
            {names.map((m) => <option key={m.user_id} value={m.subject}>{m.display_name || m.email || m.subject}</option>)}
          </select>
        </label>
        <label className="org-picker">Model<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="Any" /></label>
        <label className="org-picker">Tool<input value={tool} onChange={(e) => setTool(e.target.value)} placeholder="Any" /></label>
        <label className="org-picker">Status
          <select value={status} onChange={(e) => setStatus(e.target.value as "" | CallStatus)}>
            <option value="">Any</option><option value="settled">Settled</option><option value="failed">Failed</option><option value="reserved">In progress</option>
          </select>
        </label>
      </div>
      {state === "loading" && <p className="small dim" role="status">Loading calls…</p>}
      {state === "error" && <div className="empty" role="alert"><b>Couldn&rsquo;t load calls.</b><p>{message}</p></div>}
      {state === "ok" && (rows.length === 0
        ? <p className="small dim">No calls match.</p>
        : (
          <>
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>When</th><th>Person</th><th>Model</th><th>Tool</th><th>Data</th><th>Status</th><th className="num">Tokens</th><th className="num">Cost</th><th className="num">Time</th></tr></thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.id} data-status={c.status}>
                      <td>{when(c.created_at)}</td><td>{nameFor(c.user_id, names)}</td><td>{c.model}</td><td>{c.tool}</td><td>{c.data_class}</td>
                      <td>{c.status}{c.error_type && <span className="dim"> · {c.error_type}</span>}</td>
                      <td className="num">{tokens(c).toLocaleString("en-US")}</td><td className="num">{usd(c.cost_usd)}</td><td className="num">{c.provider_ms !== null ? `${Math.round(c.provider_ms)} ms` : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {next && <div className="cluster"><button type="button" className="btn-line" onClick={() => void loadMore()} disabled={more}>{more ? "Loading…" : "Load more"}</button></div>}
          </>
        ))}
    </div>
  );
}

function Denials({ days }: { days: number }) {
  const { api, org } = useOrg();
  const id = org.organization_id;
  const members = useLoad(() => api.members(id), `m${id}`);
  const names = members.loaded.state === "ok" ? members.loaded.data : [];
  const [page, setPage] = useState<DenialsPage | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [message, setMessage] = useState("");
  const [reason, setReason] = useState("");
  const [more, setMore] = useState(false);
  const r = lastDays(days);

  useEffect(() => {
    let live = true;
    setState("loading");
    api.denials(id, r.since, r.until, undefined, 100).then((p) => { if (live) { setPage(p); setState("ok"); } })
      .catch((e) => { if (live) { setState("error"); setMessage(e instanceof Error ? e.message : "Something went wrong."); } });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, id, days]);

  async function loadMore() {
    if (!page?.next_after) return;
    setMore(true);
    try { const p = await api.denials(id, r.since, r.until, page.next_after, 100); setPage({ ...p, events: [...page.events, ...p.events] }); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Something went wrong."); setState("error"); }
    finally { setMore(false); }
  }

  const events = (page?.events ?? []).filter((e) => !reason || e.reason_code === reason);
  return (
    <div className="profile-card">
      <h2 className="h3">Refused by the policy</h2>
      {state === "loading" && <p className="small dim" role="status">Loading…</p>}
      {state === "error" && <div className="empty" role="alert"><b>Couldn&rsquo;t load this.</b><p>{message}</p></div>}
      {state === "ok" && page && (
        page.counts_by_reason.length === 0
          ? <p className="small dim">Nothing was refused in this period.</p>
          : (
            <>
              <div className="org-chips" role="group" aria-label="Filter by reason">
                <button type="button" className="org-chipbtn" aria-pressed={reason === ""} onClick={() => setReason("")}>All</button>
                {page.counts_by_reason.map((c) => (
                  <button key={c.reason_code} type="button" className="org-chipbtn" aria-pressed={reason === c.reason_code} onClick={() => setReason(reason === c.reason_code ? "" : c.reason_code)}>
                    {REASON_TEXT[c.reason_code] ?? c.reason_code} · {c.count}
                  </button>
                ))}
              </div>
              <div className="org-table-wrap">
                <table className="org-table">
                  <thead><tr><th>When</th><th>Person</th><th>Model</th><th>Tool</th><th>Data</th><th>Why</th></tr></thead>
                  <tbody>
                    {events.map((e) => (
                      <tr key={e.id}><td>{when(e.created_at)}</td><td>{nameFor(e.user_id, names)}</td><td>{e.model}</td><td>{e.tool}</td><td>{e.data_class}</td><td>{REASON_TEXT[e.reason_code] ?? e.reason_code}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {page.next_after && <div className="cluster"><button type="button" className="btn-line" onClick={() => void loadMore()} disabled={more}>{more ? "Loading…" : "Load more"}</button></div>}
            </>
          )
      )}
    </div>
  );
}
