"use client";
import { useEffect, useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { KNOWN_DATA_CLASSES, PUBLIC_DATA_CLASSES, isPublicOnly, listFromText, usd, type OrgPolicy, type PolicyChanges } from "@/lib/org-admin";

const same = (a: string[], b: string[]) => a.length === b.length && [...a].sort().join("|") === [...b].sort().join("|");

/** What the organization may call and spend: models, data types, and the monthly and per-person daily budgets. */
export default function PolicyPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const policy = useLoad(() => api.policy(id), id);
  if (!admin) return <div className="empty"><b>The policy is visible to owners and admins.</b></div>;
  const none = policy.loaded.state === "error" && policy.loaded.status === 409;
  return (
    <div className="org-stack">
      {!none && <LoadState loaded={policy.loaded} reload={policy.reload} what="the policy" />}
      {none && <Setup onDone={policy.reload} />}
      {policy.loaded.state === "ok" && <Editor key={policy.loaded.data.version} policy={policy.loaded.data} onSaved={policy.reload} />}
    </div>
  );
}

/** No policy yet: build one from the connected keys and two budgets. */
function Setup({ onDone }: { onDone: () => void }) {
  const { api, org } = useOrg();
  const [monthly, setMonthly] = useState("");
  const [daily, setDaily] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try { await api.recommendedPolicy(org.organization_id, monthly, daily); onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(false); }
  }
  return (
    <form className="profile-card cform" onSubmit={(e) => void go(e)}>
      <h2 className="h3">Set up your policy</h2>
      <p>There is no policy yet, so every call is refused. Connect a provider key first, then set the two budgets: the policy is built from your connected keys.</p>
      <div className="org-row">
        <div className="cfield"><label htmlFor="rec-m">Monthly budget (USD)</label><input id="rec-m" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} placeholder="500" /></div>
        <div className="cfield"><label htmlFor="rec-d">Per person per day (USD)</label><input id="rec-d" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} placeholder="20" /></div>
      </div>
      <div className="cform-submit"><button className="btn-ink" type="submit" disabled={busy || !monthly || !daily}><span>{busy ? "Setting up…" : "Create the policy"}</span></button></div>
      {error && <p className="small" role="alert">{error}</p>}
    </form>
  );
}

function Editor({ policy, onSaved }: { policy: OrgPolicy; onSaved: () => void }) {
  const { api, org } = useOrg();
  const [classes, setClasses] = useState<string[]>(policy.allowed_data_classes);
  const [models, setModels] = useState(policy.allowed_models.join(", "));
  const [providers, setProviders] = useState(policy.allowed_providers.join(", "));
  const [tools, setTools] = useState(policy.allowed_tools.join(", "));
  const [monthly, setMonthly] = useState(policy.monthly_budget_usd);
  const [daily, setDaily] = useState(policy.per_user_daily_budget_usd);
  const [custom, setCustom] = useState(!isPublicOnly(policy.allowed_data_classes));
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => setSaved(false), [classes, models, providers, tools, monthly, daily]);

  const publicOnly = isPublicOnly(classes);
  const changes: PolicyChanges = {};
  if (!same(classes, policy.allowed_data_classes)) changes.allowed_data_classes = classes;
  if (!same(listFromText(models), policy.allowed_models)) changes.allowed_models = listFromText(models);
  if (!same(listFromText(providers), policy.allowed_providers)) changes.allowed_providers = listFromText(providers);
  if (!same(listFromText(tools), policy.allowed_tools)) changes.allowed_tools = listFromText(tools);
  if (Number(monthly) !== Number(policy.monthly_budget_usd)) changes.monthly_budget_usd = monthly;
  if (Number(daily) !== Number(policy.per_user_daily_budget_usd)) changes.per_user_daily_budget_usd = daily;
  const dirty = Object.keys(changes).length > 0;
  const noClasses = classes.length === 0;

  const toggle = (c: string) => setClasses((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!dirty || noClasses) return;
    setBusy(true); setError("");
    try { await api.putPolicy(org.organization_id, changes, policy.version); setSaved(true); onSaved(); }
    catch (err) {
      const status = (err as { status?: number })?.status;
      setError(status === 409 ? "Someone else changed the policy while you were editing. The latest version is loaded: check it and try again." : err instanceof Error ? err.message : "That didn't work.");
      if (status === 409) onSaved();
    } finally { setBusy(false); }
  }

  return (
    <form className="profile-card cform" onSubmit={(e) => void save(e)}>
      <h2 className="h3">Policy <span className="dim">version {policy.version}</span></h2>

      <fieldset className="profile-visibility">
        <legend>What data may go to models</legend>
        <label>
          <input type="checkbox" checked={publicOnly} onChange={(e) => { if (e.target.checked) { setClasses([...PUBLIC_DATA_CLASSES]); setCustom(false); } else { setClasses([]); setCustom(true); } }} />
          <span><b>Public data only.</b> Nothing private leaves: only public material, what is derived from it, and shared procedures.</span>
        </label>
        {!publicOnly && !custom && null}
        {(custom || !publicOnly) && (
          <div className="org-classes">
            {KNOWN_DATA_CLASSES.map((c) => (
              <label key={c.id}><input type="checkbox" checked={classes.includes(c.id)} onChange={() => toggle(c.id)} /> {c.label}{!c.public && <span className="dim"> · private</span>}</label>
            ))}
            {classes.filter((c) => !KNOWN_DATA_CLASSES.some((k) => k.id === c)).map((c) => (
              <label key={c}><input type="checkbox" checked onChange={() => toggle(c)} /> {c}</label>
            ))}
          </div>
        )}
        {noClasses && <p className="small" role="alert">Choose at least one data type, or select public data only.</p>}
      </fieldset>

      <div className="cfield"><label htmlFor="pol-models">Allowed models</label>
        <textarea id="pol-models" rows={2} value={models} onChange={(e) => setModels(e.target.value)} />
        <span className="hint">Separate with commas or spaces.</span></div>
      <div className="cfield"><label htmlFor="pol-providers">Allowed providers</label>
        <textarea id="pol-providers" rows={1} value={providers} onChange={(e) => setProviders(e.target.value)} /></div>
      <div className="cfield"><label htmlFor="pol-tools">Allowed tools</label>
        <textarea id="pol-tools" rows={1} value={tools} onChange={(e) => setTools(e.target.value)} />
        <span className="hint">The website chat uses <code>chat_completions</code>; agents use <code>call_model</code>.</span></div>

      <div className="org-row">
        <div className="cfield"><label htmlFor="pol-m">Monthly budget (USD)</label><input id="pol-m" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} /><span className="hint">Now {usd(policy.monthly_budget_usd)}.</span></div>
        <div className="cfield"><label htmlFor="pol-d">Per person per day (USD)</label><input id="pol-d" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} /><span className="hint">Now {usd(policy.per_user_daily_budget_usd)}.</span></div>
      </div>

      <div className="cform-submit">
        <button className="btn-ink" type="submit" disabled={!dirty || busy || noClasses}><span>{busy ? "Saving…" : "Save policy"}</span><span className="sq" aria-hidden="true">→</span></button>
        {saved && <span className="small" role="status">Saved.</span>}
      </div>
      {error && <p className="small" role="alert">{error}</p>}
    </form>
  );
}
