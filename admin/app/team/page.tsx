"use client";
import { useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { createInvite, getInvites, revokeInvite, type CreatedInvite, type Invite } from "@/lib/onboarding-api";
import { getMembers, type ApiState, type Member, type Org } from "@/lib/org-api";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "");

/** The team for the organisation picked in the selector: who is in it, and the invitations that are out. */
export default function Team() {
  return (
    <>
      <Hero marker="TEAM" title="Team" lead="Who can sign in to this organisation and what they can do. Invite people with a link; remove an invitation to stop it working." />
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
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [label, setLabel] = useState("");
  const [days, setDays] = useState("7");
  const [uses, setUses] = useState("10");
  const [msg, setMsg] = useState("");
  const load = useCallback(() => { void getMembers(id).then(setMembers); void getInvites(id).then(setInvites); }, [id]);
  useEffect(() => load(), [load]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    const r = await createInvite(id, { label: label.trim() || undefined, days: Number(days), max_uses: Number(uses) });
    if (r.kind === "ok") { setCreated(r.data); setLabel(""); load(); } else setMsg(r.kind === "error" ? r.message : `Not created (${r.kind}).`);
  }
  async function revoke(inv: Invite) {
    if (!window.confirm("Stop this invitation from working?")) return;
    const r = await revokeInvite(id, inv.id);
    setMsg(r.kind === "ok" ? "Invitation removed." : r.kind === "error" ? r.message : `Not removed (${r.kind}).`);
    load();
  }

  return (
    <div style={{ gridColumn: "1 / -1", display: "grid", gap: 32 }}>
      <div>
        <h2 className="h3">Members of {org.name}</h2>
        {members.kind !== "ok" ? <AdminNotice state={members} /> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Since</th><th>Status</th></tr></thead>
            <tbody>{members.data.members.map((m) => (
              <tr key={m.user_id}><td>{m.display_name ?? "—"}</td><td>{m.email ?? "—"}</td><td>{m.roles.join(", ")}</td><td>{when(m.member_since)}</td><td>{m.is_active ? "Active" : "Deactivated"}</td></tr>
            ))}</tbody>
          </table></div>
        )}
        <p className="small dim" style={{ marginTop: 10 }}>Changing a role, deactivating someone and transferring ownership are not built yet. For now, remove their access with the policy kill switch or ask the platform team.</p>
      </div>
      <div>
        <h2 className="h3">Invite someone</h2>
        <form className="cform profile-card" onSubmit={invite} style={{ maxWidth: 560 }}>
          <div className="cfield"><label htmlFor="i-label">Label <span className="dim">(optional, for you)</span></label><input id="i-label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} /></div>
          <div className="cfield"><label htmlFor="i-days">Works for (days)</label><input id="i-days" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} /></div>
          <div className="cfield"><label htmlFor="i-uses">Can be used (times)</label><input id="i-uses" inputMode="numeric" value={uses} onChange={(e) => setUses(e.target.value)} /></div>
          <div className="cluster"><button className="btn-ink" type="submit">Create invitation</button></div>
          {created && <p className="small" role="status">Invitation code: <code>{created.code}</code>. Share it only with the people you mean to invite; it is shown once.</p>}
          {msg && <p className="small" role="status">{msg}</p>}
        </form>
      </div>
      <div>
        <h2 className="h3">Invitations</h2>
        {invites.kind !== "ok" ? <AdminNotice state={invites} /> : invites.data.length === 0 ? <div className="empty"><b>No invitations.</b></div> : (
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Label</th><th>Created</th><th>Expires</th><th>Used</th><th>Status</th><th /></tr></thead>
            <tbody>{invites.data.map((i) => (
              <tr key={i.id}><td>{i.label ?? "—"}</td><td>{when(i.created_at)}</td><td>{when(i.expires_at)}</td><td>{i.uses} of {i.max_uses}</td><td>{i.revoked_at ? "Removed" : i.active ? "Active" : "Expired"}</td>
                <td>{i.active && <button type="button" className="btn-line" onClick={() => void revoke(i)}>Remove</button>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
