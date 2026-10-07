"use client";
import { useState } from "react";
import { Hero } from "@/components/Admin";
import { moderateWay } from "@/lib/admin-api";

export default function Moderation() {
  const [id, setId] = useState("");
  const [action, setAction] = useState<"hide" | "restore" | "remove">("hide");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (action === "remove" && !window.confirm("Remove this way? Nothing is deleted, but it leaves public view.")) return;
    setBusy(true);
    const r = await moderateWay(id.trim(), action, reason.trim());
    setBusy(false);
    setMsg(
      r.kind === "ok" ? `Done: ${action} ${id.trim().slice(0, 8)}.`
        : r.kind === "forbidden" ? "This account is not an admin."
        : r.kind === "unauthenticated" ? "Sign in first."
        : r.kind === "error" ? r.message
        : `Not applied (${r.kind}).`,
    );
    if (r.kind === "ok") setReason("");
  }

  return (
    <>
      <Hero marker="MODERATION" title="Moderation" lead="Hide, restore or remove a published way. Every action is audited with your account and the reason." />
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <form className="cform" onSubmit={submit}>
          <div className="cfield">
            <label htmlFor="rid">Procedure row id</label>
            <input id="rid" type="text" value={id} onChange={(e) => setId(e.target.value)} required placeholder="uuid of the way (procedure row)" />
          </div>
          <div className="cfield">
            <label htmlFor="act">Action</label>
            <select id="act" value={action} onChange={(e) => setAction(e.target.value as typeof action)}>
              <option value="hide">Hide (reversible)</option>
              <option value="restore">Restore</option>
              <option value="remove">Remove</option>
            </select>
          </div>
          <div className="cfield">
            <label htmlFor="why">Reason</label>
            <textarea id="why" value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={2000} placeholder="Recorded in the audit trail." />
          </div>
          <div><button type="submit" className="btn-ink" disabled={busy || !id.trim() || !reason.trim()}>{busy ? "Applying…" : "Apply"}</button></div>
          {msg && <p className="notice-ok" role="status">{msg}</p>}
        </form>
      </section>
    </>
  );
}
