"use client";
import { useCallback, useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import {
  applyRecommendedPolicy, createInvite, deleteProviderKey, getInvites, getOnboarding, getProviderKeys, joinCommand, NEXT_STEP_TEXT,
  parseUnits, putProviderKey, revokeInvite, type CreatedInvite, type Invite, type Onboarding, type ProviderKey, type ApiState,
} from "@/lib/onboarding-api";
import type { Org } from "@/lib/org-api";

const MCP_URL = (process.env.NEXT_PUBLIC_KEL_MCP_URL || "").replace(/\/$/, "");
const DEFAULT_MODELS = "gemma-4-31b-it, 0.09, 0.34, gemma-4-31B-it\ngpt-oss-120b, 0.037, 0.17\nminimax-m2-7, 0.21, 0.84, minimax-m2.7";

function errText(r: ApiState<unknown>): string {
  return r.kind === "error" ? r.message : r.kind === "forbidden" ? "The backend refused this: your role can't do it." : `Not applied (${r.kind}).`;
}

function StartPanel({ org }: { org: Org }) {
  const id = org.organization_id;
  const [status, setStatus] = useState<ApiState<Onboarding>>({ kind: "loading" });
  const [keys, setKeys] = useState<ApiState<ProviderKey[]>>({ kind: "loading" });
  const [invites, setInvites] = useState<ApiState<Invite[]>>({ kind: "loading" });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const [provider, setProvider] = useState("general-compute");
  const [baseUrl, setBaseUrl] = useState("https://api.generalcompute.com/v1");
  const [connection, setConnection] = useState("main");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState(DEFAULT_MODELS);

  const [monthly, setMonthly] = useState("");
  const [daily, setDaily] = useState("");

  const [label, setLabel] = useState("");
  const [days, setDays] = useState("7");
  const [maxUses, setMaxUses] = useState("50");
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    const [s, k, i] = await Promise.all([getOnboarding(id, signal), getProviderKeys(id, signal), getInvites(id, signal)]);
    setStatus(s); setKeys(k); setInvites(i);
  }, [id]);

  useEffect(() => {
    const ac = new AbortController();
    load(ac.signal);
    return () => ac.abort();
  }, [load]);

  async function saveKey(e: React.FormEvent) {
    e.preventDefault();
    const parsed = parseUnits(models);
    if (!parsed.ok) { setMsg(parsed.error); return; }
    setBusy(true);
    const r = await putProviderKey(id, connection.trim(), { provider: provider.trim(), base_url: baseUrl.trim(), units: parsed.units, ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}) });
    setBusy(false);
    if (r.kind === "ok") { setApiKey(""); setMsg(`Connected ${r.data.connection_id} (key ending ${r.data.key_hint.replace("...", "")}). The key is stored encrypted and is never shown again.`); load(); }
    else setMsg(errText(r));
  }

  async function removeKey(k: ProviderKey) {
    if (!window.confirm(`Remove ${k.connection_id}? Developers lose these models immediately and the stored key is deleted.`)) return;
    setBusy(true);
    const r = await deleteProviderKey(id, k.connection_id);
    setBusy(false);
    setMsg(r.kind === "ok" ? `Removed ${k.connection_id}.` : errText(r));
    if (r.kind === "ok") load();
  }

  async function applyPolicy(e: React.FormEvent) {
    e.preventDefault();
    if (!window.confirm("Apply the policy? It allows exactly the providers and models you connected, and these budgets. It takes effect immediately.")) return;
    setBusy(true);
    const r = await applyRecommendedPolicy(id, monthly, daily);
    setBusy(false);
    setMsg(r.kind === "ok" ? `Policy applied (version ${r.data.version}).` : errText(r));
    if (r.kind === "ok") load();
  }

  async function makeInvite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await createInvite(id, { ...(label.trim() ? { label: label.trim() } : {}), days: Number(days), max_uses: Number(maxUses) });
    setBusy(false);
    if (r.kind === "ok") { setCreated(r.data); setCopied(false); setMsg(""); load(); }
    else setMsg(errText(r));
  }

  async function revoke(i: Invite) {
    if (!window.confirm("Revoke this invite? People who already joined stay members.")) return;
    const r = await revokeInvite(id, i.id);
    setMsg(r.kind === "ok" ? "Invite revoked." : errText(r));
    if (r.kind === "ok") load();
  }

  const command = created ? joinCommand(created.code, MCP_URL) : "";
  const s = status.kind === "ok" ? status.data : null;
  const keyList = keys.kind === "ok" ? keys.data : [];

  return (
    <>
      {s && (
        <div className="stats">
          <Stat label="Provider keys" value={s.provider_keys} bad={s.provider_keys === 0} />
          <Stat label="Policy" value={s.policy_set ? "Applied" : "Not set"} bad={!s.policy_set} note={s.kill_switch ? "Kill switch is ON" : undefined} />
          <Stat label="Joined" value={s.members} note="including admins" />
          <Stat label="Ready" value={s.members_ready} note="made a successful call" />
        </div>
      )}
      {s && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}><b>Next:</b> {NEXT_STEP_TEXT[s.next_step]}</p>}
      {status.kind !== "ok" && <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={status} /></div>}
      {msg && <p className="notice-ok" role="status" style={{ gridColumn: "1 / span 12" }}>{msg}</p>}

      <form className="cform wide" onSubmit={saveKey} style={{ maxWidth: 720 }} autoComplete="off">
        <h2 className="h3">1. Connect your provider key</h2>
        <p className="small dim">Your company&rsquo;s own key for the model provider. It is stored encrypted, used only for your developers&rsquo; calls, and never shown again. Developers never see or need it.</p>
        <div className="cfield"><label htmlFor="ck-name">Connection name</label><input id="ck-name" value={connection} onChange={(e) => setConnection(e.target.value)} required pattern="[a-z0-9][a-z0-9_-]{0,62}" /></div>
        <div className="cfield"><label htmlFor="ck-provider">Provider</label><input id="ck-provider" value={provider} onChange={(e) => setProvider(e.target.value)} required /></div>
        <div className="cfield"><label htmlFor="ck-url">API address (https)</label><input id="ck-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required placeholder="https://…/v1" /></div>
        <div className="cfield"><label htmlFor="ck-key">API key</label><input id="ck-key" type="password" autoComplete="new-password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="leave empty to keep the stored key" /></div>
        <div className="cfield"><label htmlFor="ck-models">Models</label>
          <textarea id="ck-models" rows={4} value={models} onChange={(e) => setModels(e.target.value)} spellCheck={false} />
          <span className="small dim">One per line: <code>model, input price, output price</code> (USD per million tokens), optionally <code>, provider model id</code>. Prices drive the budget.</span>
        </div>
        <div><button type="submit" className="btn-ink" disabled={busy}>{busy ? "Saving…" : "Connect"}</button></div>
        {keys.kind !== "ok" ? <AdminNotice state={keys} /> : keyList.length > 0 && (
          <div className="scroll-x">
            <table className="dtable">
              <thead><tr><th>Connection</th><th>Provider</th><th>Models</th><th>Key</th><th /></tr></thead>
              <tbody>
                {keyList.map((k) => (
                  <tr key={k.connection_id}>
                    <td>{k.connection_id}</td><td>{k.provider}</td><td>{k.units.map((u) => u.model).join(", ")}</td><td className="mono">{k.key_hint}</td>
                    <td><button type="button" className="btn-line danger" disabled={busy} onClick={() => removeKey(k)}>Remove</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </form>

      <form className="cform wide" onSubmit={applyPolicy} style={{ maxWidth: 720 }}>
        <h2 className="h3">2. Set the budget and apply the policy</h2>
        <p className="small dim">The policy allows exactly the providers and models you connected. There is no default budget: type one. A budget of 0 means no spend. Fine-tune anything later under Policy.</p>
        <div className="cfield"><label htmlFor="po-m">Monthly budget (USD)</label><input id="po-m" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} required /></div>
        <div className="cfield"><label htmlFor="po-d">Per-developer daily budget (USD)</label><input id="po-d" inputMode="decimal" value={daily} onChange={(e) => setDaily(e.target.value)} required /></div>
        <div><button type="submit" className="btn-ink" disabled={busy || keyList.length === 0}>{keyList.length === 0 ? "Connect a key first" : "Apply policy"}</button></div>
      </form>

      <form className="cform wide" onSubmit={makeInvite} style={{ maxWidth: 720 }}>
        <h2 className="h3">3. Invite your developers</h2>
        <p className="small dim">Send the command below to a team or a pilot group. Each developer runs it once: they sign in with your company login and are done. The command works until it expires or is used up.</p>
        <div className="cfield"><label htmlFor="iv-l">Label (optional)</label><input id="iv-l" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="e.g. Pilot group" /></div>
        <div className="cfield"><label htmlFor="iv-d">Valid for (days, 1–30)</label><input id="iv-d" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} required /></div>
        <div className="cfield"><label htmlFor="iv-u">People who can use it (1–5000)</label><input id="iv-u" inputMode="numeric" value={maxUses} onChange={(e) => setMaxUses(e.target.value)} required /></div>
        <div><button type="submit" className="btn-ink" disabled={busy}>Create invite</button></div>
        {created && (
          <div className="panel">
            <p className="small"><b>Shown once.</b> Copy it now; it can&rsquo;t be displayed again (revoke it and make another if lost).</p>
            <pre className="mono small" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{command}</pre>
            {!MCP_URL && <p className="small dim">Set <code>NEXT_PUBLIC_KEL_MCP_URL</code> for this console and the command will include the MCP address; until then add <code>--url &lt;your MCP address&gt;</code> yourself.</p>}
            <div className="row-actions">
              <button type="button" className="btn-line" onClick={async () => { try { await navigator.clipboard.writeText(command); setCopied(true); } catch { setCopied(false); } }}>{copied ? "Copied" : "Copy command"}</button>
            </div>
          </div>
        )}
        {invites.kind !== "ok" ? <AdminNotice state={invites} /> : invites.data.length > 0 && (
          <div className="scroll-x">
            <table className="dtable">
              <thead><tr><th>Label</th><th>Created</th><th>Expires</th><th>Used</th><th>State</th><th /></tr></thead>
              <tbody>
                {invites.data.map((i) => (
                  <tr key={i.id}>
                    <td>{i.label || "—"}</td><td>{i.created_at.slice(0, 10)}</td><td>{i.expires_at.slice(0, 10)}</td><td>{i.uses} / {i.max_uses}</td>
                    <td>{i.revoked_at ? "Revoked" : i.active ? "Active" : "Expired or used up"}</td>
                    <td>{i.active && <button type="button" className="btn-line danger" onClick={() => revoke(i)}>Revoke</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </form>
    </>
  );
}

export default function StartPage() {
  return (
    <>
      <Hero marker="ONBOARDING" title="Get started" lead="Connect your company's model key once, set a budget, and send one command to your developers." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <StartPanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
