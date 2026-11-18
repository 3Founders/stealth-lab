"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { usd, type OrgAgent } from "@/lib/org-admin";

const CEILINGS = [
  { id: "PUBLIC_SOURCE", label: "Public source only" },
  { id: "PUBLIC_DERIVED", label: "Public and derived" },
  { id: "GLOBAL_PROCEDURE", label: "Public, derived and shared procedures" },
  { id: "USER_PRIVATE", label: "Private to a person or team" },
];
const STATUS_LABEL: Record<OrgAgent["status"], string> = {
  online: "Online", offline: "Offline", revoked: "Revoked", expired: "Expired",
};
const when = (iso: string | null) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
const scopesOf = (text: string) => text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);

/** The agents an organization's people run, with the owner, status and the containment recorded for each. */
export default function AgentsPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const agents = useLoad(() => admin ? api.agents(id) : Promise.resolve([]), `agents${id}`);
  const [name, setName] = useState("");
  const [ceiling, setCeiling] = useState("PUBLIC_SOURCE");
  const [scopes, setScopes] = useState("");
  const [budget, setBudget] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const dialog = useRef<HTMLDialogElement>(null);
  const [editing, setEditing] = useState<OrgAgent | null>(null);
  const [eScopes, setEScopes] = useState("");
  const [eCeiling, setECeiling] = useState("PUBLIC_SOURCE");
  const [eBudget, setEBudget] = useState("");
  const [eExpires, setEExpires] = useState("");

  if (!admin) return <div className="empty"><b>Agents are visible to owners and admins.</b></div>;

  const live = agents.loaded.state === "ok" ? agents.loaded.data.filter((a) => a.status === "online" || a.status === "offline") : [];
  const waiting = agents.loaded.state === "ok" ? agents.loaded.data.filter((a) => a.status === "expired") : [];

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy("create"); setError("");
    try {
      await api.createAgent(id, {
        name: name.trim(),
        kind: "agent",
        data_ceiling: ceiling,
        scopes: scopesOf(scopes),
        ...(budget.trim() ? { budget_usd_month: budget.trim() } : {}),
        ...(expires.trim() ? { expires_days: Number(expires) } : {}),
      });
      setName(""); setScopes(""); setBudget(""); setExpires("");
      agents.reload();
    } catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function revoke(a: OrgAgent) {
    if (!window.confirm(`Revoke ${a.name}? Its unfinished tasks are cancelled.`)) return;
    setBusy(a.id); setError("");
    try { await api.revokeAgent(id, a.id); agents.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  function openEdit(a: OrgAgent) {
    setEditing(a);
    setEScopes(a.scopes.join(", "));
    setECeiling(a.data_ceiling || "PUBLIC_SOURCE");
    setEBudget(a.budget_usd_month ?? "");
    setEExpires("");
    setError("");
    dialog.current?.showModal();
  }
  async function saveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(editing.id); setError("");
    try {
      const clearingExpiry = Boolean(editing.expires_at) && !eExpires.trim();
      await api.updateAgent(id, editing.id, {
        scopes: scopesOf(eScopes),
        data_ceiling: eCeiling,
        budget_usd_month: eBudget.trim(),
        ...(eExpires.trim() ? { expires_days: Number(eExpires) } : clearingExpiry ? { clear_expiry: true } : {}),
      });
      dialog.current?.close();
      setEditing(null);
      agents.reload();
    } catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }

  return (
    <div className="org-stack">
      {error && <p className="org-banner small" role="alert">{error}</p>}

      <div className="profile-card">
        <h2 className="h3">Agents</h2>
        <p className="small dim">These are relay agents (the ones that read their inbox with <code>delegate_inbox</code>). A desktop session is reported by the desktop app and appears in the chat&apos;s Desktop sessions panel; an MCP connector is added in the chat. The scope, data ceiling, budget and expiry here are <b>recorded for review</b>; the gate does not enforce them yet.</p>
        {agents.loaded.state === "ok" && (
          <p className="small dim">{live.length} active · {waiting.length} expired · {agents.loaded.data.length} total</p>
        )}
        <LoadState loaded={agents.loaded} reload={agents.reload} what="agents" />
        {agents.loaded.state === "ok" && (agents.loaded.data.length === 0
          ? <div className="empty"><b>No agents yet.</b><p>Add one below, or connect a coding agent in <Link href="/chat" style={{ textDecoration: "underline" }}>the chat</Link>.</p></div>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>Agent</th><th>Owner</th><th>Status</th><th>Data</th><th>Scopes</th><th className="num">Budget</th><th>Expires</th><th /></tr></thead>
                <tbody>
                  {agents.loaded.data.map((a) => (
                    <tr key={a.id} data-status={a.status === "revoked" || a.status === "expired" ? "failed" : undefined}>
                      <td><b>{a.name}</b><span className="dim"> · {a.kind} · {a.id.slice(0, 8)}</span></td>
                      <td>{a.owner_name || a.owner_email || a.owner_sub}</td>
                      <td><span className="org-status" data-status={a.status}>{STATUS_LABEL[a.status] ?? a.status}</span>{a.status === "offline" && a.last_seen && <span className="dim"> · {when(a.last_seen)}</span>}</td>
                      <td>{String(a.data_ceiling || "PUBLIC_SOURCE").replace(/_/g, " ").toLowerCase()}</td>
                      <td>{a.scopes.length ? a.scopes.join(", ") : <span className="dim">not set</span>}</td>
                      <td className="num">{a.budget_usd_month ? usd(a.budget_usd_month) : "—"}</td>
                      <td>{a.expires_at ? when(a.expires_at) : "—"}</td>
                      <td className="org-actions">
                        {a.status !== "revoked" && <button type="button" className="btn-line dg-mini-btn" disabled={busy === a.id} onClick={() => openEdit(a)}>Edit</button>}
                        {a.status !== "revoked" && <button type="button" className="btn-line dg-mini-btn" disabled={busy === a.id} onClick={() => void revoke(a)}>Revoke</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>

      <form className="profile-card cform" onSubmit={(e) => void create(e)}>
        <h2 className="h3">Add an agent</h2>
        <div className="org-row">
          <div className="cfield"><label htmlFor="ag-name">Name</label><input id="ag-name" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} placeholder="reviewer" /></div>
          <div className="cfield"><label htmlFor="ag-ceiling">Data ceiling</label><select id="ag-ceiling" value={ceiling} onChange={(e) => setCeiling(e.target.value)}>{CEILINGS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></div>
        </div>
        <div className="org-row">
          <div className="cfield"><label htmlFor="ag-scopes">Scopes <span className="dim">(optional)</span></label><input id="ag-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="read, call_model" /></div>
          <div className="cfield"><label htmlFor="ag-budget">Monthly budget (USD, optional)</label><input id="ag-budget" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="20" /></div>
          <div className="cfield"><label htmlFor="ag-expires">Expires in (days, optional)</label><input id="ag-expires" type="number" min={1} max={365} value={expires} onChange={(e) => setExpires(e.target.value)} placeholder="30" /></div>
        </div>
        <p className="small dim">The agent runs on its owner&apos;s machine or relay. It reads its inbox with <code>delegate_inbox</code>; every tool call still passes the gate.</p>
        <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy === "create" || !name.trim()}><span>{busy === "create" ? "Adding…" : "Add agent"}</span></button></div>
      </form>

      <dialog ref={dialog} className="org-dialog" aria-labelledby="ag-edit-title" onClose={() => { setEditing(null); setBusy(""); }}>
        <form onSubmit={(e) => void saveEdit(e)} className="org-stack">
          <h2 id="ag-edit-title" className="h3">Edit {editing?.name}</h2>
          <p className="small dim">Only the containment record changes. The agent keeps its id, owner and history.</p>
          <div className="cfield"><label htmlFor="ed-ceiling">Data ceiling</label>
            <select id="ed-ceiling" value={eCeiling} onChange={(e) => setECeiling(e.target.value)}>{CEILINGS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></div>
          <div className="cfield"><label htmlFor="ed-scopes">Scopes <span className="dim">(empty clears)</span></label>
            <input id="ed-scopes" value={eScopes} onChange={(e) => setEScopes(e.target.value)} placeholder="read, call_model" /></div>
          <div className="org-row">
            <div className="cfield"><label htmlFor="ed-budget">Monthly budget (USD, empty clears)</label>
              <input id="ed-budget" inputMode="decimal" value={eBudget} onChange={(e) => setEBudget(e.target.value)} placeholder="20" /></div>
            <div className="cfield"><label htmlFor="ed-expires">New expiry in days {editing?.expires_at ? <span className="dim">(empty clears)</span> : <span className="dim">(optional)</span>}</label>
              <input id="ed-expires" type="number" min={1} max={365} value={eExpires} onChange={(e) => setEExpires(e.target.value)} placeholder={editing?.expires_at ? "clear" : "30"} /></div>
          </div>
          {error && <p className="small" role="alert">{error}</p>}
          <div className="cluster">
            <button type="submit" className="btn-ink" disabled={busy === editing?.id}><span>{busy === editing?.id ? "Saving…" : "Save changes"}</span></button>
            <button type="button" className="btn-line" onClick={() => dialog.current?.close()}>Cancel</button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
