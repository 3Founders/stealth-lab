"use client";
import { createContext, useContext, useState } from "react";
import { fileKind, formatSize, type RoomFile } from "@/lib/chat/files";

/** What a file card needs from the room around it: how to fetch the bytes. Provided by RoomView. */
export const FilesContext = createContext<{ fetchBlob: (fileId: string) => Promise<Blob> } | null>(null);

/**
 * A file in a message, as a card: a type label, the name, the size, and one button. Nothing is fetched until it is pressed. A picture,
 * audio, video or PDF opens in a new tab; anything else downloads. The type shown is the one the sender's device named; the server
 * decided the real type when it stored the file and serves everything else as a download only.
 */
export default function FileCard({ file }: { file: RoomFile }) {
  const ctx = useContext(FilesContext);
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  const [why, setWhy] = useState("");
  const open = /^(image\/(png|jpeg|gif|webp)|application\/pdf|audio\/|video\/)/.test(file.mime);

  async function go() {
    if (!ctx || state === "busy") return;
    setState("busy"); setWhy("");
    try {
      const blob = await ctx.fetchBlob(file.id);
      const url = URL.createObjectURL(open ? blob : new Blob([blob], { type: "application/octet-stream" }));
      if (open) window.open(url, "_blank", "noopener");
      else { const a = document.createElement("a"); a.href = url; a.download = file.name; document.body.appendChild(a); a.click(); a.remove(); }
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setState("idle");
    } catch (err) { setWhy(err instanceof Error ? err.message : "That didn't work."); setState("error"); }
  }

  return (
    <div className="dg-file" data-state={state}>
      <span className="dg-file-kind" aria-hidden="true">{fileKind(file)}</span>
      <span className="dg-file-meta"><b title={file.name}>{file.name}</b><span className="small dim">{formatSize(file.size)}</span></span>
      <button type="button" className="dg-artifact-btn" onClick={() => void go()} disabled={!ctx || state === "busy"} aria-label={`${open ? "Open" : "Download"} ${file.name}`}>
        {state === "busy" ? "…" : open ? "Open" : "Download"}
      </button>
      {state === "error" && <span className="dg-file-err small" role="status">{why}</span>}
    </div>
  );
}
