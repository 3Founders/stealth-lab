"use client";
import { useRef, useState } from "react";
import { track } from "@/lib/analytics";

/**
 * Execution itself happens through the MCP tools (see /docs#how-to-use — `reproduce_procedure`
 * et al.), not a browser button; there is no safe public "run it now" endpoint to wire up.
 * So "try this way" is the real, honest action: the exact call your coding agent would make,
 * ready to copy. Mirrors InstallCommand's copy pattern for visual and behavioral consistency.
 */
export default function TryThisWay({ procedureId }: { procedureId: string }) {
  const [done, setDone] = useState(false);
  const t = useRef<number>(undefined);
  const line = `reproduce_procedure(procedure_id="${procedureId}")`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(line);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = line; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    track("install_copy");
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install">
      <div className="install-head caption"><span>Try this way</span><span>Via MCP</span></div>
      <div className="install-body">
        <pre aria-label="MCP tool call"><span className="p">$ </span>{line}</pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy MCP tool call">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        Run from your coding agent once keळ’s MCP server is connected. <a href="/docs#how-to-use" style={{ textDecoration: "underline" }}>How execution works</a>
      </div>
    </div>
  );
}
