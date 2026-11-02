"use client";
import { useEffect, useRef } from "react";
import OnboardButton, { type OnboardResult, type OnboardTarget } from "@/components/chat/OnboardButton";

export interface AddAgentDialogProps {
  open: boolean; onClose: () => void;
  /** The room already has as many added agents as it can have. */
  full: boolean;
  name: string; onName: (n: string) => void;
  /** What the new agent should do: its own instructions, on top of being kell. */
  about: string; onAbout: (t: string) => void;
  error: string;
  onSubmit: (e: React.FormEvent) => void;
  /** Registers a new name for an agent that runs on the person's own machine and returns the text to paste into it. */
  onOnboard?: (target: OnboardTarget) => Promise<OnboardResult>;
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
    <dialog data-lenis-prevent ref={dialog} className="dg-settings dg-addagent" aria-labelledby="dg-addagent-title" onCancel={(e) => { e.preventDefault(); close(); }} onClick={(e) => { if (e.target === dialog.current) close(); }}>
      <form className="dg-set-frame" onSubmit={p.onSubmit}>
        <div className="dg-set-head">
          <h2 id="dg-addagent-title">Add an agent</h2>
          <button type="button" className="dg-set-x" onClick={close} aria-label="Close">Close</button>
        </div>
        <div className="dg-addagent-body">
          <label htmlFor="dg-agent-name">Name (optional)</label>
          <input id="dg-agent-name" ref={input} value={p.name} onChange={(e) => p.onName(e.target.value)} placeholder="reviewer" maxLength={40} autoComplete="off" />
          <label htmlFor="dg-agent-about">What should it do? (optional)</label>
          <textarea id="dg-agent-about" value={p.about} onChange={(e) => p.onAbout(e.target.value)} rows={3} maxLength={1500} placeholder="Review my pull requests. Be blunt and short." />
          <p className="small dim" style={{ margin: 0 }}>Don't put keys, tokens or passwords here: this text is sent to the model with every question.</p>
          <p className="small dim" style={{ margin: 0 }}>
            Every agent is kell. A new one is another instance of it: same models and tools, a name of its own. Mention it with <code>@name</code> and it answers in this room.
          </p>
          {p.full && <p className="small dim" style={{ margin: 0 }}>This room has the most added agents it can have. Remove one to add another.</p>}
          {p.onOnboard && (
            <div className="dg-addagent-connect">
              <span className="small dim">Already use Claude, Cursor or ChatGPT? Connect it here:</span>
              <OnboardButton onOnboard={p.onOnboard} />
            </div>
          )}
          {p.error && <p className="dg-err" role="alert">{p.error}</p>}
          <div className="dg-form-row">
            <button type="submit" className="btn-ink dg-mini-btn" disabled={p.full}><span>Add</span></button>
            <button type="button" className="btn-line dg-mini-btn" onClick={close}>Cancel</button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
