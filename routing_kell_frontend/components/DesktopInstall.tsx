"use client";
import { useRef, useState } from "react";
import { track } from "@/lib/analytics";
import { DESKTOP_INSTALL_COMMAND } from "@/lib/install-commands";

/**
 * The one line that installs kell for Windows, and the site's primary way to get the desktop app. It downloads the
 * newest installer through /download, runs it for the current user and opens kell. The installer file stays available
 * at /download for anyone whose machine blocks `iex`; that link is the small fallback in the foot, not the offer.
 */
export default function DesktopInstall() {
  const [done, setDone] = useState(false);
  const timer = useRef<number>(undefined);

  async function copy() {
    try {
      await navigator.clipboard.writeText(DESKTOP_INSTALL_COMMAND);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = DESKTOP_INSTALL_COMMAND; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setDone(true);
    track("desktop_install_copy");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDone(false), 1800);
  }

  return (
    <div className="install">
      <div className="install-head caption">
        <span>One-line install</span>
        <span>Windows 10 or 11 · PowerShell</span>
      </div>
      <div className="install-body">
        <pre aria-label="Desktop installation command"><span className="p">{"PS> "}</span>{DESKTOP_INSTALL_COMMAND}</pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy the installation command">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        Downloads kell, installs it for you and opens it. Nothing else on your machine changes.{" "}
        <a href="/download" style={{ textDecoration: "underline" }}>Prefer a file? Download the installer</a>.
      </div>
    </div>
  );
}
