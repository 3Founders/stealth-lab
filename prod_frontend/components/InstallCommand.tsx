"use client";
import { useEffect, useRef, useState } from "react";
import { track } from "@/lib/analytics";
import {
  INSTALL_PLATFORMS, defaultInstallPlatform, installCommand, type InstallPlatform,
} from "@/lib/install-commands";

// Connects the visitor's coding agent (Claude Code, Cursor, VS Code, Windsurf,
// Codex, Claude Desktop) to the hosted keळ MCP server. Nothing else is
// installed locally.
export default function InstallCommand() {
  const [done, setDone] = useState(false);
  const [platform, setPlatform] = useState<InstallPlatform>("unix");
  const [origin, setOrigin] = useState(process.env.NEXT_PUBLIC_SITE_URL || "");
  const t = useRef<number>(undefined);

  useEffect(() => {
    setOrigin(window.location.origin);
    setPlatform(defaultInstallPlatform(window.navigator.userAgent));
  }, []);

  const command = installCommand(platform, origin);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = command; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    track("install_copy");
    window.clearTimeout(t.current);
    t.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install" id="install">
      <div className="install-head caption">
        <span>Get started</span>
        <span role="tablist" aria-label="Your system" style={{ display: "flex", gap: 14 }}>
          {INSTALL_PLATFORMS.map((p) => (
            <button
              key={p.id} type="button" role="tab" aria-selected={platform === p.id}
              onClick={() => { setPlatform(p.id); setDone(false); }}
              style={{
                background: "none", border: 0, padding: 0, font: "inherit", cursor: "pointer",
                color: platform === p.id ? "var(--ink)" : "inherit",
                textDecoration: platform === p.id ? "underline" : "none",
              }}
            >
              {p.label}
            </button>
          ))}
        </span>
      </div>
      <div className="install-body">
        <pre aria-label="Installation command"><span style={{ display: "block" }}><span className="p">{platform === "windows" ? "PS> " : "$ "}</span>{command}</span></pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy installation command">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        Adds keळ to Claude Code, Cursor, VS Code, Windsurf, Codex and Claude Desktop. Nothing else runs on your machine.{" "}
        <a href="/docs#getting-started" style={{ textDecoration: "underline" }}>Setup details</a>
      </div>
    </div>
  );
}
