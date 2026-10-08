"use client";
import { useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import { ComingCard } from "@/components/ExecWidgets";
import OrgGate from "@/components/OrgGate";
import { getPolicy, parseList, putPolicy, setKillSwitch, type ApiState, type Org, type OrgPolicy } from "@/lib/org-api";

function errText(r: ApiState<unknown>): string {
  return r.kind === "error" ? r.message : r.kind === "forbidden" ? "The backend refused this: your role can't do it." : `Not applied (${r.kind}).`;
}

function PolicyPanel({ org }: { org: Org }) {
  const [state, setState] = useState<ApiState<OrgPolicy>>({ kind: "loading" });
  const [providers, setProviders] = useState("");
  const [models, setModels] = useState("");
  const [tools, setTools] = useState("");
  const [classes, setClasses] = useState("");
  const [monthly, setMonthly] = useState("");
  const [daily, setDaily] = useState("");
  const [msg, setMsg] = useState("");
  const [killReason, setKillReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    setState({ kind: "loading" });
    const r = await getPolicy(org.organization_id, signal);
    setState(r);
    if (r.kind === "ok") {
      setProviders(r.data.allowed_providers.join("\n"));
      setModels(r.data.allowed_models.join("\n"));
      setTools(r.data.allowed_tools.join("\n"));
      setClasses(r.data.allowed_data_classes.join("\n"));
      setMonthly(String(r.data.monthly_budget_usd));
      setDaily(String(r.data.per_user_daily_budget_usd));
    }
  }, [org.organization_id]);

  useEffect(() => {
    const ac = new AbortController();
    load(ac.signal);
    return () => ac.abort();
  }, [load]);

  // The backend answers 409 until a policy exists (it has no defaults: an empty list allows nothing, 0 spends nothing).
  const noPolicyYet = state.kind === "error" && state.status === 409;
  const policy = state.kind === "ok" ? state.data : null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!window.confirm(policy ? "Save this policy? It takes effect immediately for the whole organisation." : "Create the policy? Until it exists, nothing is allowed.")) return;
    setBusy(true);
    const r = await putPolicy(org.organization_id, {
      allowed_providers: parseList(providers), allowed_models: parseList(models), allowed_tools: parseList(tools),
      allowed_data_classes: parseList(classes), monthly_budget_usd: monthly.trim(), per_user_daily_budget_usd: daily.trim(),
    }, policy?.version);
    setBusy(false);
    if (r.kind === "ok") { setMsg(`Saved (version ${r.data.version}).`); setState(r); }
    else setMsg(r.kind === "error" && r.status === 409 && policy ? "Someone else changed the policy since you loaded it. Reload and re-apply your edit." : errText(r));
  }

  async function toggleKill(on: boolean) {
    if (!killReason.trim()) { setMsg("A reason is required."); return; }
    if (!window.confirm(on ? "Stop this organisation? Every model call it makes will be refused until you turn this off." : "Turn the kill switch off and let the organisation spend again?")) return;
    setBusy(true);
    const r = await setKillSwitch(org.organization_id, on, killReason.trim());
    setBusy(false);
    if (r.kind === "ok") { setMsg(on ? "Kill switch ON: the organisation is stopped." : "Kill switch off."); setState(r); setKillReason(""); }
    else setMsg(errText(r));
  }

  if (state.kind !== "ok" && !noPolicyYet) return <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div>;

  return (
    <>
      {policy && (
        <div className="stats">
          <Stat label="Kill switch" value={policy.kill_switch ? "ON" : "Off"} bad={policy.kill_switch} note={policy.kill_switch ? policy.kill_switch_reason ?? undefined : "Organisation may call models"} />
          <Stat label="Monthly budget" value={`$${policy.monthly_budget_usd}`} />
          <Stat label="Per-user daily" value={`$${policy.per_user_daily_budget_usd}`} />
          <Stat label="Policy version" value={policy.version} />
        </div>
      )}
      {noPolicyYet && (
        <div className="empty" style={{ gridColumn: "1 / span 12" }}>
          <b>No policy yet for {org.name}.</b>
          <p>There are no defaults: with no policy, nothing is allowed and nothing is spent. Fill in every field below to create one.</p>
        </div>
      )}
      {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}

      {policy && (
        <div className="panel">
          <h2 className="h3">Kill switch</h2>
          <p className="small dim">Immediately refuses every model call for this organisation. Reversible; both directions are audited with your reason.</p>
          <div className="cfield" style={{ maxWidth: 560 }}>
            <label htmlFor="kr">Reason</label>
            <input id="kr" type="text" value={killReason} onChange={(e) => setKillReason(e.target.value)} placeholder="Recorded in the audit log" />
          </div>
          <div className="row-actions">
            {policy.kill_switch
              ? <button type="button" className="btn-line" disabled={busy} onClick={() => toggleKill(false)}>Turn off</button>
              : <button type="button" className="btn-line danger" disabled={busy} onClick={() => toggleKill(true)}>Stop the organisation</button>}
          </div>
        </div>
      )}

      <form className="cform wide" onSubmit={save} style={{ maxWidth: 720 }}>
        <h2 className="h3">Policy</h2>
        <p className="small dim">One entry per line or comma-separated. An empty list allows nothing, and there are no wildcards.</p>
        <div className="cfield"><label htmlFor="pp">Allowed providers</label><textarea id="pp" rows={3} value={providers} onChange={(e) => setProviders(e.target.value)} /></div>
        <div className="cfield"><label htmlFor="pm">Allowed models</label><textarea id="pm" rows={3} value={models} onChange={(e) => setModels(e.target.value)} /></div>
        <div className="cfield"><label htmlFor="pt">Allowed tools</label><textarea id="pt" rows={3} value={tools} onChange={(e) => setTools(e.target.value)} /></div>
        <div className="cfield"><label htmlFor="pd">Allowed data classes</label><textarea id="pd" rows={3} value={classes} onChange={(e) => setClasses(e.target.value)} /></div>
        <div className="cfield"><label htmlFor="mb">Monthly budget (USD)</label><input id="mb" type="text" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} required placeholder="0 means no spend" /></div>
        <div className="cfield"><label htmlFor="db">Per-user daily budget (USD)</label><input id="db" type="text" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} required placeholder="0 means no spend" /></div>
        <div><button type="submit" className="btn-ink" disabled={busy}>{busy ? "Saving…" : policy ? "Save policy" : "Create policy"}</button></div>
      </form>

      <div className="panel" style={{ maxWidth: 720 }}>
        <ComingCard title="Model catalog and policy rules">
          Allow models by rule (provider, family, price ceiling, data class, region, open weights) instead of listing each one, with new models included automatically or held for approval. Until then, this page uses the explicit lists above.
        </ComingCard>
        <ComingCard title="Home region">Choose where the organisation&rsquo;s data is kept.</ComingCard>
        <ComingCard title="Proposals and approval">Policy and budget changes will become proposals that an executive approves. Today an owner or admin changes them directly.</ComingCard>
      </div>
    </>
  );
}

export default function PolicyPage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Policy" lead="What this organisation's agents may use and spend, plus the emergency stop." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <PolicyPanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
