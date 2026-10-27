"use client";
import { useRef, useState } from "react";

/** A command to copy, in the website's install-box style. Only ever used for fixed commands, never for anything secret. */
export default function CopyCommand({ command, label }: { command: string; label: string }) {
  const [done, setDone] = useState(false);
  const t = useRef<number | undefined>(undefined);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = command;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install">
      <div className="install-body">
        <pre aria-label={label}><span style={{ display: "block" }}>{command}</span></pre>
        <button type="button" className="copy" data-done={done} onClick={copy} aria-label={`Copy: ${label}`}>
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
    </div>
  );
}
