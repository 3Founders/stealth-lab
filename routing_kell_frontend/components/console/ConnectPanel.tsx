"use client";
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import type { HelperClient } from "@/lib/console/helper";
import type { ClientId, ClientInfo, InstallResult, ReadinessItem } from "@/lib/console/types";
import { HelperError } from "@/lib/console/words";

// A convenience list for the Cursor model picker. It can go stale: "Other" lets a person type the exact name Cursor shows.
const CURSOR_MODELS = ["claude-sonnet-4.5", "claude-opus-4.1", "gpt-5", "gemini-2.5-pro"];
const OTHER = "__other__";

type Step = "idle" | "previewing" | "preview" | "installing" | "done";
type Progress = "waiting" | "working" | "done" | "failed";

/** The honest statement of what each tool receives (from the setup brief; the helper's `hooks` flag keeps it in step). */
function gets(c: ClientInfo): string {
  if (c.id === "claude-code") return "The MCP tools and the per-prompt hooks (lookup, cheap-model dispatch with a check, outcome capture). Restart the tool.";
  if (c.id === "cursor") return "The MCP tools and the hooks, with dispatch once a model is named. Close every window and reopen.";
  return "The MCP tools today. Hooks for Codex are not integrated yet, so the agent calls kell only when it chooses to. Restart Codex.";
}

export function cursorModelProblem(raw: string): string | null {
  const v = raw.trim();
  if (!v) return "Name the model you have selected in Cursor.";
  if (v.toLowerCase() === "auto") return "“auto” is not a model. Without a model name, dispatch stays off for Cursor.";
  if (!/^[\w.:/@+-]{1,80}$/.test(v)) return "That does not look like a model name.";
  return null;
}

export default function ConnectPanel({ client, initial }: { client: HelperClient; initial: ClientInfo[] }) {
  const uid = useId();
  const [clients, setClients] = useState<ClientInfo[]>(initial);
  const [picked, setPicked] = useState<Set<ClientId>>(() => new Set(initial.filter((c) => c.detected).map((c) => c.id)));
  const [cursorChoice, setCursorChoice] = useState<string>(CURSOR_MODELS[0]);
  const [cursorOther, setCursorOther] = useState("");
  const [step, setStep] = useState<Step>("idle");
  const [preview, setPreview] = useState<InstallResult[]>([]);
  const [progress, setProgress] = useState<Partial<Record<ClientId, Progress>>>({});
  const [results, setResults] = useState<Partial<Record<ClientId, InstallResult>>>({});
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState<ReadinessItem[] | null>(null);
  const [readyError, setReadyError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const cursorModel = cursorChoice === OTHER ? cursorOther : cursorChoice;
  const wantsCursor = picked.has("cursor");
  const cursorProblem = wantsCursor ? cursorModelProblem(cursorModel) : null;

  const loadReadiness = useCallback(async () => {
    setChecking(true); setReadyError(null);
    try { setReady(await client.readiness()); }
    catch (e) { setReadyError(e instanceof HelperError ? e.message : "The checklist could not be read."); }
    finally { setChecking(false); }
  }, [client]);

  useEffect(() => { void loadReadiness(); }, [loadReadiness]);

  const nothingInstalled = useMemo(() => clients.every((c) => !c.installed), [clients]);

  function toggle(id: ClientId) {
    setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    setStep("idle"); setPreview([]);
  }

  async function showPreview() {
    setError(null); setStep("previewing");
    try {
      setPreview(await client.install([...picked], { ...(wantsCursor ? { cursor_model: cursorModel.trim() } : {}), dry_run: true }));
      setStep("preview");
    } catch (e) {
      setError(e instanceof HelperError ? e.message : "The preview could not be made."); setStep("idle");
    }
  }

  async function runInstall() {
    setError(null); setStep("installing"); setResults({});
    const ids = [...picked];
    setProgress(Object.fromEntries(ids.map((id) => [id, "waiting" as Progress])));
    for (const id of ids) {
      setProgress((p) => ({ ...p, [id]: "working" }));
      try {
        const [r] = await client.install([id], id === "cursor" ? { cursor_model: cursorModel.trim() } : {});
        setResults((x) => ({ ...x, [id]: r }));
        setProgress((p) => ({ ...p, [id]: r?.ok ? "done" : "failed" }));
      } catch (e) {
        const msg = e instanceof HelperError ? e.message : "The install could not run.";
        setResults((x) => ({ ...x, [id]: { client: id, ok: false, changed_files: [], error: msg } }));
        setProgress((p) => ({ ...p, [id]: "failed" }));
      }
    }
    setStep("done");
    try { setClients(await client.clients()); } catch { /* the list stays as it was */ }
    await loadReadiness();
  }

  async function undo() {
    const ids = Object.entries(results).filter(([, r]) => r?.ok).map(([id]) => id as ClientId);
    if (!ids.length) return;
    setError(null);
    try {
      await client.uninstall(ids);
      setResults({}); setProgress({}); setStep("idle"); setPreview([]);
      setClients(await client.clients());
      await loadReadiness();
    } catch (e) { setError(e instanceof HelperError ? e.message : "Undo did not work."); }
  }

  const serverDown = ready?.find((i) => i.name === "server" && !i.ok);
  const noTestCommand = ready?.find((i) => /test command/i.test(i.name) && !i.ok);
  const busy = step === "previewing" || step === "installing";

  return (
    <div className="panel" role="tabpanel" aria-labelledby="tab-connect" id="panel-connect">
      <section className="card" aria-labelledby={`${uid}-which`}>
        <h2 id={`${uid}-which`}>Which tools do you use?</h2>
        <p className="small dim">Tick every one you want kell in. Tools found on this machine are ticked for you.</p>
        <fieldset style={{ border: 0, padding: 0, margin: "14px 0 0" }}>
          <legend className="sr-only">Tools to install into</legend>
          <div className="stack">
            {clients.map((c) => (
              <div key={c.id} className="stack">
                <label className="check">
                  <input type="checkbox" checked={picked.has(c.id)} onChange={() => toggle(c.id)} disabled={busy} />
                  <span>
                    {c.label}{" "}
                    <span className="status" data-s={c.detected ? "evidenced" : "unknown"}>{c.detected ? "found here" : "not found here"}</span>{" "}
                    {c.installed && <span className="status" data-s="successful">installed</span>}
                    <small>{gets(c)}</small>
                  </span>
                </label>
              </div>
            ))}
          </div>
        </fieldset>

        {wantsCursor && (
          <div className="field" style={{ marginTop: 18, maxWidth: 420 }}>
            <label htmlFor={`${uid}-cm`}>The model you have selected in Cursor</label>
            <select id={`${uid}-cm`} value={cursorChoice} onChange={(e) => { setCursorChoice(e.target.value); setStep("idle"); setPreview([]); }} aria-describedby={`${uid}-cmh`} disabled={busy}>
              {CURSOR_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              <option value={OTHER}>Other (type it)</option>
            </select>
            {cursorChoice === OTHER && (
              <input type="text" aria-label="Cursor model name" value={cursorOther} onChange={(e) => { setCursorOther(e.target.value); setStep("idle"); setPreview([]); }} disabled={busy} />
            )}
            <span id={`${uid}-cmh`} className="hint">“auto” is not a model. Without a model name, dispatch stays off for Cursor. The list may be out of date: choose Other and type the name Cursor shows.</span>
            {cursorProblem && <span className="notice" role="alert" data-tone="bad">{cursorProblem}</span>}
          </div>
        )}

        <div className="cluster" style={{ marginTop: 20 }}>
          {step !== "preview" && step !== "installing" && (
            <button type="button" className="btn-ink" onClick={showPreview} disabled={busy || picked.size === 0 || Boolean(cursorProblem)}>
              {step === "previewing" ? "Checking…" : "Install"}
            </button>
          )}
          {step === "preview" && (
            <>
              <button type="button" className="btn-ink" onClick={runInstall}>Confirm and install</button>
              <button type="button" className="btn-line" onClick={() => { setStep("idle"); setPreview([]); }}>Cancel</button>
            </>
          )}
          {picked.size === 0 && <span className="small dim">Tick at least one tool.</span>}
        </div>
        {error && <p className="notice" role="alert" data-tone="bad" style={{ marginTop: 12 }}>{error}</p>}
      </section>

      {(step === "preview") && (
        <section className="card" aria-labelledby={`${uid}-prev`}>
          <h2 id={`${uid}-prev`}>What will change</h2>
          <p className="small dim">Nothing has been written yet. This is what the installer says it would do.</p>
          <div className="stack" style={{ marginTop: 12 }}>
            {preview.map((p) => (
              <div key={p.client}>
                <h3>{clients.find((c) => c.id === p.client)?.label ?? p.client}</h3>
                {p.ok ? <pre className="diff">{p.diff || "No change is needed."}</pre> : <p className="notice" data-tone="bad">{p.error || "This tool cannot be installed."} {p.fix}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {(step === "installing" || step === "done") && (
        <section className="card" aria-labelledby={`${uid}-prog`} aria-live="polite">
          <h2 id={`${uid}-prog`}>Installing</h2>
          <ul className="checklist">
            {[...picked].map((id) => {
              const r = results[id]; const pr = progress[id] ?? "waiting";
              const label = clients.find((c) => c.id === id)?.label ?? id;
              return (
                <li key={id}>
                  <span className="status" data-s={pr === "done" ? "successful" : pr === "failed" ? "failed" : pr === "working" ? "executed" : "unknown"}>
                    {pr === "done" ? "done" : pr === "failed" ? "failed" : pr === "working" ? "working" : "waiting"}
                  </span>
                  <span>
                    <b style={{ fontWeight: 400 }}>{label}</b>
                    {pr === "done" && <span className="small dim"> {r?.changed_files?.length ? `Wrote ${r.changed_files.join(", ")}.` : "Already up to date."} {clients.find((c) => c.id === id)?.notes}</span>}
                    {pr === "failed" && <span className="fix"> {r?.error} {r?.fix}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
          {step === "done" && Object.values(results).some((r) => r?.ok) && (
            <div className="cluster" style={{ marginTop: 14 }}>
              <button type="button" className="btn-line" onClick={undo}>Undo</button>
              <span className="small dim">Puts back what this install changed.</span>
            </div>
          )}
        </section>
      )}

      {nothingInstalled && step === "idle" && (
        <p className="notice">Nothing is installed yet. Choose your tools above and press Install.</p>
      )}

      <section className="card" aria-labelledby={`${uid}-ready`}>
        <div className="cluster" style={{ justifyContent: "space-between" }}>
          <h2 id={`${uid}-ready`} style={{ marginBottom: 0 }}>Readiness</h2>
          <button type="button" className="btn-line" onClick={loadReadiness} disabled={checking}>{checking ? "Checking…" : "Check again"}</button>
        </div>
        {serverDown && (
          <p className="notice" role="alert" data-tone="bad" style={{ marginTop: 12 }}>
            The kell server is not answering. {serverDown.detail}. {serverDown.fix}
          </p>
        )}
        {noTestCommand && (
          <p className="notice" style={{ marginTop: 12 }}>No test command was found in the repository, so cheap-model dispatch is skipped until one is set.</p>
        )}
        {readyError && <p className="notice" role="alert" data-tone="bad" style={{ marginTop: 12 }}>{readyError}</p>}
        {ready && (
          <ul className="checklist" style={{ marginTop: 14 }}>
            {ready.map((i) => (
              <li key={i.name}>
                <span className="status" data-s={i.ok ? "successful" : "failed"}>{i.ok ? "ok" : "missing"}</span>
                <span>
                  <b style={{ fontWeight: 400 }}>{i.name}</b> <span className="small dim">{i.detail}</span>
                  {!i.ok && i.fix && <span className="fix"><br />Fix: {i.fix}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!ready && !readyError && <p className="small dim" style={{ marginTop: 12 }}>Checking…</p>}
      </section>

      <section className="card">
        <h2>What leaves your machine</h2>
        <p>Installing only changes files on this machine. Once it is installed, the hooks look things up: in Cursor, the hook sends your prompt (up to 1,500 characters) and the repository’s <code>claims.md</code> to the kell server. Cheap-model dispatch sends the task to the model providers you list under Keys &amp; models.</p>
        <p className="small dim">Your own Claude Code or Codex subscription stays yours. This page never asks for, stores or passes on a Claude or Codex login.</p>
      </section>
    </div>
  );
}
