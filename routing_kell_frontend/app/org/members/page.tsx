"use client";
import { useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import type { CreatedInvite, Member, Role } from "@/lib/org-admin";

const day = (iso: string) => iso.slice(0, 10);
const ROLES: Array<{ id: Role; label: string; why: string }> = [
  { id: "owner", label: "Owner", why: "Everything, including billing and other owners." },
  { id: "admin", label: "Admin", why: "Policy, keys, members and agents; not billing or ownership." },
  { id: "member", label: "Member", why: "Uses the organization's tools and rooms." },
  { id: "viewer", label: "Viewer", why: "Read-only access to shared work." },
];

/** Who is in the organization, the invite codes that bring people in, and the two actions a team actually needs:
 *  change a role and remove someone. */
export default function MembersPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const members = useLoad(() => api.members(id), `m${id}`);
  const invites = useLoad(() => api.invites(id), `i${id}`);
  const [label, setLabel] = useState("");
  const [days, setDays] = useState(7);
  const [uses, setUses] = useState(50);
  const [inviteRole, setInviteRole] = useState<Role>("member");
  const [made, setMade] = useState<CreatedInvite | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState<"code" | "link" | null>(null);

  if (!admin) return <div className="empty"><b>Members are visible to owners and admins.</b></div>;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy("invite"); setError(""); setCopied(null);
    try {
      setMade(await api.createInvite(id, { label: label.trim() || undefined, days, max_uses: uses, role: inviteRole }));
      setLabel(""); invites.reload();
    } catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function revoke(inviteId: string) {
    setBusy(inviteId); setError("");
    try { await api.revokeInvite(id, inviteId); invites.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function changeRole(m: Member, role: Role) {
    setBusy(m.user_id); setError("");
    try { await api.setMemberRole(id, m.user_id, role); members.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function remove(m: Member) {
    const who = m.display_name || m.email || m.subject;
    if (!window.confirm(`Remove ${who} from ${org.name}? Their account stays; they lose access to this organization and its agents.`)) return;
    setBusy(m.user_id); setError("");
    try { await api.removeMember(id, m.user_id); members.reload(); invites.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function copy(text: string, what: "code" | "link") {
    try { await navigator.clipboard.writeText(text); setCopied(what); } catch { setCopied(null); }
  }

  return (
    <div className="org-stack">
      {error && <p className="org-banner small" role="alert">{error}</p>}

      <div className="profile-card">
        <h2 className="h3">Members</h2>
        <LoadState loaded={members.loaded} reload={members.reload} what="members" />
        {members.loaded.state === "ok" && (
          <div className="org-table-wrap">
            <table className="org-table">
              <thead><tr><th>Person</th><th>Role</th><th>Member since</th><th>Status</th><th /></tr></thead>
              <tbody>
                {members.loaded.data.map((m) => (
                  <tr key={m.user_id}>
                    <td>{m.display_name || m.email || m.subject}{m.display_name && m.email && <span className="dim"> · {m.email}</span>}</td>
                    <td>
                      <select aria-label={`Role for ${m.display_name || m.email || m.subject}`} value={m.roles[0] ?? "member"}
                        disabled={busy === m.user_id} onChange={(e) => void changeRole(m, e.target.value as Role)}>
                        {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                      </select>
                    </td>
                    <td>{day(m.member_since)}</td>
                    <td>{m.is_active ? "Active" : "Inactive"}</td>
                    <td className="org-actions">
                      <button type="button" className="btn-line dg-mini-btn" disabled={busy === m.user_id}
                        onClick={() => void remove(m)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small dim">An organization needs at least one owner. Removing someone revokes the agents they owned here; their account and personal work are untouched.</p>
      </div>

      <form className="profile-card cform" onSubmit={(e) => void create(e)}>
        <h2 className="h3">Invite your team</h2>
        <div className="org-row">
          <div className="cfield"><label htmlFor="inv-label">Label <span className="dim">(optional)</span></label>
            <input id="inv-label" type="text" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="Platform team" /></div>
          <div className="cfield"><label htmlFor="inv-role">They join as</label>
            <select id="inv-role" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Role)}>
              {ROLES.filter((r) => r.id !== "owner" || org.role === "owner").map((r) => <option key={r.id} value={r.id}>{r.label} — {r.why}</option>)}
            </select></div>
        </div>
        <div className="org-row">
          <div className="cfield"><label htmlFor="inv-days">Valid for (days)</label><input id="inv-days" type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></div>
          <div className="cfield"><label htmlFor="inv-uses">People who can use it</label><input id="inv-uses" type="number" min={1} max={5000} value={uses} onChange={(e) => setUses(Number(e.target.value))} /></div>
        </div>
        <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy === "invite"}><span>{busy === "invite" ? "Creating…" : "Create invite"}</span></button></div>
        {made && (
          <div className="org-code" role="status">
            <p><b>Share this invite.</b> It is shown once and can&rsquo;t be shown again. It joins people as <b>{made.role}</b>.</p>
            <code className="org-code-value">{made.code}</code>
            <div className="cluster">
              <button type="button" className="btn-line" onClick={() => void copy(made.code, "code")}>{copied === "code" ? "Copied" : "Copy code"}</button>
              <button type="button" className="btn-line" onClick={() => void copy(`${window.location.origin}/join?code=${encodeURIComponent(made.code)}`, "link")}>{copied === "link" ? "Copied" : "Copy link"}</button>
            </div>
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
                <thead><tr><th>Label</th><th>Role</th><th>Created</th><th>Expires</th><th className="num">Used</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {invites.loaded.data.map((i) => (
                    <tr key={i.id}>
                      <td>{i.label || <span className="dim">No label</span>}</td><td>{i.role}</td><td>{day(i.created_at)}</td><td>{day(i.expires_at)}</td><td className="num">{i.uses}/{i.max_uses}</td>
                      <td>{i.revoked_at ? "Revoked" : i.active ? "Active" : "Expired or used up"}</td>
                      <td>{i.active && <button type="button" className="btn-line" disabled={busy === i.id} onClick={() => void revoke(i.id)}>Revoke</button>}</td>
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
