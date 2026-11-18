"use client";
import { useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { lastDays, type AuditPage, type CreatedInvite, type Member, type Role } from "@/lib/org-admin";

const day = (iso: string) => iso.slice(0, 10);
const when = (iso: string) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const ROLES: Array<{ id: Role; label: string; why: string }> = [
  { id: "owner", label: "Owner", why: "Everything, including billing and other owners." },
  { id: "admin", label: "Admin", why: "Policy, keys, members and agents; not billing or ownership." },
  { id: "member", label: "Member", why: "Uses the organization's tools and rooms." },
  { id: "viewer", label: "Viewer", why: "Read-only access to shared work." },
];
const ADMIN_CHANGE = /^(org_|tool_approval\.)/;

/**
 * Who is in the organization, what each role can do, the invite codes that bring people in, and the two actions a
 * team actually needs: change a role and remove someone. Recent admin changes come from the same audit chain the
 * rest of the console uses, so this page answers "who changed what" without a second log.
 */
export default function MembersPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const members = useLoad(() => admin ? api.members(id) : Promise.resolve([]), `m${id}`);
  const invites = useLoad(() => admin ? api.invites(id) : Promise.resolve([]), `i${id}`);
  const catalog = useLoad(() => admin ? api.roles(id)
    : Promise.resolve({ organization_id: id, roles: [], capabilities: [], members: 0 }), `r${id}`);
  const changes = useLoad<AuditPage>(() => {
    if (!admin) return Promise.resolve({ events: [], chain_intact: true });
    const r = lastDays(30);
    return api.audit(id, r.since, r.until, { order: "desc", limit: 20 });
  }, `a${id}`);
  const [label, setLabel] = useState("");
  const [email, setEmail] = useState("");
  const [days, setDays] = useState(7);
  const [uses, setUses] = useState(50);
  const [inviteRole, setInviteRole] = useState<Role>("member");
  const [made, setMade] = useState<CreatedInvite | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [copied, setCopied] = useState<"code" | "link" | null>(null);

  if (!admin) return <div className="empty"><b>Members are visible to owners and admins.</b></div>;

  const owner = org.role === "owner";
  const canTouch = (m: Member) => owner || !m.roles.some((r) => r === "owner" || r === "admin");
  const roleOptions = ROLES.filter((r) => r.id !== "owner" || owner);
  const cat = catalog.loaded.state === "ok" ? catalog.loaded.data : null;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy("invite"); setError(""); setCopied(null);
    try {
      setMade(await api.createInvite(id, {
        label: label.trim() || undefined,
        days,
        max_uses: uses,
        role: inviteRole,
        email: email.trim() || undefined,
      }));
      setLabel(""); setEmail(""); invites.reload();
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
    try { await api.setMemberRole(id, m.user_id, role); members.reload(); changes.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(""); }
  }
  async function remove(m: Member) {
    const who = m.display_name || m.email || m.subject;
    if (!window.confirm(`Remove ${who} from ${org.name}? Their account stays; they lose access to this organization, and the agents and grants they held here are revoked.`)) return;
    setBusy(m.user_id); setError("");
    try { await api.removeMember(id, m.user_id); members.reload(); invites.reload(); changes.reload(); }
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
                        disabled={busy === m.user_id || !canTouch(m)}
                        title={!canTouch(m) ? "Only an owner can change an owner's or another admin's role." : undefined}
                        onChange={(e) => void changeRole(m, e.target.value as Role)}>
                        {roleOptions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                      </select>
                    </td>
                    <td>{day(m.member_since)}</td>
                    <td>{m.is_active ? "Active" : "Inactive"}</td>
                    <td className="org-actions">
                      <button type="button" className="btn-line dg-mini-btn" disabled={busy === m.user_id || !canTouch(m)}
                        title={!canTouch(m) ? "Only an owner can remove an owner or another admin." : undefined}
                        onClick={() => void remove(m)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="small dim">An organization needs at least one owner. Removing someone revokes the agents they owned here and deletes the desktop and agent grants they held; their account and personal work are untouched.</p>
      </div>

      <div className="profile-card">
        <h2 className="h3">Roles and permissions</h2>
        <p className="small dim">These are the four roles and what each can do. The server checks the role on every request; this table is the explanation, not the enforcement.</p>
        <LoadState loaded={catalog.loaded} reload={catalog.reload} what="roles" />
        {cat && (
          <div className="org-table-wrap">
            <table className="org-table org-matrix">
              <thead>
                <tr><th>Can</th>{cat.roles.map((r) => <th key={r.id} className="org-matrix-role">{r.label}<span className="dim"> · {r.members}</span></th>)}</tr>
              </thead>
              <tbody>
                {cat.capabilities.map((c) => (
                  <tr key={c.id}>
                    <td>{c.label}</td>
                    {cat.roles.map((r) => (
                      <td key={r.id} className="org-matrix-cell" aria-label={`${r.label}: ${r.capabilities.includes(c.id) ? "yes" : "no"}`}>
                        {r.capabilities.includes(c.id) ? "✓" : <span className="dim">·</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cat && (
          <p className="small dim">{cat.roles.map((r) => `${r.label}: ${r.summary}`).join(" ")}</p>
        )}
      </div>

      <form className="profile-card cform" onSubmit={(e) => void create(e)}>
        <h2 className="h3">Invite your team</h2>
        <div className="org-row">
          <div className="cfield"><label htmlFor="inv-label">Label <span className="dim">(optional)</span></label>
            <input id="inv-label" type="text" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="Platform team" /></div>
          <div className="cfield"><label htmlFor="inv-email">Send to <span className="dim">(optional)</span></label>
            <input id="inv-email" type="email" value={email} maxLength={320} onChange={(e) => setEmail(e.target.value)} placeholder="teammate@company.com" autoComplete="off" /></div>
          <div className="cfield"><label htmlFor="inv-role">They join as</label>
            <select id="inv-role" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as Role)}>
              {roleOptions.map((r) => <option key={r.id} value={r.id}>{r.label} — {r.why}</option>)}
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
            {made.email && (
              <p className="small dim">
                {made.email_sent
                  ? `Sent to ${made.email}. The code is still shown here once in case it does not arrive.`
                  : `Email to ${made.email} was not sent (email delivery is not configured, or the provider refused it). Copy the link and send it yourself.`}
              </p>
            )}
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
                <thead><tr><th>Label</th><th>Role</th><th>Sent to</th><th>Created</th><th>Expires</th><th className="num">Used</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {invites.loaded.data.map((i) => (
                    <tr key={i.id}>
                      <td>{i.label || <span className="dim">No label</span>}</td><td>{i.role}</td>
                      <td>{i.email ? <>{i.email}{i.email_sent_at ? <span className="dim"> · sent</span> : <span className="dim"> · not sent</span>}</> : <span className="dim">—</span>}</td>
                      <td>{day(i.created_at)}</td><td>{day(i.expires_at)}</td><td className="num">{i.uses}/{i.max_uses}</td>
                      <td>{i.revoked_at ? "Revoked" : i.active ? "Active" : "Expired or used up"}</td>
                      <td>{i.active && <button type="button" className="btn-line" disabled={busy === i.id} onClick={() => void revoke(i.id)}>Revoke</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>

      <div className="profile-card">
        <h2 className="h3">Recent admin changes</h2>
        <p className="small dim">The last 30 days of membership, invite, agent, approval and policy changes, newest first. The same rows are in the audit chain.</p>
        <LoadState loaded={changes.loaded} reload={changes.reload} what="admin changes" />
        {changes.loaded.state === "ok" && (() => {
          const events = changes.loaded.data.events.filter((e) => ADMIN_CHANGE.test(e.action));
          return events.length === 0
            ? <p className="small dim">No admin changes in the last 30 days.</p>
            : (
              <div className="org-table-wrap">
                <table className="org-table">
                  <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Object</th></tr></thead>
                  <tbody>
                    {events.map((e) => (
                      <tr key={e.id}>
                        <td>{when(e.t_created)}</td><td>{e.action}</td><td>{e.actor_subject}</td>
                        <td>{e.object_type}{e.object_id ? <span className="dim"> · {e.object_id.slice(0, 8)}</span> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        })()}
      </div>
    </div>
  );
}
