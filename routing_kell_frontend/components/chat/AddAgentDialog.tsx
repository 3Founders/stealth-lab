"use client";
import { useEffect, useRef } from "react";

export interface AddAgentDialogProps {
  open: boolean; onClose: () => void;
  /** The room already has as many in-tab agents as it can run. */
  full: boolean;
  kind: "mine" | "tab"; onKind: (k: "mine" | "tab") => void;
  name: string; onName: (n: string) => void;
  error: string;
  onSubmit: (e: React.FormEvent) => void;
}

/** "+ Add agent": a popup over the chat, not a form squeezed into the side panel. Same native <dialog> as Settings and Manage rooms. */
export default function AddAgentDialog(p: AddAgentDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (p.open && !d.open) { d.showModal(); requestAnimationFrame(() => input.current?.focus()); }
    if (!p.open && d.open) d.close();
  }, [p.open]);

  function close() {
    const d = dialog.current;
    if (!d || !d.open) { p.onClose(); return; }
    const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    d.setAttribute("data-closing", "");
    window.setTimeout(() => { d.removeAttribute("data-closing"); p.onClose(); }, calm ? 0 : 240);
  }

  return (
    <dialog ref={dialog} className="dg-settings dg-addagent" aria-labelledby="dg-addagent-title" onCancel={(e) => { e.preventDefault(); close(); }} onClick={(e) => { if (e.target === dialog.current) close(); }}>
      <form className="dg-set-frame" onSubmit={p.onSubmit}>
        <div className="dg-set-head">
          <h2 id="dg-addagent-title">Add an agent</h2>
          <button type="button" className="dg-set-x" onClick={close} aria-label="Close">Close</button>
        </div>
        <div className="dg-addagent-body">
          <fieldset>
            <legend>What kind of agent?</legend>
            <label><input type="radio" name="dg-kind" checked={p.kind === "mine"} onChange={() => p.onKind("mine")} /> My own agent</label>
            <label><input type="radio" name="dg-kind" checked={p.kind === "tab"} onChange={() => p.onKind("tab")} disabled={p.full} /> A model in this tab</label>
          </fieldset>
          <label htmlFor="dg-agent-name">Name{p.kind === "tab" ? " (optional)" : ""}</label>
          <input id="dg-agent-name" ref={input} value={p.name} onChange={(e) => p.onName(e.target.value)} placeholder={p.kind === "mine" ? "web-chat" : "reviewer"} maxLength={40} autoComplete="off" />
          {p.error && <p className="dg-err" role="alert">{p.error}</p>}
          <div className="dg-form-row">
            <button type="submit" className="btn-ink dg-mini-btn"><span>Add</span></button>
            <button type="button" className="btn-line dg-mini-btn" onClick={close}>Cancel</button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
