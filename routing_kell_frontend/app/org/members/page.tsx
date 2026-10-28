"use client";
import { useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import type { CreatedInvite } from "@/lib/org-admin";

const day = (iso: string) => iso.slice(0, 10);

/** Who is in the organization, and the invite codes that bring people in. */
export default function MembersPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const members = useLoad(() => api.members(id), `m${id}`);
  const invites = useLoad(() => api.invites(id), `i${id}`);
  const [label, setLabel] = useState("");
  const [days, setDays] = useState(7);
  const [uses, setUses] = useState(50);
  const [made, setMade] = useState<CreatedInvite | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!admin) return <div className="empty"><b>Members are visible to owners and admins.</b></div>;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(""); setCopied(false);
    try { setMade(await api.createInvite(id, { label: label.trim() || undefined, days, max_uses: uses })); setLabel(""); invites.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(false); }
  }
  async function revoke(inviteId: string) {
    try { await api.revokeInvite(id, inviteId); invites.reload(); } catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
  }
  async function copy(code: string) {
    try { await navigator.clipboard.writeText(code); setCopied(true); } catch { setCopied(false); }
  }

  return (
    <div className="org-stack">
      <div className="profile-card">
        <h2 className="h3">Members</h2>
        <LoadState loaded={members.loaded} reload={members.reload} what="members" />
        {members.loaded.state === "ok" && (
          <div className="org-table-wrap">
            <table className="org-table">
              <thead><tr><th>Person</th><th>Role</th><th>Member since</th><th>Status</th></tr></thead>
              <tbody>
                {members.loaded.data.map((m) => (
                  <tr key={m.user_id}>
                    <td>{m.display_name || m.email || m.subject}{m.display_name && m.email && <span className="dim"> · {m.email}</span>}</td>
                    <td>{m.roles.join(", ")}</td><td>{day(m.member_since)}</td><td>{m.is_active ? "Active" : "Inactive"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small dim">Roles are set when someone joins. Changing a role or removing a member isn&rsquo;t available yet.</p>
      </div>

      <form className="profile-card cform" onSubmit={(e) => void create(e)}>
        <h2 className="h3">Invite your team</h2>
        <div className="cfield"><label htmlFor="inv-label">Label <span className="dim">(optional)</span></label>
          <input id="inv-label" type="text" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="Platform team" /></div>
        <div className="org-row">
          <div className="cfield"><label htmlFor="inv-days">Valid for (days)</label><input id="inv-days" type="number" min={1} max={90} value={days} onChange={(e) => setDays(Number(e.target.value))} /></div>
          <div className="cfield"><label htmlFor="inv-uses">People who can use it</label><input id="inv-uses" type="number" min={1} max={500} value={uses} onChange={(e) => setUses(Number(e.target.value))} /></div>
        </div>
        <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy}><span>{busy ? "Creating…" : "Create invite code"}</span></button></div>
        {error && <p className="small" role="alert">{error}</p>}
        {made && (
          <div className="org-code" role="status">
            <p><b>Copy this code now.</b> It is shown once and can&rsquo;t be shown again.</p>
            <code className="org-code-value">{made.code}</code>
            <div className="cluster"><button type="button" className="btn-line" onClick={() => void copy(made.code)}>{copied ? "Copied" : "Copy"}</button></div>
            <p className="small dim">Valid until {day(made.expires_at)}, up to {made.max_uses} people. They redeem it at <code>/join</code>.</p>
          </div>
        )}
      </form>

      <div className="profile-card">
        <h2 className="h3">Invites</h2>
        <LoadState loaded={invites.loaded} reload={invites.reload} what="invites" />
        {invites.loaded.state === "ok" && (invites.loaded.data.length === 0
          ? <p className="small dim">No invites yet.</p>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>Label</th><th>Created</th><th>Expires</th><th className="num">Used</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {invites.loaded.data.map((i) => (
                    <tr key={i.id}>
                      <td>{i.label || <span className="dim">No label</span>}</td><td>{day(i.created_at)}</td><td>{day(i.expires_at)}</td><td className="num">{i.uses}/{i.max_uses}</td>
                      <td>{i.revoked_at ? "Revoked" : i.active ? "Active" : "Expired or used up"}</td>
                      <td>{i.active && <button type="button" className="btn-line" onClick={() => void revoke(i.id)}>Revoke</button>}</td>
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
