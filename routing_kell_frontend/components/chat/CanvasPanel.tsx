"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Markdown from "@/components/chat/Markdown";
import PanelShell from "@/components/chat/PanelShell";
import HtmlViewer, { type ViewerMode } from "@/components/chat/HtmlViewer";
import { addVersion, fileNameFor, viewOf, type Canvas, type CanvasVersion } from "@/lib/chat/canvas";

/**
 * The canvas kept on this device (temporary rooms, and rooms that are not on your account yet): what an agent wrote, a page, code or a
 * document, as a read-only panel with "Version N of M", Copy, Download, Full screen and Close. Rooms on your account use the shared,
 * editable DocPanel instead. Laid out after Open WebUI's artifacts panel (https://github.com/open-webui/open-webui); no code copied. Pages
 * run in a sandbox that cannot reach this site or your account.
 */
interface Entry { canvas: Canvas; v: CanvasVersion }

export default function CanvasPanel({ open, onClose, canvases, room, selectedId, onChange, onSeen }: {
  open: boolean; onClose: () => void; canvases: Canvas[]; room: string; selectedId: string | null;
  /** A canvas with a new version (you edited it here): the owner saves it. */
  onChange?: (c: Canvas) => void;
  /** This version of this canvas is on screen: it no longer counts as new. */
  onSeen?: (canvasId: string, n: number) => void;
}) {
  const entries = useMemo<Entry[]>(() => canvases.filter((c) => c.room === room).flatMap((c) => c.versions.map((v) => ({ canvas: c, v }))).sort((a, b) => a.v.at - b.v.at), [canvases, room]);
  const [idx, setIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const [viewer, setViewer] = useState<ViewerMode>("browser");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const timer = useRef<number | undefined>(undefined);

  // The newest step is shown when one is added; asking for a canvas by title shows its newest version.
  const count = entries.length;
  const lastCount = useRef(0);
  useEffect(() => { if (count > lastCount.current) setIdx(count - 1); else setIdx((i) => Math.min(i, Math.max(0, count - 1))); lastCount.current = count; }, [count]);
  useEffect(() => {
    if (!selectedId) return;
    for (let i = entries.length - 1; i >= 0; i--) if (entries[i].canvas.id === selectedId) { setIdx(i); break; }
  }, [selectedId, entries]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const shown = count > 0 ? entries[Math.min(idx, count - 1)] : null;
  useEffect(() => { if (open && shown) onSeen?.(shown.canvas.id, shown.v.n); }, [open, shown, onSeen]);
  useEffect(() => { setEditing(false); }, [shown?.canvas.id, shown?.v.n]);

  if (!open || count === 0) return null;
  const cur = entries[Math.min(idx, count - 1)];
  const newest = cur.v.n === cur.canvas.versions[cur.canvas.versions.length - 1].n;
  const text = cur.v.text;
  const view = viewOf(cur.canvas.kind, text);
  const isPage = view === "page";
  const flashCopied = () => { setCopied(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setCopied(false), 2000); };
  const copy = () => { void navigator.clipboard.writeText(text).then(flashCopied, () => {}); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: isPage ? "text/html" : "text/plain" }));
    const a = document.createElement("a"); a.href = url; a.download = fileNameFor(cur.canvas); document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  };

  return (
    <PanelShell open label="Canvas">
      <div className="dg-canvas-head">
        <div className="dg-canvas-headrow">
          <div className="dg-canvas-ver" dir="ltr">
            <button type="button" className="dg-cv-icon" aria-label="Previous version" disabled={count <= 1} onClick={() => setIdx((i) => Math.max(i - 1, 0))}>
              <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" /></svg>
            </button>
            <div className="dg-cv-label">Version {Math.min(idx, count - 1) + 1} of {count}</div>
            <button type="button" className="dg-cv-icon" aria-label="Next version" disabled={count <= 1} onClick={() => setIdx((i) => Math.min(i + 1, count - 1))}>
              <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" /></svg>
            </button>
          </div>
          <div className="dg-canvas-tools">
            <button type="button" className="dg-cv-chip dg-cv-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            <button type="button" className="dg-cv-chip dg-cv-sq" aria-label="Download" title="Download" onClick={download}>
              <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>
            </button>
            {isPage && (
              <span className="dg-doc-seg" role="tablist" aria-label="Viewer">
                <button type="button" role="tab" aria-selected={viewer === "browser"} onClick={() => setViewer("browser")}>Browser</button>
                <button type="button" role="tab" aria-selected={viewer === "page"} onClick={() => setViewer("page")}>Page</button>
              </span>
            )}
            {onChange && newest && !editing && <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => { setDraft(text); setEditing(true); }}>Edit</button>}
          </div>
        </div>
        <button type="button" className="dg-cv-close" aria-label="Close" onClick={onClose}>
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
        </button>
      </div>
      <div className="dg-canvas-body">
        {editing ? (
          <div className="dg-canvas-edit">
            <textarea className="dg-doc-text" data-kind={cur.canvas.kind} value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={`Edit ${cur.canvas.title}`} spellCheck={false} />
            <div className="dg-canvas-editbar">
              <span className="small dim">Saved as a new version, on this device.</span>
              <button type="button" className="btn-line dg-mini-btn" onClick={() => setEditing(false)}>Cancel</button>
              <button type="button" className="btn-ink dg-mini-btn" disabled={draft === text} onClick={() => { onChange?.(addVersion(cur.canvas, draft, "you")); setEditing(false); }}><span>Save version</span></button>
            </div>
          </div>
        ) : isPage ? (
          <HtmlViewer html={text} title={cur.canvas.title} mode={viewer} />
        ) : view === "markdown" ? (
          <div className="dg-canvas-doc" tabIndex={0} aria-label={cur.canvas.title}><Markdown text={text} /></div>
        ) : (
          <pre className="dg-canvas-pre" data-kind={cur.canvas.kind} tabIndex={0} aria-label={cur.canvas.title}>{text}</pre>
        )}
      </div>
    </PanelShell>
  );
}
