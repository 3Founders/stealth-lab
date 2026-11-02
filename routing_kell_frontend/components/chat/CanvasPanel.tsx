"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { fileNameFor, latest, restoreVersion, addVersion, reviseRequest, type Canvas } from "@/lib/chat/canvas";

/**
 * The canvas: documents, code and pages that agents write in this room, as a panel beside the chat. Pick one, read it, edit it, step back to
 * an earlier version, copy it, download it, or ask an agent to revise it. Everything is stored on this device, encrypted; a page (kind html)
 * can be previewed in a sandbox that cannot reach this page or your account.
 */
export default function CanvasPanel({ open, onClose, canvases, room, selectedId, onSelect, onChange, onDelete, onCreate, onRevise }: {
  open: boolean; onClose: () => void;
  canvases: Canvas[]; room: string; selectedId: string | null;
  onSelect: (id: string) => void;
  /** A new state of a canvas (a version added or restored): the owner saves it. */
  onChange: (c: Canvas) => void;
  onDelete: (id: string) => void;
  onCreate: () => void;
  /** Puts a request to revise the canvas into the message box. */
  onRevise: (text: string) => void;
}) {
  const mine = useMemo(() => canvases.filter((c) => c.room === room).sort((a, b) => latest(b).at - latest(a).at), [canvases, room]);
  const current = mine.find((c) => c.id === selectedId) ?? mine[0] ?? null;
  const [viewN, setViewN] = useState<number | null>(null);        // null: the newest version
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"edit" | "preview">("edit");
  const [note, setNote] = useState("");
  const close = useRef<HTMLButtonElement>(null);

  const shown = current ? current.versions.find((v) => v.n === (viewN ?? latest(current).n)) ?? latest(current) : null;
  const isNewest = !!current && !!shown && shown.n === latest(current).n;
  useEffect(() => { setViewN(null); }, [current?.id]);
  useEffect(() => { setDraft(shown?.text ?? ""); setNote(""); }, [current?.id, shown?.n, shown?.text]);
  useEffect(() => { if (open) requestAnimationFrame(() => close.current?.focus()); }, [open]);
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open, onClose]);

  if (!open) return null;
  const dirty = !!shown && draft !== shown.text;
  const flash = (t: string) => { setNote(t); window.setTimeout(() => setNote(""), 2200); };

  return (
    <aside className="dg-canvas" role="complementary" aria-label="Canvas">
      <header className="dg-canvas-head">
        <b>Canvas</b>
        <span className="dg-canvas-actions">
          <button type="button" className="dg-mini" onClick={onCreate}>New</button>
          <button type="button" ref={close} className="dg-set-x" onClick={onClose} aria-label="Close the canvas">Close</button>
        </span>
      </header>
      {!current && <p className="dg-canvas-empty small dim">Nothing here yet. When an agent writes a document, code or a page for you in this room it appears here, with every version. Or press New.</p>}
      {current && shown && (
        <>
          <div className="dg-canvas-bar">
            <select value={current.id} onChange={(e) => onSelect(e.target.value)} aria-label="Which canvas">
              {mine.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
            </select>
            <select value={shown.n} onChange={(e) => setViewN(Number(e.target.value))} aria-label="Version">
              {[...current.versions].reverse().map((v) => <option key={v.n} value={v.n}>{`Version ${v.n}${v.n === latest(current).n ? " (newest)" : ""} · ${new Date(v.at).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}`}</option>)}
            </select>
          </div>
          {current.kind === "html" && (
            <div className="dg-canvas-tabs" role="tablist" aria-label="View">
              <button type="button" role="tab" aria-selected={tab === "edit"} onClick={() => setTab("edit")}>Source</button>
              <button type="button" role="tab" aria-selected={tab === "preview"} onClick={() => setTab("preview")}>Preview</button>
            </div>
          )}
          {current.kind === "html" && tab === "preview" ? (
            <iframe className="dg-canvas-frame" title={current.title} srcDoc={draft} sandbox="allow-scripts allow-forms allow-modals allow-popups" referrerPolicy="no-referrer" />
          ) : (
            <textarea
              className="dg-canvas-text" data-kind={current.kind} value={draft} onChange={(e) => setDraft(e.target.value)} readOnly={!isNewest}
              spellCheck={current.kind === "document"} aria-label={`${current.title}, ${current.kind}`}
            />
          )}
          <footer className="dg-canvas-foot">
            {isNewest
              ? <button type="button" className="btn-ink dg-mini-btn" disabled={!dirty} onClick={() => { onChange(addVersion(current, draft, "you")); flash("Saved as a new version."); }}><span>Save version</span></button>
              : <button type="button" className="btn-ink dg-mini-btn" onClick={() => { onChange(restoreVersion(current, shown.n)); setViewN(null); flash("Restored as the newest version."); }}><span>Restore this version</span></button>}
            <button type="button" className="btn-line dg-mini-btn" onClick={() => { void navigator.clipboard.writeText(draft).then(() => flash("Copied."), () => flash("Copying was blocked.")); }}>Copy</button>
            <button type="button" className="btn-line dg-mini-btn" onClick={() => {
              const url = URL.createObjectURL(new Blob([draft], { type: current.kind === "html" ? "text/html" : "text/plain" }));
              const a = document.createElement("a"); a.href = url; a.download = fileNameFor(current); document.body.appendChild(a); a.click(); a.remove();
              window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
            }}>Download</button>
            <button type="button" className="btn-line dg-mini-btn" onClick={() => onRevise(reviseRequest({ ...current, versions: [...current.versions.slice(0, -1), { ...latest(current), text: dirty && isNewest ? draft : latest(current).text }] }))}>Ask Kell to revise</button>
            <button type="button" className="btn-line dg-mini-btn" onClick={() => { if (window.confirm(`Delete "${current.title}" and all its versions from this device?`)) onDelete(current.id); }}>Delete</button>
            {note && <span className="small dim" role="status">{note}</span>}
          </footer>
        </>
      )}
    </aside>
  );
}
