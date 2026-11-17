"use client";
import { useEffect, useRef, useState } from "react";
import { track } from "@/lib/analytics";
import { DESKTOP_INSTALL_COMMAND, DESKTOP_INSTALL_COMMAND_LINUX } from "@/lib/install-commands";

type OS = "windows" | "linux";
const OSES: Record<OS, { label: string; needs: string; prompt: string; command: string; file: string; fileLabel: string; note: string }> = {
  windows: {
    label: "Windows", needs: "Windows 10 or 11 · PowerShell", prompt: "PS> ", command: DESKTOP_INSTALL_COMMAND,
    file: "/download", fileLabel: "Prefer a file? Download the installer",
    note: "Downloads kell, installs it for you and opens it. Nothing else on your machine changes.",
  },
  linux: {
    label: "Linux", needs: "x86_64 · any terminal", prompt: "$ ", command: DESKTOP_INSTALL_COMMAND_LINUX,
    file: "/download?os=linux&format=deb", fileLabel: "Debian or Ubuntu? Download the .deb",
    note: "Puts the kell AppImage in ~/.local/bin, adds it to your app menu and opens it. No root needed.",
  },
};

/**
 * The one line that installs kell for Windows or Linux, and the site's primary way to get the desktop app. It downloads the
 * newest build through /download, installs it for the current user and opens kell. The files stay available at /download
 * (Windows) and /download?os=linux[&format=deb] for anyone who prefers them; that link is the small fallback in the foot.
 */
export default function DesktopInstall() {
  const [os, setOs] = useState<OS>("windows");
  const [done, setDone] = useState(false);
  const timer = useRef<number>(undefined);
  // A Linux browser sees the Linux line first (Android is not desktop Linux).
  useEffect(() => { if (/Linux/i.test(navigator.userAgent) && !/Android/i.test(navigator.userAgent)) setOs("linux"); }, []);
  const o = OSES[os];

  async function copy() {
    try {
      await navigator.clipboard.writeText(o.command);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = o.command; document.body.appendChild(ta); ta.select();
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
        <span role="tablist" aria-label="Operating system" style={{ display: "inline-flex", gap: 12 }}>
          {(Object.keys(OSES) as OS[]).map((k) => (
            <button key={k} type="button" role="tab" aria-selected={os === k} onClick={() => { setOs(k); setDone(false); }}
              style={{ background: "none", border: 0, padding: 0, font: "inherit", color: "inherit", cursor: "pointer",
                textDecoration: os === k ? "underline" : "none", textUnderlineOffset: 4, opacity: os === k ? 1 : 0.6 }}>
              {OSES[k].label}
            </button>
          ))}
        </span>
        <span>{o.needs}</span>
      </div>
      <div className="install-body">
        <pre aria-label="Desktop installation command"><span className="p">{o.prompt}</span>{o.command}</pre>
        <button className="copy" data-done={done} onClick={copy} aria-label="Copy the installation command">
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <div className="install-foot">
        {o.note}{" "}
        <a href={o.file} style={{ textDecoration: "underline" }}>{o.fileLabel}</a>.
      </div>
    </div>
  );
}
