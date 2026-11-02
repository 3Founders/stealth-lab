"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ModelKeys from "@/components/models/ModelKeys";
import OnboardButton from "@/components/chat/OnboardButton";

export interface AddAgentDialogProps {
  open: boolean; onClose: () => void;
  /** The room already has as many in-tab agents as it can run. */
  full: boolean;
  /** The signed-in account: its model keys live in this browser, per account. */
  subject: string;
  kind: "mine" | "tab"; onKind: (k: "mine" | "tab") => void;
  name: string; onName: (n: string) => void;
  error: string;
  onSubmit: (e: React.FormEvent) => void;
  /** A model or key changed here: refresh what backs the in-tab agents. */
  onModelChanged?: () => void;
  /** Registers a new agent name and returns the text to paste into the agent. */
  onOnboard?: () => Promise<string>;
}

/** "+ Add agent": a popup over the chat, not a form squeezed into the side panel. Same native <dialog> as Settings and Manage rooms. */
export default function AddAgentDialog(p: AddAgentDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [modelsOpen, setModelsOpen] = useState(false);

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
          <label htmlFor="dg-agent-name">Name{p.kind === "tab" ? " (optional)" : ""}</label>
          <input id="dg-agent-name" ref={input} value={p.name} onChange={(e) => p.onName(e.target.value)} placeholder={p.kind === "mine" ? "web-chat" : "reviewer"} maxLength={40} autoComplete="off" />
          <fieldset>
            <legend>What kind of agent?</legend>
            <label><input type="radio" name="dg-kind" checked={p.kind === "tab"} onChange={() => p.onKind("tab")} disabled={p.full} /> Kell agent</label>
            <label><input type="radio" name="dg-kind" checked={p.kind === "mine"} onChange={() => p.onKind("mine")} /> My own agent</label>
          </fieldset>
          {p.kind === "mine" && p.onOnboard && <OnboardButton onOnboard={p.onOnboard} />}
          {p.kind === "mine" && (
            <p className="small dim" style={{ margin: 0 }}>
              Your agent joins through kell&rsquo;s MCP (Claude Code, Cursor and the like). It can read and post in this room with
              room_list, room_read and room_post, and shows slides or a small page when it posts a fenced <code>```html</code> block.{" "}
              <Link href="/docs#agents" style={{ textDecoration: "underline" }}>How to connect it</Link>.
            </p>
          )}
          {p.kind === "tab" && (
            <details className="dg-agent-models" open={modelsOpen} onToggle={(e) => setModelsOpen((e.currentTarget as HTMLDetailsElement).open)}>
              <summary><b>Model key and model</b> <span className="dim small">- your key, or kell&rsquo;s hosted models</span></summary>
              <ModelKeys subject={p.subject} onDone={p.onModelChanged} />
            </details>
          )}
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
