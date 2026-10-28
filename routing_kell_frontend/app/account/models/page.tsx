"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import { listModels, scrub } from "@/lib/byok/chat";
import { parseEnvBlock, type FoundKey } from "@/lib/byok/envimport";
import { OPENROUTER_BASE_URL, beginConnect, finishConnect } from "@/lib/byok/openrouter";
import { PROVIDERS, cleanBaseUrl, providerById } from "@/lib/byok/providers";
import { DEFAULT_DAILY_CAP, readCap, usedToday, writeCap, type KeyValue } from "@/lib/byok/spend";
import { Vault, idbStore, type KeyMeta } from "@/lib/byok/vault";
import { KEL_MODELS_ENABLED } from "@/lib/features";
import { getSession, onSessionChange, type Session } from "@/lib/session";

const kv = (): KeyValue => {
  try { if (window.localStorage) return window.localStorage; } catch { /* blocked */ }
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
};

type Probe = { state: "idle" } | { state: "testing" } | { state: "ok"; models: string[] } | { state: "failed"; message: string };

/**
 * Your own model keys. A key is encrypted in THIS browser and sent only to the provider you choose; kell never sees it,
 * never pays for your usage and never falls back to a key of its own. Shown to signed-in visitors only.
 */
export default function ModelsPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    getSession().then((s) => { if (live) setSession(s); });
    const off = onSessionChange((s) => { if (live) setSession(s); });
    return () => { live = false; off(); };
  }, []);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>MODELS</b></div>
        <h1 className="display"><AnimatedHeading>Your model keys</AnimatedHeading></h1>
      </section>
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="models-wrap">
          {session === undefined && <p className="small dim" role="status">Checking your session…</p>}
          {session === null && (
            <div className="empty" role="status">
              <b>Sign in to add a key.</b>
              <p>Keys belong to your account. <Link href="/sign-in?redirect=/account/models" style={{ textDecoration: "underline" }}>Sign in</Link>.</p>
            </div>
          )}
          {session && <Panel subject={session.subject} />}
        </div>
      </section>
    </>
  );
}

function Panel({ subject }: { subject: string }) {
  const vault = useMemo(() => new Vault(idbStore(), subject), [subject]);
  const [keys, setKeys] = useState<KeyMeta[] | undefined>(undefined);
  const [probe, setProbe] = useState<Record<string, Probe>>({});
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [provider, setProvider] = useState("anthropic");
  const [baseUrl, setBaseUrl] = useState("");
  const [label, setLabel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cap, setCap] = useState(DEFAULT_DAILY_CAP);
  const [used, setUsed] = useState(0);
  const [note, setNote] = useState("");
  const [envText, setEnvText] = useState("");
  const [envFound, setEnvFound] = useState<FoundKey[] | null>(null);
  const [envPick, setEnvPick] = useState<Record<string, boolean>>({});
  const [envIgnored, setEnvIgnored] = useState(0);
  const handledCallback = useRef(false);

  const reload = useCallback(() => {
    vault.list().then(setKeys).catch(() => { setKeys([]); setError("This browser would not let us read stored keys."); });
    const store = kv();
    setCap(readCap(store, subject));
    setUsed(usedToday(store, subject));
  }, [vault, subject]);
  useEffect(reload, [reload]);

  // Returning from openrouter.ai with ?code and ?state: take them out of the address bar at once, check them, and store the key.
  // Run once (a ref, not a cleanup flag), so a development double-run cannot drop the result.
  useEffect(() => {
    if (handledCallback.current) return;
    const q = new URLSearchParams(window.location.search);
    const code = q.get("code"), state = q.get("state");
    if (!code && !state) return;
    handledCallback.current = true;
    window.history.replaceState(null, "", window.location.pathname);
    void (async () => {
      const r = await finishConnect(code ?? "", state ?? "", window.sessionStorage);
      if (!r.ok) { setError(r.message); return; }
      try {
        await vault.add({ provider: "openrouter", baseUrl: OPENROUTER_BASE_URL, label: "OpenRouter (connected)", apiKey: r.key });
        setNote("Connected to OpenRouter. Press Test on the new key to choose a model.");
        reload();
      } catch { setError("We could not store that key in this browser."); }
    })();
  }, [vault, reload]);

  const def = providerById(provider)!;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const url = def.baseUrl || cleanBaseUrl(baseUrl);
    if (!url) { setError("Enter the provider's address, starting with https://."); return; }
    setBusy(true);
    try {
      await vault.add({ provider, baseUrl: url, label: label || def.label, apiKey });
      setApiKey(""); setLabel("");
      reload();
    } catch {
      setError("We could not store that key in this browser.");
    } finally { setBusy(false); }
  }

  async function test(k: KeyMeta) {
    setProbe((p) => ({ ...p, [k.id]: { state: "testing" } }));
    const prov = providerById(k.provider);
    const secret = await vault.reveal(k.id);
    if (!prov || !secret) { setProbe((p) => ({ ...p, [k.id]: { state: "failed", message: "This key can't be read in this browser any more. Delete it and add it again." } })); return; }
    try {
      const models = await listModels({ provider: prov, baseUrl: k.baseUrl, apiKey: secret });
      setProbe((p) => ({ ...p, [k.id]: { state: "ok", models } }));
      setChosen((c) => ({ ...c, [k.id]: models.includes(k.model) ? k.model : models[0] ?? "" }));
    } catch (err) {
      setProbe((p) => ({ ...p, [k.id]: { state: "failed", message: scrub(err instanceof Error ? err.message : "The test failed.", secret) } }));
    }
  }

  async function useModel(k: KeyMeta) {
    const m = chosen[k.id];
    if (!m) return;
    await vault.setModel(k.id, m);
    reload();
  }

  async function remove(k: KeyMeta) {
    await vault.remove(k.id);
    setProbe((p) => { const n = { ...p }; delete n[k.id]; return n; });
    reload();
  }

  async function connectOpenRouter() {
    setError("");
    try {
      window.location.assign(await beginConnect(`${window.location.origin}/account/models`, window.sessionStorage));
    } catch { setError("This browser could not start the connection."); }
  }

  function findKeys() {
    const p = parseEnvBlock(envText);
    setEnvFound(p.found);
    setEnvIgnored(p.ignored);
    setEnvPick(Object.fromEntries(p.found.map((f) => [f.varName, true])));
  }

  async function saveFound() {
    setError("");
    try {
      let n = 0;
      for (const f of envFound ?? []) {
        const prov = providerById(f.provider);
        if (!prov || !envPick[f.varName]) continue;
        await vault.add({ provider: f.provider, baseUrl: prov.baseUrl, label: `${prov.label} (imported)`, apiKey: f.apiKey });
        n++;
      }
      setNote(n ? `Saved ${n} key${n === 1 ? "" : "s"}. Press Test on each to choose a model.` : "Nothing was selected.");
      reload();
    } catch { setError("We could not store those keys in this browser."); }
    setEnvText(""); setEnvFound(null); // the pasted text is dropped as soon as it has been used
  }

  return (
    <>
      <div className="models-note">
        <p><b>Where your key goes.</b> It is encrypted in this browser and sent only to the provider you choose below. keळ never sees it, never pays for your usage and never uses a key of its own. Your provider bills you.</p>
        <p className="small dim">Keys are kept per browser. Clearing this site&rsquo;s data, or using another browser, removes them; add the key again there. A script running on this site could in principle use your stored key, so use keys with a spending limit set at the provider.</p>
      </div>

      <section className="models-kell" aria-labelledby="kell-models">
        <h2 className="h3" id="kell-models">keळ models <span className="status" data-s="unknown">Pilot</span></h2>
        <p className="small">Two small models on keळ&rsquo;s own provider accounts, for trying the assistant without a key of your own. It is separate from your keys and is never used instead of one.</p>
        <ul className="small models-kell-terms">
          <li>Capped: a small daily allowance per person, enforced by the server.</li>
          <li>Public material only. Do not send private code, secrets or personal data.</li>
          <li>Your prompt goes to the model provider (Z.ai or General Compute). We have no data-processing agreement with them yet.</li>
        </ul>
        {KEL_MODELS_ENABLED
          ? <p className="small" role="status">Not connected yet.</p>
          : <p className="small dim" role="status">Not available yet. Your own key, below, is the way to chat today.</p>}
      </section>

      {note && <p className="small" role="status">{note}</p>}

      <section className="models-ways" aria-labelledby="other-ways">
        <h2 className="h3" id="other-ways">Other ways to add a key</h2>
        <div className="models-way">
          <h3 className="small"><b>Connect with OpenRouter</b></h3>
          <p className="small dim">Approve on openrouter.ai and we receive a key that you can revoke there. Nothing to copy or paste.</p>
          <button type="button" className="btn-line" onClick={() => void connectOpenRouter()}>Connect with OpenRouter</button>
        </div>
        <div className="models-way">
          <h3 className="small"><b>Import from a .env block</b></h3>
          <p className="small dim">Paste the lines from a .env file. Only the standard provider key names are read; everything else is ignored. The text stays in this browser.</p>
          <div className="cfield"><label htmlFor="m-env">Pasted lines</label>
            <textarea id="m-env" rows={4} spellCheck={false} autoComplete="off" value={envText} onChange={(e) => { setEnvText(e.target.value); setEnvFound(null); }} /></div>
          <div className="cluster"><button type="button" className="btn-line" disabled={!envText.trim()} onClick={findKeys}>Find keys</button></div>
          {envFound && (
            <div role="status" className="models-found">
              {envFound.length === 0 && <p className="small">No provider keys found{envIgnored ? ` (${envIgnored} other line${envIgnored === 1 ? "" : "s"} ignored)` : ""}.</p>}
              {envFound.map((f) => (
                <label key={f.varName} className="small models-found-row">
                  <input type="checkbox" checked={Boolean(envPick[f.varName])} onChange={(e) => setEnvPick((p) => ({ ...p, [f.varName]: e.target.checked }))} />
                  <code>{f.varName}</code> &rarr; {providerById(f.provider)?.label}, ends in {f.apiKey.slice(-4)}
                </label>
              ))}
              {envFound.length > 0 && <div className="cluster"><button type="button" className="btn-ink" onClick={() => void saveFound()}><span>Save selected</span></button><span className="small dim">{envIgnored} other line{envIgnored === 1 ? "" : "s"} ignored</span></div>}
            </div>
          )}
        </div>
      </section>

      <form className="cform models-form" onSubmit={add}>
        <h2 className="h3">Or paste a key</h2>
        <div className="cfield"><label htmlFor="m-provider">Provider</label>
          <select id="m-provider" value={provider} onChange={(e) => { setProvider(e.target.value); setBaseUrl(""); }}>
            {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select></div>
        {!def.baseUrl && (
          <div className="cfield"><label htmlFor="m-base">Provider address</label>
            <input id="m-base" type="text" inputMode="url" autoComplete="off" placeholder="https://api.example.com/v1" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
            <span className="hint">Any OpenAI-compatible host. It must allow requests from a web page.</span></div>
        )}
        <div className="cfield"><label htmlFor="m-key">API key</label>
          <input id="m-key" type="password" autoComplete="off" spellCheck={false} required value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          <span className="hint">
            {def.keyHint}.
            {provider === "anthropic" && " Only an API key works here. A login or session token from a chat app is never accepted."}
          </span></div>
        <div className="cfield"><label htmlFor="m-label">Label <span className="dim">(optional)</span></label>
          <input id="m-label" type="text" autoComplete="off" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} /></div>
        <div className="cform-submit">
          <button className="btn-ink" type="submit" disabled={busy || !apiKey.trim()}><span>{busy ? "Saving…" : "Save key"}</span><span className="sq" aria-hidden="true">→</span></button>
          {error && <span className="small" role="alert">{error}</span>}
        </div>
      </form>

      <div className="models-list">
        <h2 className="h3">Your keys</h2>
        {keys === undefined && <p className="small dim">Loading…</p>}
        {keys && keys.length === 0 && <p className="small dim">No keys yet. Add one above to chat with a model in Delegator.</p>}
        {keys?.map((k) => {
          const pr = probe[k.id] ?? { state: "idle" as const };
          return (
            <article className="pane models-key" key={k.id}>
              <header>
                <span>{k.label || providerById(k.provider)?.label}</span>
                <span className="small dim">{providerById(k.provider)?.label} · ends in {k.last4} · added {new Date(k.createdAt).toLocaleDateString()}</span>
              </header>
              <div className="models-key-body">
                <p className="small">{k.model ? <>Model: <code>{k.model}</code></> : "No model chosen yet. Press Test, then pick one."}</p>
                <div className="cluster">
                  <button type="button" className="btn-line" onClick={() => void test(k)} disabled={pr.state === "testing"}>{pr.state === "testing" ? "Testing…" : "Test"}</button>
                  <button type="button" className="btn-line" onClick={() => void remove(k)}>Delete</button>
                </div>
                {pr.state === "failed" && <p className="small" role="alert">{pr.message}</p>}
                {pr.state === "ok" && (
                  <div className="cluster" role="status">
                    <span className="small">The key works. {pr.models.length} model{pr.models.length === 1 ? "" : "s"} available.</span>
                    <label className="small" htmlFor={`m-model-${k.id}`}>Use</label>
                    <select id={`m-model-${k.id}`} value={chosen[k.id] ?? ""} onChange={(e) => setChosen((c) => ({ ...c, [k.id]: e.target.value }))}>
                      {pr.models.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                    <button type="button" className="btn-ink" onClick={() => void useModel(k)}><span>Use this model</span></button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      <div className="models-cap">
        <h2 className="h3">Daily limit</h2>
        <p className="small dim">A soft limit in this browser on assistant replies per day. It is a convenience: your provider bills you and applies its own limits.</p>
        <div className="cfield" style={{ maxWidth: 240 }}>
          <label htmlFor="m-cap">Replies per day</label>
          <input id="m-cap" type="number" min={1} max={5000} value={cap} onChange={(e) => setCap(writeCap(kv(), subject, Number(e.target.value)))} />
          <span className="hint">Used today: {used}</span>
        </div>
      </div>
    </>
  );
}
