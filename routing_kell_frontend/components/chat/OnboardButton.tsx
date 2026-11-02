"use client";
import { useRef, useState } from "react";

/** "Onboard Your Agent": one pill. Pressing it registers a new agent name, and copies the instructions to paste into the agent, which then
 * connects itself. The three round marks are plain shapes, not any company's logo. */
export default function OnboardButton({ onOnboard, className = "" }: { onOnboard: () => Promise<string>; className?: string }) {
  const [state, setState] = useState<"idle" | "busy" | "copied" | "failed">("idle");
  const [shown, setShown] = useState("");
  const timer = useRef<number | undefined>(undefined);

  async function go() {
    if (state === "busy") return;
    setState("busy"); setShown("");
    try {
      const text = await onOnboard();
      try { await navigator.clipboard.writeText(text); setState("copied"); }
      catch { setShown(text); setState("failed"); }   // the browser refused the clipboard: show the text to copy by hand
    } catch { setState("idle"); return; }              // onOnboard reports its own error in the room
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState((s) => (s === "copied" ? "idle" : s)), 2400);
  }

  return (
    <div className={`dg-onboard-wrap ${className}`}>
      <button type="button" className="dg-onboard" onClick={() => void go()} data-state={state} aria-live="polite"
        aria-label="Onboard your agent: copies instructions to paste into it">
        <span className="dg-onboard-marks" aria-hidden="true">
          <i><svg viewBox="0 0 16 16"><path d="M8 1.5l1.4 4.1 4.1 1.4-4.1 1.4L8 12.5 6.6 8.4 2.5 7l4.1-1.4z" fill="currentColor" /></svg></i>
          <i><svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg></i>
          <i><svg viewBox="0 0 16 16"><path d="M3 4l4 4-4 4M8.5 12.5h4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></i>
        </span>
        <span className="dg-onboard-label">{state === "copied" ? "Copied. Paste it into your agent" : state === "busy" ? "Setting up…" : "Onboard Your Agent"}</span>
        <svg className="dg-onboard-copy" viewBox="0 0 16 16" aria-hidden="true">
          {state === "copied"
            ? <path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            : <><rect x="5.5" y="5.5" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M10.5 3.5v-.4A1.6 1.6 0 008.9 1.5H3.6A1.6 1.6 0 002 3.1v5.3a1.6 1.6 0 001.6 1.6h.4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></>}
        </svg>
      </button>
      {state === "failed" && (
        <label className="dg-onboard-manual small">The browser blocked copying. Select and copy this text, then paste it into your agent:
          <textarea readOnly rows={6} value={shown} onFocus={(e) => e.currentTarget.select()} />
        </label>
      )}
    </div>
  );
}
