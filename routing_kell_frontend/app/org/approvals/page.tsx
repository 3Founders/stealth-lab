"use client";
import { useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import type { OrgApproval } from "@/lib/org-admin";

const when = (iso: string | null) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
const hash = (s: string) => String(s || "").replace(/^sha256:/, "").slice(0, 12);
const STATUS: Record<string, string> = {
  pending: "Waiting", approved: "Approved", denied: "Denied", expired: "Expired", used: "Used",
};

/** Tool calls a member's agent is waiting on: the exact call shape, who asked, and the one-use decision. */
export default function ApprovalsPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const approvals = useLoad(() => api.approvals(id, tab), `${tab}${id}`);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  if (!admin) return <div className="empty"><b>Approvals are visible to owners and admins.</b></div>;

  async function decide(a: OrgApproval, yes: boolean) {
    setBusy(a.id); setError("");
    try {
      if (yes) await api.approveApproval(id, a.id);
      else await api.denyApproval(id, a.id);
      approvals.reload();
    } catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }

  return (
    <div className="org-stack">
      {error && <p className="org-banner small" role="alert">{error}</p>}
      <div className="org-filters">
        <div role="tablist" aria-label="Approvals" className="org-tabs">
          <button type="button" role="tab" aria-selected={tab === "pending"} onClick={() => setTab("pending")}>Waiting</button>
          <button type="button" role="tab" aria-selected={tab === "all"} onClick={() => setTab("all")}>All</button>
        </div>
      </div>

      <div className="profile-card">
        <h2 className="h3">Tool approvals</h2>
        <p className="small dim">An approval is bound to one exact call — owner, task, server, tool, argument hash and certificate — and is single-use. A changed argument is a different approval.</p>
        <LoadState loaded={approvals.loaded} reload={approvals.reload} what="approvals" />
        {approvals.loaded.state === "ok" && (approvals.loaded.data.length === 0
          ? <p className="small dim">{tab === "pending" ? "Nothing is waiting on you." : "No approvals yet."}</p>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>When</th><th>Person</th><th>Tool</th><th>Risk</th><th>Data</th><th>Arguments</th><th>Expires</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {approvals.loaded.data.map((a) => (
                    <tr key={a.id} data-status={a.status === "denied" ? "failed" : undefined}>
                      <td>{when(a.requested_at)}</td>
                      <td>{a.owner_name || a.owner_email || a.owner_id}</td>
                      <td><b>{a.tool}</b><span className="dim"> · {a.server}</span></td>
                      <td>{a.risk}</td>
                      <td>{a.data_class}</td>
                      <td><code>sha256:{hash(a.args_hash)}…</code></td>
                      <td>{when(a.expires_at)}</td>
                      <td>{STATUS[a.status] ?? a.status}{a.decided_by && <span className="dim"> · {a.decided_by}</span>}</td>
                      <td className="org-actions">
                        {a.status === "pending" && (
                          <>
                            <button type="button" className="btn-ink dg-mini-btn" disabled={busy === a.id} onClick={() => void decide(a, true)}><span>Allow once</span></button>
                            <button type="button" className="btn-line dg-mini-btn" disabled={busy === a.id} onClick={() => void decide(a, false)}>Deny</button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>
    </div>
  );
}
