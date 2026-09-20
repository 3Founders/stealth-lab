"use client";
import { useRef, useState } from "react";
import { track } from "@/lib/analytics";

// The real setup path today (README "Quick install" / packaging/README.md). There is no hosted installer or PyPI release yet.
const LINES = [
  "git clone https://github.com/3Founders/stealth-lab && cd stealth-lab",
  "pip install -e packaging/",
];

export default function InstallCommand() {
  const [done, setDone] = useState(false);
  const t = useRef<number>(undefined);

  async function copy() {
    const text = LINES.join("\n");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    track("install_copy");
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install" id="install">
      <div className="install-head caption"><span>Get started</span><span>Python 3.12+</span></div>
      <div className="install-body">
        <pre aria-label="Installation commands">
          {LINES.map((l) => (<span key={l} style={{ display: "block" }}><span className="p">$ </span>{l}</span>))}
        </pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy installation commands">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        Installs from source — not yet on PyPI. Needs Postgres 15+ with pgvector for the MCP server.{" "}
        <a href="/docs#getting-started" style={{ textDecoration: "underline" }}>Full setup</a>
      </div>
    </div>
  );
}
