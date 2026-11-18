"use client";
import { useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import {
  createInvite, getInvites, getRoles, removeMember, revokeInvite, setMemberRole,
  type CreatedInvite, type Invite, type InviteRole, type RoleCatalog,
} from "@/lib/onboarding-api";
import { getAuditRecent, getMembers, type ApiState, type AuditRecent, type Member, type Org } from "@/lib/org-api";
import { lastDays } from "@/lib/range";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "");
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const ADMIN_CHANGE = /^(org_|tool_approval\.)/;
const ROLES: Array<{ id: InviteRole; label: string; why: string }> = [
  { id: "owner", label: "Owner", why: "Everything, including billing and other owners." },
  { id: "admin", label: "Admin", why: "Policy, keys, members and agents; not billing or ownership." },
  { id: "member", label: "Member", why: "Uses the organization's tools and rooms." },
  { id: "viewer", label: "Viewer", why: "Read-only access to shared work." },
];

/** The team for the organisation picked in the selector: who is in it, what each role can do, the invitations that are
 *  out, and the recent membership changes. Role changes and removal use the same routes as the member-facing console. */
export default function Team() {
  return (
    <>
      <Hero marker="TEAM" title="Team" lead="Who can sign in to this organisation, what each role can do, and the invitations that are out. Change a role, remove someone, or invite a person with a link." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <TeamFor org={org} />}</OrgGate>
      </section>
    </>
  );
}

function TeamFor({ org }: { org: Org }) {
  const id = org.organization_id;
  const [members, setMembers] = useState<ApiState<{ members: Member[] }>>({ kind: "loading" });
  const [invites, setInvites] = useState<ApiState<Invite[]>>({ kind: "loading" });
  const [roles, setRoles] = useState<ApiState<RoleCatalog>>({ kind: "loading" });
  const [changes, setChanges] = useState<ApiState<AuditRecent>>({ kind: "loading" });
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [label, setLabel] = useState("");
  const [email, setEmail] = useState("");
  const [days, setDays] = useState("7");
  const [uses, setUses] = useState("10");
  const [inviteRole, setInviteRole] = useState<InviteRole>("member");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    void getMembers(id).then(setMembers);
    void getInvites(id).then(setInvites);
    void getRoles(id).then(setRoles);
    const r = lastDays(30);
    void getAuditRecent(id, r.since, r.until, 20).then(setChanges);
  }, [id]);
  useEffect(() => load(), [load]);

  const owner = org.role === "owner";
  const canTouch = (m: Member) => owner || !m.roles.some((r) => r === "owner" || r === "admin");
  const roleOptions = ROLES.filter((r) => r.id !== "owner" || owner);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setMsg(""); setBusy("invite");
    const r = await createInvite(id, {
      label: label.trim() || undefined,
      days: Number(days),
      max_uses: Number(uses),
      role: inviteRole,
      email: email.trim() || undefined,
    });
    setBusy("");
    if (r.kind === "ok") { setCreated(r.data); setLabel(""); setEmail(""); load(); }
    else setMsg(r.kind === "error" ? r.message : `Not created (${r.kind}).`);
  }
  async function revoke(inv: Invite) {
    if (!window.confirm("Stop this invitation from working?")) return;
    setBusy(inv.id);
    const r = await revokeInvite(id, inv.id);
    setBusy("");
    setMsg(r.kind === "ok" ? "Invitation removed." : r.kind === "error" ? r.message : `Not removed (${r.kind}).`);
    load();
  }
  async function changeRole(m: Member, role: InviteRole) {
    setBusy(m.user_id); setMsg("");
    const r = await setMemberRole(id, m.user_id, role);
    setBusy("");
    setMsg(r.kind === "ok" ? "Role changed." : r.kind === "error" ? r.message : `Not changed (${r.kind}).`);
    load();
  }
  async function remove(m: Member) {
    const who = m.display_name || m.email || m.subject;
    if (!window.confirm(`Remove ${who} from ${org.name}? Their account stays; they lose access, and the agents and grants they held here are revoked.`)) return;
    setBusy(m.user_id); setMsg("");
    const r = await removeMember(id, m.user_id);
    setBusy("");
    setMsg(r.kind === "ok" ? "Member removed." : r.kind === "error" ? r.message : `Not removed (${r.kind}).`);
    load();
  }

  const events = changes.kind === "ok" ? changes.data.events.filter((e) => ADMIN_CHANGE.test(e.action)) : [];

  return (
    <div style={{ gridColumn: "1 / -1", display: "grid", gap: 32 }}>
      {msg && <p className="small" role="status" style={{ gridColumn: "1 / -1" }}>{msg}</p>}

      <div>
        <h2 className="h3">Members of {org.name}</h2>
        {members.kind !== "ok" ? <AdminNotice state={members} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Since</th><th>Status</th><th /></tr></thead>
            <tbody>{members.data.members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.display_name ?? "—"}</td><td>{m.email ?? "—"}</td>
                <td>
                  <select aria-label={`Role for ${m.display_name || m.email || m.subject}`} value={m.roles[0] ?? "member"}
                    disabled={busy === m.user_id || !canTouch(m)}
                    title={!canTouch(m) ? "Only an owner can change an owner's or another admin's role." : undefined}
                    onChange={(e) => void changeRole(m, e.target.value as InviteRole)}>
                    {roleOptions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                  </select>
                </td>
                <td>{when(m.member_since)}</td><td>{m.is_active ? "Active" : "Deactivated"}</td>
                <td>{canTouch(m) && <button type="button" className="btn-line" disabled={busy === m.user_id} onClick={() => void remove(m)}>Remove</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <p className="small dim" style={{ marginTop: 10 }}>An organization needs at least one owner. Removing someone revokes the agents they owned here and deletes the desktop and agent grants they held; their account and personal work are untouched.</p>
      </div>

      <div>
        <h2 className="h3">Roles and permissions</h2>
        <p className="small dim">The server checks the role on every request; this table is the explanation, not the enforcement.</p>
        {roles.kind !== "ok" ? <AdminNotice state={roles} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Can</th>{roles.data.roles.map((r) => <th key={r.id}>{r.label} <span className="dim">· {r.members}</span></th>)}</tr></thead>
            <tbody>{roles.data.capabilities.map((c) => (
              <tr key={c.id}><td>{c.label}</td>{roles.data.roles.map((r) => (
                <td key={r.id} aria-label={`${r.label}: ${r.capabilities.includes(c.id) ? "yes" : "no"}`}>{r.capabilities.includes(c.id) ? "✓" : <span className="dim">·</span>}</td>
              ))}</tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div>
        <h2 className="h3">Invite someone</h2>
        <form className="cform profile-card" onSubmit={invite} style={{ maxWidth: 620 }}>
          <div className="cfield"><label htmlFor="i-label">Label <span className="dim">(optional, for you)</span></label><input id="i-label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} /></div>
          <div className="cfield"><label htmlFor="i-email">Send to <span className="dim">(optional)</span></label><input id="i-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={320} autoComplete="off" /></div>
          <div className="cfield"><label htmlFor="i-role">They join as</label><select id="i-role" value={inviteRole} onChange={(e) => setInviteRole(e.target.value as InviteRole)}>
            {roleOptions.map((r) => <option key={r.id} value={r.id}>{r.label} — {r.why}</option>)}
          </select></div>
          <div className="cfield"><label htmlFor="i-days">Works for (days)</label><input id="i-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} /></div>
          <div className="cfield"><label htmlFor="i-uses">Can be used (times)</label><input id="i-uses" inputMode="numeric" value={uses} onChange={(e) => setUses(e.target.value)} /></div>
          <div className="cluster"><button className="btn-ink" type="submit" disabled={busy === "invite"}>{busy === "invite" ? "Creating…" : "Create invitation"}</button></div>
          {created && (
            <p className="small" role="status">
              Invitation code: <code>{created.code}</code>. Share it only with the people you mean to invite; it is shown once.
              {created.email && (created.email_sent
                ? ` Sent to ${created.email}.`
                : ` Email to ${created.email} was not sent; copy the code and send it yourself.`)}
            </p>
          )}
        </form>
      </div>

      <div>
        <h2 className="h3">Invitations</h2>
        {invites.kind !== "ok" ? <AdminNotice state={invites} /> : invites.data.length === 0 ? <div className="empty"><b>No invitations.</b></div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Label</th><th>Role</th><th>Sent to</th><th>Created</th><th>Expires</th><th>Used</th><th>Status</th><th /></tr></thead>
            <tbody>{invites.data.map((i) => (
              <tr key={i.id}>
                <td>{i.label ?? "—"}</td><td>{i.role}</td>
                <td>{i.email ? <>{i.email}{i.email_sent_at ? <span className="dim"> · sent</span> : <span className="dim"> · not sent</span>}</> : "—"}</td>
                <td>{when(i.created_at)}</td><td>{when(i.expires_at)}</td><td>{i.uses} of {i.max_uses}</td>
                <td>{i.revoked_at ? "Removed" : i.active ? "Active" : "Expired"}</td>
                <td>{i.active && <button type="button" className="btn-line" disabled={busy === i.id} onClick={() => void revoke(i)}>Remove</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div>
        <h2 className="h3">Recent membership changes</h2>
        <p className="small dim">The last 30 days of membership, invite, agent, approval and policy changes from the audit chain.</p>
        {changes.kind !== "ok" ? <AdminNotice state={changes} /> : events.length === 0 ? <div className="empty"><b>No admin changes in the last 30 days.</b></div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>Object</th></tr></thead>
            <tbody>{events.map((e) => (
              <tr key={e.id}><td>{day(e.t_created)}</td><td>{e.action}</td><td>{e.actor_subject}</td><td>{e.object_type}{e.object_id ? <span className="dim"> · {e.object_id.slice(0, 8)}</span> : null}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
