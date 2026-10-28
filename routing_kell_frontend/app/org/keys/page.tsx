"use client";
import { useRef, useState } from "react";
import { LoadState, useLoad } from "@/components/org/useLoad";
import { useOrg } from "@/components/org/OrgShell";
import { KNOWN_DATA_CLASSES, type ProviderKey, type ProviderKeyInput } from "@/lib/org-admin";

interface UnitRow { model: string; input: string; output: string }
const blankUnit = (): UnitRow => ({ model: "", input: "", output: "" });
const CID = /^[a-z0-9][a-z0-9_-]{0,62}$/;

/** The organization's own provider keys: add, replace, remove. A key is stored encrypted and never shown again; only its last four characters are. */
export default function KeysPage() {
  const { api, org, admin } = useOrg();
  const id = org.organization_id;
  const keys = useLoad(() => api.providerKeys(id), id);
  const [editing, setEditing] = useState<ProviderKey | "new" | null>(null);
  const [removing, setRemoving] = useState<ProviderKey | null>(null);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);

  if (!admin) return <div className="empty"><b>Provider keys are managed by owners and admins.</b></div>;

  async function remove() {
    if (!removing) return;
    try { await api.removeProviderKey(id, removing.connection_id); setRemoving(null); dialog.current?.close(); keys.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
  }

  return (
    <div className="org-stack">
      <div className="profile-card">
        <h2 className="h3">Provider keys</h2>
        <p className="small dim">Keys your organization pays for. They are stored encrypted, used only for your members&rsquo; calls, and never shown again: only the last four characters are.</p>
        <LoadState loaded={keys.loaded} reload={keys.reload} what="provider keys" />
        {keys.loaded.state === "ok" && (keys.loaded.data.length === 0
          ? <p className="small dim">No keys connected yet.</p>
          : (
            <div className="org-table-wrap">
              <table className="org-table">
                <thead><tr><th>Connection</th><th>Provider</th><th>Models</th><th>Data allowed</th><th>Key</th><th /></tr></thead>
                <tbody>
                  {keys.loaded.data.map((k) => (
                    <tr key={k.connection_id}>
                      <td><code>{k.connection_id}</code>{!k.enabled && <span className="dim"> · off</span>}</td><td>{k.provider}</td>
                      <td>{k.units.map((u) => u.model).join(", ")}</td><td>{k.allowed_data_classes.join(", ")}</td>
                      <td>{k.key_hint ? `••••${k.key_hint}` : "–"}</td>
                      <td className="org-actions">
                        <button type="button" className="btn-line" onClick={() => { setEditing(k); setError(""); }}>Replace</button>
                        <button type="button" className="btn-line" onClick={() => { setRemoving(k); setError(""); dialog.current?.showModal(); }}>Remove</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        {editing === null && <div className="cluster"><button type="button" className="btn-ink" onClick={() => { setEditing("new"); setError(""); }}><span>Add a key</span></button></div>}
      </div>

      {editing !== null && <KeyForm key={editing === "new" ? "new" : editing.connection_id} existing={editing === "new" ? null : editing} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); keys.reload(); }} />}

      <dialog ref={dialog} className="org-dialog" aria-labelledby="rk-title" onClose={() => setRemoving(null)}>
        <div className="org-stack">
          <h2 id="rk-title" className="h3">Remove {removing?.connection_id}?</h2>
          <p>The key is deleted, ciphertext included. Calls that use this connection stop working until another key is added.</p>
          {error && <p className="small" role="alert">{error}</p>}
          <div className="cluster">
            <button type="button" className="btn-ink org-stop-solid" onClick={() => void remove()}><span>Remove key</span></button>
            <button type="button" className="btn-line" onClick={() => dialog.current?.close()}>Cancel</button>
          </div>
        </div>
      </dialog>
    </div>
  );
}

function KeyForm({ existing, onCancel, onSaved }: { existing: ProviderKey | null; onCancel: () => void; onSaved: () => void }) {
  const { api, org } = useOrg();
  const [cid, setCid] = useState(existing?.connection_id ?? "");
  const [provider, setProvider] = useState(existing?.provider ?? "");
  const [baseUrl, setBaseUrl] = useState(existing?.base_url ?? "https://");
  const [apiKey, setApiKey] = useState("");
  const [units, setUnits] = useState<UnitRow[]>(existing?.units.length ? existing.units.map((u) => ({ model: u.model, input: String(u.input_per_mtok), output: String(u.output_per_mtok) })) : [blankUnit()]);
  const [classes, setClasses] = useState<string[]>(existing?.allowed_data_classes ?? ["USER_PRIVATE"]);
  const [zdr, setZdr] = useState(false);
  const [noTraining, setNoTraining] = useState(false);
  const [dpa, setDpa] = useState(false);
  const [region, setRegion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const setUnit = (i: number, patch: Partial<UnitRow>) => setUnits((p) => p.map((u, j) => (j === i ? { ...u, ...patch } : u)));
  const toggle = (c: string) => setClasses((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!existing && !CID.test(cid)) return setError("The connection name is 1 to 63 characters: lowercase letters, numbers, dashes or underscores.");
    if (!provider.trim()) return setError("Say which provider this is.");
    if (!baseUrl.startsWith("https://")) return setError("The address must start with https://");
    if (!existing && !apiKey.trim()) return setError("Paste the key.");
    const clean = units.filter((u) => u.model.trim());
    if (clean.length === 0 || clean.some((u) => !(Number(u.input) >= 0) || !(Number(u.output) >= 0) || u.input === "" || u.output === "")) return setError("Give each model a name and its input and output cost per million tokens. Without a cost the budget check blocks every call.");
    if (classes.length === 0) return setError("Choose at least one data type this key may be used for.");
    const body: ProviderKeyInput = {
      provider: provider.trim(), base_url: baseUrl.trim(), allowed_data_classes: classes,
      units: clean.map((u) => ({ model: u.model.trim(), input_per_mtok: Number(u.input), output_per_mtok: Number(u.output) })),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      ...(zdr ? { zdr: true } : {}), ...(noTraining ? { no_training: true } : {}), ...(dpa ? { dpa_signed: true } : {}), ...(region.trim() ? { region: region.trim() } : {}),
    };
    setBusy(true);
    try { await api.putProviderKey(org.organization_id, existing?.connection_id ?? cid, body); setApiKey(""); onSaved(); }
    catch (err) { setError(err instanceof Error ? err.message : "That didn't work."); }
    finally { setBusy(false); }
  }

  return (
    <form className="profile-card cform" onSubmit={(e) => void submit(e)} autoComplete="off">
      <h2 className="h3">{existing ? `Replace ${existing.connection_id}` : "Add a provider key"}</h2>
      {!existing && <div className="cfield"><label htmlFor="k-cid">Connection name</label><input id="k-cid" value={cid} onChange={(e) => setCid(e.target.value.toLowerCase())} placeholder="main" maxLength={63} /></div>}
      <div className="org-row">
        <div className="cfield"><label htmlFor="k-provider">Provider</label><input id="k-provider" value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="general-compute" /></div>
        <div className="cfield"><label htmlFor="k-url">Address</label><input id="k-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} inputMode="url" /></div>
      </div>
      <div className="cfield"><label htmlFor="k-key">API key {existing && <span className="dim">(leave blank to keep the stored key)</span>}</label>
        <input id="k-key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="new-password" spellCheck={false} />
        <span className="hint">Sent once over HTTPS and stored encrypted. It is never shown again{existing?.key_hint ? `; the stored key ends in ${existing.key_hint}` : ""}.</span></div>

      <fieldset className="profile-visibility">
        <legend>Models and what you pay the provider per million tokens</legend>
        {units.map((u, i) => (
          <div className="org-unit" key={i}>
            <input aria-label={`Model ${i + 1}`} value={u.model} onChange={(e) => setUnit(i, { model: e.target.value })} placeholder="model name" />
            <input aria-label={`Input cost per million tokens, model ${i + 1}`} inputMode="decimal" value={u.input} onChange={(e) => setUnit(i, { input: e.target.value })} placeholder="input" />
            <input aria-label={`Output cost per million tokens, model ${i + 1}`} inputMode="decimal" value={u.output} onChange={(e) => setUnit(i, { output: e.target.value })} placeholder="output" />
            {units.length > 1 && <button type="button" className="btn-line" onClick={() => setUnits((p) => p.filter((_, j) => j !== i))} aria-label={`Remove model ${i + 1}`}>×</button>}
          </div>
        ))}
        <div><button type="button" className="btn-line" onClick={() => setUnits((p) => [...p, blankUnit()])}>+ Add a model</button></div>
        <span className="hint">These are your own contract costs. They are used only to count your spend against your budgets.</span>
      </fieldset>

      <fieldset className="profile-visibility">
        <legend>Data this key may be used for</legend>
        <div className="org-classes">
          {KNOWN_DATA_CLASSES.map((c) => <label key={c.id}><input type="checkbox" checked={classes.includes(c.id)} onChange={() => toggle(c.id)} /> {c.label}{!c.public && <span className="dim"> · private</span>}</label>)}
        </div>
      </fieldset>

      <fieldset className="profile-visibility">
        <legend>What your provider agreement says <span className="dim">(optional)</span></legend>
        <div className="org-classes">
          <label><input type="checkbox" checked={zdr} onChange={(e) => setZdr(e.target.checked)} /> Zero data retention</label>
          <label><input type="checkbox" checked={noTraining} onChange={(e) => setNoTraining(e.target.checked)} /> Not used for training</label>
          <label><input type="checkbox" checked={dpa} onChange={(e) => setDpa(e.target.checked)} /> Data processing agreement signed</label>
        </div>
        <div className="cfield"><label htmlFor="k-region">Region</label><input id="k-region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="eu" /></div>
        <span className="hint">These are your statements about your agreement, recorded as you give them.</span>
      </fieldset>

      {error && <p className="small" role="alert">{error}</p>}
      <div className="cform-submit">
        <button className="btn-ink" type="submit" disabled={busy}><span>{busy ? "Saving…" : existing ? "Replace key" : "Add key"}</span></button>
        <button type="button" className="btn-line" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
