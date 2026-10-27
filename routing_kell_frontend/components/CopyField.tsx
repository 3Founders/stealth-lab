"use client";
import { useRef, useState } from "react";
import { track, type EventName } from "@/lib/analytics";

// A read-only block with a Copy button, in the same frame as the install command.
export default function CopyField({ label, value, event }: { label: string; value: string; event: EventName }) {
  const [done, setDone] = useState(false);
  const t = useRef<number>(undefined);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = value; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    track(event);
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install">
      <div className="install-head caption"><span>{label}</span></div>
      <div className="install-body">
        <pre aria-label={label}>{value}</pre>
        <button className="copy" data-done={done} onClick={copy} aria-label={`Copy ${label}`}>
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
    </div>
  );
}
