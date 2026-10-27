"use client";
import { useCallback, useEffect, useId, useState } from "react";
import KeyField from "@/components/KeyField";
import { formatMs, formatPrices } from "@/lib/format";
import type { HelperClient } from "@/lib/helper";
import type { ModelInfo, NewModel } from "@/lib/types";
import { HelperError, isLoopbackAddress, KEY_NAME, MODEL_NAME, scrub, suggestKeyName } from "@/lib/words";

interface Preset { id: string; label: string; base: string; keyEnv: string; local: boolean; model: string }
// The two default providers (the open models that were run end to end), and a local server. Every field stays editable.
export const PRESETS: Preset[] = [
  { id: "custom", label: "Another provider (OpenAI-compatible)", base: "", keyEnv: "", local: false, model: "" },
  { id: "general-compute", label: "General Compute", base: "https://api.generalcompute.com/v1", keyEnv: "GENERAL_COMPUTE_API_KEY", local: false, model: "" },
  { id: "zai", label: "Z.ai", base: "https://api.z.ai/api/paas/v4", keyEnv: "ZAI_API_KEY", local: false, model: "" },
  { id: "local", label: "Local model (for example Ollama)", base: "http://127.0.0.1:11434/v1", keyEnv: "", local: true, model: "" },
];

export interface DraftModel {
  preset: string; name: string; model_id: string; base: string; keyEnv: string; key: string; inPrice: string; outPrice: string; noSystem: boolean;
}
export const emptyDraft = (): DraftModel => ({ preset: "custom", name: "", model_id: "", base: "", keyEnv: "", key: "", inPrice: "", outPrice: "", noSystem: false });

/** Returns the first problem in words, or null. The helper checks again; this is so a person is told before sending. */
export function draftProblem(d: DraftModel): string | null {
  const local = d.preset === "local";
  if (!MODEL_NAME.test(d.name)) return "Give it a short name: letters, digits, dots or dashes.";
  if (!d.model_id.trim()) return "Enter the model id the provider expects.";
  let u: URL;
  try { u = new URL(d.base); } catch { return "Enter the provider’s address, starting with https://."; }
  if (local && !isLoopbackAddress(d.base)) return "A local model must be at 127.0.0.1 or localhost.";
  if (!local && u.protocol !== "https:" && !isLoopbackAddress(d.base)) return "A provider address must start with https://.";
  if (!local && !KEY_NAME.test(d.keyEnv)) return "The key name is letters, digits and underscores, for example MY_PROVIDER_API_KEY.";
  const a = d.inPrice.trim(), b = d.outPrice.trim();
  if ((a === "") !== (b === "")) return "Give both prices or neither.";
  if (a !== "" && (!(Number(a) >= 0) || !(Number(b) >= 0))) return "Prices are numbers, in dollars per million tokens.";
  return null;
}

export default function ModelsPanel({ client }: { client: HelperClient }) {
  const uid = useId();
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<DraftModel>(emptyDraft());
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, string>>({});
  const [rowError, setRowError] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoadError(null);
    try { setModels(await client.models()); }
    catch (e) { setLoadError(e instanceof HelperError ? e.message : "The model list could not be read."); setModels([]); }
  }, [client]);
  useEffect(() => { void load(); }, [load]);

  const set = <K extends keyof DraftModel>(k: K, v: DraftModel[K]) => setDraft((d) => ({ ...d, [k]: v }));

  function choosePreset(id: string) {
    const p = PRESETS.find((x) => x.id === id) ?? PRESETS[0];
    setDraft((d) => ({ ...d, preset: id, base: p.base, keyEnv: p.keyEnv || (p.local ? "" : d.keyEnv) }));
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const problem = draftProblem(draft);
    if (problem) { setFormError(problem); return; }
    setAdding(true); setFormError(null); setNotice(null);
    const local = draft.preset === "local";
    const body: NewModel = {
      name: draft.name.trim(), model_id: draft.model_id.trim(), openai_base_url: draft.base.trim().replace(/\/+$/, ""),
      ...(local ? {} : { key_env: draft.keyEnv.trim() }),
      ...(draft.inPrice.trim() !== "" ? { input_per_mtok: Number(draft.inPrice), output_per_mtok: Number(draft.outPrice) } : {}),
      ...(draft.noSystem ? { no_system_role: true } : {}),
    };
    const typedKey = draft.key.trim();
    try {
      await client.addModel(body);
      if (!local && typedKey) await client.saveKey(body.key_env!, typedKey);
      setNotice(`Added ${body.name}.${!local && !typedKey ? " Add its key below to use it." : ""}`);
      setDraft(emptyDraft());
      await load();
    } catch (err) {
      setFormError(scrub(err instanceof HelperError ? err.message : "The model could not be added.", typedKey));
      setDraft((d) => ({ ...d, key: "" }));
    } finally { setAdding(false); }
  }

  async function test(m: ModelInfo) {
    setTests((t) => ({ ...t, [m.name]: "Testing…" })); setRowError((r) => ({ ...r, [m.name]: "" }));
    try {
      const r = await client.testModel(m.name);
      setTests((t) => ({ ...t, [m.name]: r.ok ? `Replied in ${formatMs(r.latency_ms)}.` : `Failed after ${formatMs(r.latency_ms)}: ${r.error ?? "no reason given"}` }));
    } catch (e) {
      setTests((t) => ({ ...t, [m.name]: "" }));
      setRowError((r) => ({ ...r, [m.name]: e instanceof HelperError ? e.message : "The test did not run." }));
    }
  }

  async function removeModel(m: ModelInfo) {
    setRowError((r) => ({ ...r, [m.name]: "" })); setNotice(null);
    try { await client.removeModel(m.name); setNotice(`Removed ${m.name}. Its key, if any, is still saved.`); await load(); }
    catch (e) { setRowError((r) => ({ ...r, [m.name]: e instanceof HelperError ? e.message : "It could not be removed." })); }
  }

  const local = draft.preset === "local";

  return (
    <div className="panel" role="tabpanel" aria-labelledby="tab-models" id="panel-models">
      <section className="card" aria-labelledby={`${uid}-list`}>
        <h2 id={`${uid}-list`}>Models and keys</h2>
        <p className="small dim">These are the models kell can send routine tasks to. A key is kept on this machine, in a file only you can read; it is never shown again after you save it.</p>
        <div role="status" aria-live="polite">{notice && <p className="notice" style={{ marginTop: 12 }}>{notice}</p>}</div>
        {loadError && <p className="notice" role="alert" data-tone="bad" style={{ marginTop: 12 }}>{loadError}</p>}
        {models === null && <p className="small dim" style={{ marginTop: 12 }}>Loading…</p>}
        {models && models.length === 0 && !loadError && (
          <div className="empty" style={{ marginTop: 14 }}>
            <b>No models yet.</b>
            <p>Add one below. A local model needs no key.</p>
          </div>
        )}
        {models && models.length > 0 && (
          <div className="rows" style={{ marginTop: 14 }}>
            {models.map((m) => (
              <div className="row" key={m.name} data-model={m.name}>
                <div>
                  <div className="name">{m.name}</div>
                  <div className="small dim">{m.model_id}{m.tier ? ` · ${m.tier}` : ""}</div>
                  <div className="small dim" style={{ overflowWrap: "anywhere" }}>{m.base_url ?? "no OpenAI-compatible address"}</div>
                  <div className="cluster" style={{ marginTop: 6 }}>
                    {m.local && <span className="status" data-s="candidate">local</span>}
                    <span className="small dim">{formatPrices(m.input_per_mtok, m.output_per_mtok)}</span>
                  </div>
                </div>
                <div>
                  {!m.key_env ? (
                    <p className="small dim">No key needed.{m.local ? " This model runs on this machine." : ""}</p>
                  ) : (
                    <KeyField key={`${m.name}-${m.key_set}`} envName={m.key_env} isSet={m.key_set} last4={m.key_last4}
                              onSave={async (v) => { const r = await client.saveKey(m.key_env!, v); await load(); return r; }}
                              onRemove={async () => { await client.removeKey(m.key_env!); await load(); }} />
                  )}
                  <div role="status" aria-live="polite" className="small" style={{ marginTop: 8 }}>{tests[m.name]}</div>
                  {rowError[m.name] && <p className="notice" role="alert" data-tone="bad" style={{ marginTop: 8 }}>{rowError[m.name]}</p>}
                </div>
                <div className="stack">
                  <button type="button" className="btn-line" onClick={() => test(m)}>Test</button>
                  <button type="button" className="btn-line" onClick={() => removeModel(m)} aria-label={`Remove model ${m.name}`}>Remove model</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card" aria-labelledby={`${uid}-add`}>
        <h2 id={`${uid}-add`}>Add a model</h2>
        <form onSubmit={add} className="stack" autoComplete="off">
          <div className="fields">
            <div className="field">
              <label htmlFor={`${uid}-preset`}>Provider</label>
              <select id={`${uid}-preset`} value={draft.preset} onChange={(e) => choosePreset(e.target.value)}>
                {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-name`}>Name</label>
              <input id={`${uid}-name`} type="text" value={draft.name} onChange={(e) => { set("name", e.target.value); if (!local && !draft.keyEnv && draft.preset === "custom") set("keyEnv", suggestKeyName(e.target.value)); }} />
              <span className="hint">What you will call it here.</span>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-mid`}>Model id</label>
              <input id={`${uid}-mid`} type="text" value={draft.model_id} onChange={(e) => set("model_id", e.target.value)} />
              <span className="hint">{local ? "The name your local server knows, for example llama3.2." : "The id the provider expects."}</span>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-base`}>{local ? "Local address" : "Provider address"}</label>
              <input id={`${uid}-base`} type="url" value={draft.base} onChange={(e) => set("base", e.target.value)} />
              {local && <span className="hint">Must be on this machine (127.0.0.1 or localhost).</span>}
            </div>
            {!local && (
              <>
                <div className="field">
                  <label htmlFor={`${uid}-kenv`}>Key name</label>
                  <input id={`${uid}-kenv`} type="text" value={draft.keyEnv} onChange={(e) => set("keyEnv", e.target.value.toUpperCase())} />
                  <span className="hint">Where the key is kept on this machine.</span>
                </div>
                <div className="field">
                  <label htmlFor={`${uid}-key`}>Key (optional now)</label>
                  <input id={`${uid}-key`} type="password" autoComplete="new-password" spellCheck={false} value={draft.key} onChange={(e) => set("key", e.target.value)} />
                  <span className="hint">Sent once to the helper on this machine, then cleared.</span>
                </div>
              </>
            )}
            <div className="field">
              <label htmlFor={`${uid}-pin`}>Price in (optional)</label>
              <input id={`${uid}-pin`} type="number" min="0" step="any" inputMode="decimal" value={draft.inPrice} onChange={(e) => set("inPrice", e.target.value)} />
              <span className="hint">Dollars per million tokens. Give both prices or neither.</span>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-pout`}>Price out (optional)</label>
              <input id={`${uid}-pout`} type="number" min="0" step="any" inputMode="decimal" value={draft.outPrice} onChange={(e) => set("outPrice", e.target.value)} />
            </div>
          </div>
          <label className="check"><input type="checkbox" checked={draft.noSystem} onChange={(e) => set("noSystem", e.target.checked)} /><span>This model rejects a system message<small>Tick it if the provider returns an error about the “system” role.</small></span></label>
          {formError && <p className="notice" role="alert" data-tone="bad">{formError}</p>}
          <div className="cluster"><button type="submit" className="btn-ink" disabled={adding}>{adding ? "Adding…" : "Add model"}</button></div>
        </form>
      </section>
    </div>
  );
}
