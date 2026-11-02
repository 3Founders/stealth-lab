"use client";
import { useEffect, useRef, useState } from "react";
import { ChatGPTLogo, ClaudeLogo, CursorLogo } from "@/components/chat/brand";
import Popover from "@/components/chat/Popover";
import { useSlideHighlight } from "@/components/chat/useSlideHighlight";

export type OnboardTarget = "code" | "claude-web" | "chatgpt";
export interface OnboardResult { text: string; openUrl?: string }

const CHOICES: Array<{ id: OnboardTarget; label: string; hint: string; logos: React.ReactNode }> = [
  { id: "code", label: "Claude Code or Cursor", hint: "Copies one message. Paste it into your agent and it connects itself.", logos: <><ClaudeLogo size={18} /><CursorLogo size={18} /></> },
  { id: "claude-web", label: "Claude on the web", hint: "Copies the connector address and opens Claude's connector page. You press Connect there.", logos: <ClaudeLogo size={18} /> },
  { id: "chatgpt", label: "ChatGPT", hint: "Copies the connector address and opens ChatGPT's connector page. You press Connect there.", logos: <ChatGPTLogo size={18} /> },
];

/**
 * "Connect agent": one small pill with the logos of the tools it works with. Pressing it asks which tool, registers a new agent name
 * on the account, and copies what to paste (or, for Claude on the web and ChatGPT, the connector address, and opens their page).
 * The two web tools cannot be connected without the person pressing Connect inside them and signing in: that approval is theirs.
 */
export default function OnboardButton({ onOnboard, className = "" }: { onOnboard: (target: OnboardTarget) => Promise<OnboardResult>; className?: string }) {
  const [menu, setMenu] = useState(false);
  const [state, setState] = useState<"idle" | "busy" | "copied" | "failed">("idle");
  const [done, setDone] = useState<OnboardTarget | null>(null);
  const [shown, setShown] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const slide = useSlideHighlight();
  const trigger = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!menu) return;
    const off = (e: MouseEvent) => { const t = e.target as Node; if (box.current && !box.current.contains(t) && !pop.current?.contains(t)) setMenu(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", off); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [menu]);

  async function go(target: OnboardTarget) {
    if (state === "busy") return;
    setMenu(false); setState("busy"); setShown(""); setDone(target);
    try {
      const r = await onOnboard(target);
      try { await navigator.clipboard.writeText(r.text); setState("copied"); }
      catch { setShown(r.text); setState("failed"); }   // the browser refused the clipboard: show the text to copy by hand
      if (r.openUrl) window.open(r.openUrl, "_blank", "noopener");
    } catch { setState("idle"); return; }               // onOnboard reports its own error in the room
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState((s) => (s === "copied" ? "idle" : s)), 3200);
  }

  const label = state === "copied" ? (done === "code" ? "Copied. Paste it in your agent" : "Copied. Press Connect there") : state === "busy" ? "One moment…" : "Connect agent";
  return (
    <div className={`dg-onboard-wrap ${className}`} ref={box}>
      <button ref={trigger} type="button" className="dg-onboard" onClick={() => setMenu((m) => !m)} data-state={state} aria-haspopup="menu" aria-expanded={menu} aria-live="polite"
        aria-label="Connect an agent such as Claude Code, Cursor, Claude on the web or ChatGPT">
        <span className="dg-onboard-marks" aria-hidden="true"><i><ClaudeLogo size={16} /></i><i><CursorLogo size={16} /></i><i><ChatGPTLogo size={16} /></i></span>
        <span className="dg-onboard-label">{label}</span>
        <svg className="dg-onboard-copy" viewBox="0 0 16 16" aria-hidden="true">
          {state === "copied"
            ? <path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            : <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />}
        </svg>
      </button>
      <Popover anchor={trigger} open={menu} matchWidth popRef={pop}>
        <div className="dg-onboard-menu" role="menu" aria-label="Which agent?" ref={slide('button[role="menuitem"]', 'button[role="menuitem"]:focus')}>
          {CHOICES.map((c) => (
            <button key={c.id} type="button" role="menuitem" onClick={() => void go(c.id)}>
              <span className="dg-onboard-logos" aria-hidden="true">{c.logos}</span>
              <span className="dg-onboard-choice"><b>{c.label}</b><span className="small dim">{c.hint}</span></span>
            </button>
          ))}
        </div>
      </Popover>
      {state === "failed" && (
        <label className="dg-onboard-manual small">The browser blocked copying. Select and copy this text, then paste it into your agent:
          <textarea readOnly rows={6} value={shown} onFocus={(e) => e.currentTarget.select()} />
        </label>
      )}
    </div>
  );
}
