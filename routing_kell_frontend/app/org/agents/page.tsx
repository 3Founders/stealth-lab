"use client";
import { useState } from "react";
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
const KINDS = [
  { id: "agent", label: "Relay agent (delegate_inbox)" },
  { id: "desktop", label: "Desktop session" },
  { id: "connector", label: "MCP connector" },
  { id: "a2a", label: "A2A agent" },
];
const when = (iso: string | null) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

/** The agents an organization's people run, with the owner, status and the containment recorded for each. */
export default function AgentsPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const agents = useLoad(() => api.agents(id), `agents${id}`);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("agent");
  const [ceiling, setCeiling] = useState("PUBLIC_SOURCE");
  const [scopes, setScopes] = useState("");
  const [budget, setBudget] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  if (!admin) return <div className="empty"><b>Agents are visible to owners and admins.</b></div>;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy("create"); setError("");
    try {
      await api.createAgent(id, {
        name: name.trim(),
        kind,
        data_ceiling: ceiling,
        scopes: scopes.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
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

  return (
    <div className="org-stack">
      {error && <p className="org-banner small" role="alert">{error}</p>}

      <div className="profile-card">
        <h2 className="h3">Agents</h2>
        <p className="small dim">An agent is owned by a person, not by the organization. This page shows who runs one, whether it is connected, and the scope and budget recorded for it. The gate is still the enforcement point.</p>
        <LoadState loaded={agents.loaded} reload={agents.reload} what="agents" />
        {agents.loaded.state === "ok" && (agents.loaded.data.length === 0
          ? <div className="empty"><b>No agents yet.</b><p>Add one below, or connect a coding agent in <Link href="/chat" style={{ textDecoration: "underline" }}>the chat</Link>.</p></div>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>Agent</th><th>Owner</th><th>Kind</th><th>Status</th><th>Data</th><th>Scopes</th><th className="num">Budget</th><th>Expires</th><th /></tr></thead>
                <tbody>
                  {agents.loaded.data.map((a) => (
                    <tr key={a.id} data-status={a.revoked_at ? "failed" : undefined}>
                      <td><b>{a.name}</b><span className="dim"> · {a.id.slice(0, 8)}</span></td>
                      <td>{a.owner_name || a.owner_email || a.owner_sub}</td>
                      <td>{a.kind}</td>
                      <td>{a.revoked_at ? "Revoked" : a.online ? "Online" : `Offline · ${when(a.last_seen)}`}</td>
                      <td>{a.data_ceiling.replace(/_/g, " ").toLowerCase()}</td>
                      <td>{a.scopes.length ? a.scopes.join(", ") : <span className="dim">not set</span>}</td>
                      <td className="num">{a.budget_usd_month ? usd(a.budget_usd_month) : "—"}</td>
                      <td>{a.expires_at ? when(a.expires_at) : "—"}</td>
                      <td className="org-actions">{!a.revoked_at && <button type="button" className="btn-line dg-mini-btn" disabled={busy === a.id} onClick={() => void revoke(a)}>Revoke</button>}</td>
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
          <div className="cfield"><label htmlFor="ag-kind">Kind</label><select id="ag-kind" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}</select></div>
        </div>
        <div className="org-row">
          <div className="cfield"><label htmlFor="ag-ceiling">Data ceiling</label><select id="ag-ceiling" value={ceiling} onChange={(e) => setCeiling(e.target.value)}>{CEILINGS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select></div>
          <div className="cfield"><label htmlFor="ag-scopes">Scopes <span className="dim">(optional)</span></label><input id="ag-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="read, call_model" /></div>
        </div>
        <div className="org-row">
          <div className="cfield"><label htmlFor="ag-budget">Monthly budget (USD, optional)</label><input id="ag-budget" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="20" /></div>
          <div className="cfield"><label htmlFor="ag-expires">Expires in (days, optional)</label><input id="ag-expires" type="number" min={1} max={365} value={expires} onChange={(e) => setExpires(e.target.value)} placeholder="30" /></div>
        </div>
        <p className="small dim">The agent runs on its owner&apos;s machine or relay. It reads its inbox with <code>delegate_inbox</code>; every tool call still passes the gate.</p>
        <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy === "create" || !name.trim()}><span>{busy === "create" ? "Adding…" : "Add agent"}</span></button></div>
      </form>
    </div>
  );
}
