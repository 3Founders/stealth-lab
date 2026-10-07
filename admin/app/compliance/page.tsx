"use client";
import { useState } from "react";
import { Hero, str } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { approveErasure, executeErasure, placeLegalHold, releaseLegalHold, requestErasure, type ApiState, type ErasureResult, type Org } from "@/lib/org-api";

function errText(r: ApiState<unknown>): string {
  return r.kind === "error" ? r.message : r.kind === "forbidden" ? "The backend refused this: it needs the owner role." : `Not applied (${r.kind}).`;
}

/** The API returns the new record; its id key is `id` (holds) or `id`/`request_id` (requests). */
const idOf = (d: Record<string, unknown>) => str(d.id ?? d.request_id ?? d.hold_id);

function CompliancePanel({ org }: { org: Org }) {
  const orgId = org.organization_id;
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [holdReason, setHoldReason] = useState("");
  const [holdId, setHoldId] = useState("");
  const [erasureReason, setErasureReason] = useState("");
  const [requestId, setRequestId] = useState("");
  const [result, setResult] = useState<ErasureResult | null>(null);

  async function hold(e: React.FormEvent) {
    e.preventDefault();
    if (!window.confirm("Place a legal hold? It blocks erasure for this organisation until released.")) return;
    setBusy(true);
    const r = await placeLegalHold(orgId, holdReason.trim());
    setBusy(false);
    if (r.kind === "ok") { const id = idOf(r.data); setHoldId(id); setHoldReason(""); setMsg(`Legal hold placed${id ? `: ${id}` : ""}. Keep this id; there is no list endpoint yet.`); }
    else setMsg(errText(r));
  }

  async function release() {
    if (!holdId.trim() || !window.confirm("Release this legal hold?")) return;
    setBusy(true);
    const r = await releaseLegalHold(orgId, holdId.trim());
    setBusy(false);
    setMsg(r.kind === "ok" ? "Legal hold released." : errText(r));
  }

  async function request(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await requestErasure(orgId, erasureReason.trim());
    setBusy(false);
    if (r.kind === "ok") { const id = idOf(r.data); setRequestId(id); setErasureReason(""); setMsg(`Erasure requested${id ? `: ${id}` : ""}. A different owner must approve it before it can run.`); }
    else setMsg(errText(r));
  }

  async function approve() {
    if (!requestId.trim() || !window.confirm("Approve this erasure request? You cannot approve a request you opened.")) return;
    setBusy(true);
    const r = await approveErasure(orgId, requestId.trim());
    setBusy(false);
    setMsg(r.kind === "ok" ? "Approved." : errText(r));
  }

  async function execute() {
    if (!requestId.trim() || !window.confirm("Execute this erasure now? This is irreversible for whatever it is able to erase.")) return;
    setBusy(true);
    const r = await executeErasure(orgId, requestId.trim());
    setBusy(false);
    if (r.kind === "ok") { setResult(r.data); setMsg(r.data.completed ? "Erasure completed." : "Erasure did NOT complete. See what was blocked below."); }
    else setMsg(errText(r));
  }

  return (
    <>
      {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}

      <div className="panel" style={{ maxWidth: 720 }}>
        <h2 className="h3">Legal hold</h2>
        <p className="small dim">While a hold is in place, erasure is refused. Placing and releasing are audited.</p>
        <form className="cform wide" onSubmit={hold}>
          <div className="cfield"><label htmlFor="hr">Reason</label><textarea id="hr" rows={2} value={holdReason} onChange={(e) => setHoldReason(e.target.value)} required /></div>
          <div><button type="submit" className="btn-line" disabled={busy || !holdReason.trim()}>Place hold</button></div>
        </form>
        <div className="cfield"><label htmlFor="hid">Hold id</label><input id="hid" type="text" value={holdId} onChange={(e) => setHoldId(e.target.value)} placeholder="id returned when the hold was placed" /></div>
        <div className="row-actions"><button type="button" className="btn-line" disabled={busy || !holdId.trim()} onClick={release}>Release hold</button></div>
      </div>

      <div className="panel" style={{ maxWidth: 720 }}>
        <h2 className="h3">Erasure (two owners)</h2>
        <p className="small dim">One owner requests, a <b>different</b> owner approves, then it can be executed. Today core tables cannot be erased yet, so a run reports <code>completed: false</code> with the manifest of what it did and what it could not do.</p>
        <form className="cform wide" onSubmit={request}>
          <div className="cfield"><label htmlFor="er">Reason for the request</label><textarea id="er" rows={2} value={erasureReason} onChange={(e) => setErasureReason(e.target.value)} required /></div>
          <div><button type="submit" className="btn-line" disabled={busy || !erasureReason.trim()}>Request erasure</button></div>
        </form>
        <div className="cfield"><label htmlFor="rid">Request id</label><input id="rid" type="text" value={requestId} onChange={(e) => setRequestId(e.target.value)} placeholder="id returned when the request was opened" /></div>
        <div className="row-actions">
          <button type="button" className="btn-line" disabled={busy || !requestId.trim()} onClick={approve}>Approve (as second owner)</button>
          <button type="button" className="btn-line danger" disabled={busy || !requestId.trim()} onClick={execute}>Execute</button>
        </div>
        {result && (
          <div className="log" style={{ marginTop: 8 }}>
            <div><span>Completed</span><span>{result.completed ? "Yes" : "No"}</span></div>
            <div><span>Blocked</span><span>{result.blocked.length === 0 ? "Nothing" : <span className="mono small">{JSON.stringify(result.blocked, null, 2)}</span>}</span></div>
            <div><span>Manifest</span><span className="mono small" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(result.manifest, null, 2)}</span></div>
          </div>
        )}
      </div>
    </>
  );
}

export default function CompliancePage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Compliance" lead="Legal holds and the two-owner erasure workflow. Owners only." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="owner">{(org) => <CompliancePanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
