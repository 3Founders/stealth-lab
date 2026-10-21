"use client";
import { useEffect, useRef, useState } from "react";
import { track } from "@/lib/analytics";
import { SETUP_COMMAND, defaultInstallPlatform, installPrompt, type InstallPlatform } from "@/lib/install-commands";

// The one command a developer runs to set keळ up in Claude Code and Cursor.
export default function InstallCommand() {
  const [done, setDone] = useState(false);
  const [platform, setPlatform] = useState<InstallPlatform>("unix");
  const t = useRef<number>(undefined);

  useEffect(() => {
    setPlatform(defaultInstallPlatform(window.navigator.userAgent));
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(SETUP_COMMAND);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = SETUP_COMMAND; document.body.appendChild(ta); ta.select();
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
        <span>Needs Node.js 18.17+</span>
      </div>
      <div className="install-body">
        <pre aria-label="Installation command"><span style={{ display: "block" }}><span className="p">{installPrompt(platform)}</span>{SETUP_COMMAND}</span></pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy installation command">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        Sets keळ up in Claude Code and Cursor, then tells you anything still missing.{" "}
        <a href="/docs#getting-started" style={{ textDecoration: "underline" }}>Setup details</a>
      </div>
    </div>
  );
}
