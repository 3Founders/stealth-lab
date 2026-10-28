"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { monthRange, nameFor, totalsByUser, usd, usedShare, type OrgPolicy } from "@/lib/org-admin";

const NEXT_STEP: Record<string, string> = {
  connect_key: "Connect your provider key under Provider keys.",
  set_policy: "Set the policy: which models and data types are allowed, and the budgets.",
  invite_developers: "Invite your team under Members.",
  waiting_for_developers: "Invites are out. Waiting for your team to join.",
  running: "Everything is set up and running.",
};

/** Organization home: this month's spend against the cap, what each person has used, and the stop button. */
export default function OrgHome() {
  const { org, admin } = useOrg();
  return admin ? <AdminHome /> : <MemberHome key={org.organization_id} />;
}

function MemberHome() {
  const { api, org } = useOrg();
  const cfg = useLoad(() => api.clientConfig(org.organization_id), org.organization_id);
  return (
    <div className="org-stack">
      <p className="small dim">You&rsquo;re a {org.role} of {org.name}. Spend, members and policy are visible to owners and admins.</p>
      <LoadState loaded={cfg.loaded} reload={cfg.reload} what="your connection details" />
      {cfg.loaded.state === "ok" && (
        <div className="profile-card">
          <h2 className="h3">Connect your machine</h2>
          <dl className="org-dl">
            <dt>Address</dt><dd><code>{cfg.loaded.data.gateway}</code></dd>
            <dt>Models</dt><dd>{cfg.loaded.data.models.length ? cfg.loaded.data.models.map((m) => <code key={m} className="org-chip">{m}</code>) : "None set up yet."}</dd>
          </dl>
          <p className="small dim">Sign in with your keळ account when your tool asks. Nothing secret is on this page.</p>
        </div>
      )}
    </div>
  );
}

function AdminHome() {
  const { api, org } = useOrg();
  const id = org.organization_id;
  const range = monthRange();
  const budget = useLoad(() => api.budget(id), `b${id}`);
  const usage = useLoad(() => api.usageByUser(id, range.since, range.until), `u${id}`);
  const members = useLoad(() => api.members(id), `m${id}`);
  const onboarding = useLoad(() => api.onboarding(id), `o${id}`);
  const [policy, setPolicy] = useState<OrgPolicy | null>(null);
  const stopped = policy?.kill_switch ?? (budget.loaded.state === "ok" ? budget.loaded.data.kill_switch : onboarding.loaded.state === "ok" ? onboarding.loaded.data.kill_switch : false);

  const noPolicy = budget.loaded.state === "error" && budget.loaded.status === 409;
  const people = usage.loaded.state === "ok" ? totalsByUser(usage.loaded.data) : [];
  const names = members.loaded.state === "ok" ? members.loaded.data : [];

  return (
    <div className="org-stack">
      {stopped && <div className="org-banner" role="status"><b>This organization is stopped.</b> No model calls are allowed until an admin turns it back on.</div>}

      {onboarding.loaded.state === "ok" && onboarding.loaded.data.next_step !== "running" && (
        <p className="org-next"><b>Next:</b> {NEXT_STEP[onboarding.loaded.data.next_step]}</p>
      )}

      <div className="profile-card">
        <h2 className="h3">This month</h2>
        {noPolicy && <p>No policy is set yet, so there is no budget to measure against. <Link href="/org/policy" style={{ textDecoration: "underline" }}>Set the policy</Link>.</p>}
        {!noPolicy && <LoadState loaded={budget.loaded} reload={budget.reload} what="the budget" />}
        {budget.loaded.state === "ok" && (() => {
          const m = budget.loaded.data.monthly;
          const share = usedShare(m.used_usd, m.held_usd, m.budget_usd);
          return (
            <>
              <p className="org-big">{usd(m.used_usd)} <span className="dim">of {usd(m.budget_usd)}</span></p>
              <div className="org-bar-track" role="progressbar" aria-label="Spend against the monthly cap" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share ?? 0)}>
                <div className="org-bar-fill" data-high={share !== null && share >= 90} style={{ width: `${share ?? 0}%` }} />
              </div>
              <p className="small dim">
                {share === null ? "The monthly budget is zero, so calls are refused." : `${usd(m.remaining_usd)} left`}
                {Number(m.held_usd) > 0 && ` · ${usd(m.held_usd)} held by calls in progress`}. Counted from {new Date(budget.loaded.data.month_start).toISOString().slice(0, 10)} (UTC).
              </p>
            </>
          );
        })()}
      </div>

      <div className="profile-card">
        <h2 className="h3">Each person this month</h2>
        <LoadState loaded={usage.loaded} reload={usage.reload} what="usage" />
        {usage.loaded.state === "ok" && (people.length === 0
          ? <p className="small dim">No calls yet this month.</p>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>Person</th><th className="num">Calls</th><th className="num">Failed</th><th className="num">Tokens</th><th className="num">Spend</th></tr></thead>
                <tbody>
                  {people.map((p) => (
                    <tr key={p.user}><td>{nameFor(p.user, names)}</td><td className="num">{p.calls}</td><td className="num">{p.failed}</td><td className="num">{p.tokens.toLocaleString("en-US")}</td><td className="num">{usd(p.cost)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </div>

      <KillSwitch stopped={stopped} onChange={(p) => { setPolicy(p); budget.reload(); }} />
    </div>
  );
}

/** The off button. Turning it on or off asks for a reason and a second click, because it stops everyone at once. */
function KillSwitch({ stopped, onChange }: { stopped: boolean; onChange: (p: OrgPolicy) => void }) {
  const { api, org } = useOrg();
  const dialog = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (!stopped) setError(""); }, [stopped]);

  const open = () => { setReason(""); setError(""); dialog.current?.showModal(); };
  const close = () => dialog.current?.close();
  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) { setError("Say why, so the audit log explains it."); return; }
    setBusy(true); setError("");
    try { onChange(await api.killSwitch(org.organization_id, !stopped, reason.trim())); close(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(false); }
  }

  return (
    <div className="profile-card org-danger">
      <h2 className="h3">{stopped ? "Turn the organization back on" : "Stop everything"}</h2>
      <p className="small dim">{stopped ? "Model calls are refused for everyone in the organization." : "Immediately refuses every model call for everyone in the organization. You can turn it back on at any time."}</p>
      <div className="cluster"><button type="button" className={stopped ? "btn-ink" : "btn-line org-stop"} onClick={open}><span>{stopped ? "Turn on" : "Stop everything"}</span></button></div>
      <dialog ref={dialog} className="org-dialog" aria-labelledby="ks-title" onClose={() => setBusy(false)}>
        <form onSubmit={(e) => void confirm(e)} className="org-stack">
          <h2 id="ks-title" className="h3">{stopped ? "Turn the organization back on?" : `Stop ${org.name}?`}</h2>
          <p>{stopped ? "Members will be able to make model calls again, within the policy." : "Every model call from every member will be refused until you turn it back on."}</p>
          <div className="cfield"><label htmlFor="ks-reason">Reason (kept in the audit log)</label>
            <input id="ks-reason" type="text" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} autoComplete="off" /></div>
          {error && <p className="small" role="alert">{error}</p>}
          <div className="cluster">
            <button type="submit" className={stopped ? "btn-ink" : "btn-ink org-stop-solid"} disabled={busy}><span>{busy ? "Working…" : stopped ? "Turn on" : "Stop everything"}</span></button>
            <button type="button" className="btn-line" onClick={close}>Cancel</button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
