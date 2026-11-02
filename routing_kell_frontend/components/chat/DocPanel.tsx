"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "@/components/chat/Markdown";
import PanelShell from "@/components/chat/PanelShell";
import { pageDoc } from "@/components/chat/CanvasPanel";
import { DocsApiError, type DocInfo, type DocKind, type DocVersion, type DocsApi } from "@/lib/chat/docs-api";
import { mapCaret, merge3 } from "@/lib/chat/merge3";

/**
 * The docs of a room: the canvas everyone in the room sees and edits at the same time, like a shared document. You type; it saves a moment
 * after you stop; what others save appears within a second or two, merged into what you are typing without moving your cursor. Two people
 * in different lines never overwrite each other (the same merge runs on the server), every save is a version you can go back to, and an
 * agent's canvas block becomes a new version for everyone. Laid out after Open WebUI's artifacts panel (https://github.com/open-webui/open-webui);
 * no code copied.
 *
 * Honest limit: merging is by line, so two people typing inside one long paragraph at once clash (the later save wins that paragraph and
 * the other version stays in history). There are no live cursors of other people yet, only who is here.
 */
const POLL_MS = 1500;
const SAVE_AFTER_MS = 700;
const LIST_MS = 5000;

type Status = "saved" | "saving" | "typing" | "merged" | "offline" | "error";

export const editRequest = (title: string, kind: DocKind, text: string): string => {
  const fence = text.includes("```") ? "~~~" : "```";
  return `@kell Edit the document "${title}". What to change: \n\n${fence}canvas title="${title}" kind=${kind}\n${text}\n${fence}`;
};

function wrapSelection(el: HTMLTextAreaElement, before: string, after = before, placeholder = "text"): void {
  const { selectionStart: s, selectionEnd: e, value } = el;
  const chosen = value.slice(s, e) || placeholder;
  el.focus();
  el.setRangeText(before + chosen + after, s, e, "end");
  el.setSelectionRange(s + before.length, s + before.length + chosen.length);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}
function prefixLines(el: HTMLTextAreaElement, prefix: string): void {
  const { selectionStart: s, selectionEnd: e, value } = el;
  const start = value.lastIndexOf("\n", s - 1) + 1;
  const block = value.slice(start, e).split("\n").map((l) => prefix + l).join("\n");
  el.focus();
  el.setRangeText(block, start, e, "end");
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

export default function DocPanel({ open, onClose, api, roomId, wantedTitle, onAsk, onCount, timing }: {
  open: boolean; onClose: () => void; api: DocsApi; roomId: string;
  /** A document to show first (the title on the card the person pressed). */
  wantedTitle: string | null;
  /** Puts text in the message box ("Ask kell to edit this"). */
  onAsk: (text: string) => void;
  onCount?: (n: number) => void;
  /** Shorter waits for tests. */
  timing?: { poll: number; save: number };
}) {
  const pollMs = timing?.poll ?? POLL_MS, saveMs = timing?.save ?? SAVE_AFTER_MS;
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<DocKind>("document");
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("saved");
  const [note, setNote] = useState("");
  const [present, setPresent] = useState<string[]>([]);
  const [mode, setMode] = useState<"write" | "preview">("write");
  const [history, setHistory] = useState<DocVersion[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [copied, setCopied] = useState(false);

  const area = useRef<HTMLTextAreaElement>(null);
  const textRef = useRef("");                                   // what is in the editor now
  const base = useRef({ body: "", version: 0 });                // the last text and version we know the server has
  const saving = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeId;

  const flash = useCallback((t: string) => { setNote(t); window.setTimeout(() => setNote((n) => (n === t ? "" : n)), 4000); }, []);

  /** Put new text in the editor, keeping the caret with the words around it. */
  const adopt = useCallback((next: string) => {
    const el = area.current;
    const prev = textRef.current;
    const caret = el ? mapCaret(prev, next, el.selectionStart) : 0;
    const focused = !!el && document.activeElement === el;
    textRef.current = next;
    setText(next);
    if (el && focused) requestAnimationFrame(() => { try { el.setSelectionRange(caret, caret); } catch { /* not a text field right now */ } });
  }, []);

  // ---- the list of docs in this room
  const refreshList = useCallback(async () => {
    try {
      const l = await api.list(roomId);
      setDocs(l);
      onCount?.(l.length);
      setError("");
      return l;
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't load the documents."); return null; }
  }, [api, roomId, onCount]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    const pick = async () => {
      const l = await refreshList();
      if (!live || !l) return;
      const wanted = wantedTitle ? l.find((d) => d.title.toLowerCase() === wantedTitle.toLowerCase()) : null;
      setActiveId((cur) => wanted?.id ?? (cur && l.some((d) => d.id === cur) ? cur : l[0]?.id ?? null));
    };
    void pick();
    const t = window.setInterval(() => void refreshList(), LIST_MS);
    return () => { live = false; window.clearInterval(t); };
  }, [open, wantedTitle, refreshList]);

  // ---- open a document
  useEffect(() => {
    if (!open || !activeId) return;
    let live = true;
    window.clearTimeout(timer.current);
    setHistory(null); setStatus("saved"); setNote("");
    void api.get(roomId, activeId).then((d) => {
      if (!live || !("body" in d)) return;
      base.current = { body: d.body, version: d.version };
      textRef.current = d.body;
      setText(d.body); setKind(d.kind); setTitle(d.title); setPresent(d.present);
      setMode(d.kind === "document" ? "write" : "write");
    }).catch((e) => { if (live) setError(e instanceof Error ? e.message : "Couldn't open that document."); });
    return () => { live = false; };
  }, [open, activeId, api, roomId]);

  // ---- save what you typed, a moment after you stop
  const save = useCallback(async () => {
    const id = activeRef.current;
    if (!id || saving.current) return;
    const sent = textRef.current;
    if (sent === base.current.body) { setStatus("saved"); return; }
    saving.current = true;
    setStatus("saving");
    try {
      const r = await api.save(roomId, id, sent, base.current.version);
      if (activeRef.current !== id) return;
      if (r.body !== undefined && r.body !== sent) {
        // someone else saved at the same time and the server merged: show the merged text, keeping anything typed since
        const keep = textRef.current === sent ? r.body : merge3(sent, textRef.current, r.body).text;
        base.current = { body: r.body, version: r.version };
        adopt(keep);
        setStatus("merged");
        flash(r.clash ? "Merged with someone's edit. You both changed the same line, so yours was kept; theirs is in History." : "Merged with someone else's edit.");
      } else {
        base.current = { body: r.body ?? sent, version: r.version };
        setStatus(textRef.current === base.current.body ? "saved" : "typing");
      }
      setError("");
      if (textRef.current !== base.current.body) timer.current = window.setTimeout(() => void save(), saveMs);
    } catch (e) {
      const err = e as DocsApiError;
      setStatus(err.offline ? "offline" : "error");
      setError(err.offline ? "Offline: your changes are kept here and saved when you are back." : err.message);
      timer.current = window.setTimeout(() => void save(), 4000);
    } finally { saving.current = false; }
  }, [api, roomId, adopt, flash, saveMs]);

  const onType = (v: string) => {
    textRef.current = v;
    setText(v);
    setStatus("typing");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), saveMs);
  };

  // ---- see what others save
  useEffect(() => {
    if (!open || !activeId) return;
    const id = activeId;
    const t = window.setInterval(async () => {
      if (document.visibilityState === "hidden" || saving.current) return;
      try {
        const d = await api.get(roomId, id, base.current.version);
        if (activeRef.current !== id) return;
        setPresent(d.present);
        if ("unchanged" in d) return;
        const mine = textRef.current;
        const next = mine === base.current.body ? d.body : merge3(base.current.body, mine, d.body).text;   // typing in the meantime is kept
        base.current = { body: d.body, version: d.version };
        setTitle(d.title); setKind(d.kind);
        adopt(next);
        if (next !== d.body) setStatus("typing");
        flash(`${d.updated_by || "Someone"} updated this.`);
      } catch { /* the next round tries again */ }
    }, pollMs);
    return () => window.clearInterval(t);
  }, [open, activeId, api, roomId, adopt, flash, pollMs]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  // a last save when the panel closes or the page is left
  useEffect(() => {
    if (!open) return;
    const bye = () => { if (textRef.current !== base.current.body) void save(); };
    window.addEventListener("pagehide", bye);
    return () => { window.removeEventListener("pagehide", bye); bye(); };
  }, [open, save]);

  const create = async () => {
    const t = newTitle.trim();
    if (!t) return;
    try {
      const d = await api.create(roomId, t, "document", `# ${t}\n\n`);
      setCreating(false); setNewTitle("");
      await refreshList();
      setActiveId(d.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't make that document."); }
  };

  const openHistory = async () => {
    if (!activeId) return;
    if (history) { setHistory(null); return; }
    try { setHistory(await api.history(roomId, activeId)); } catch (e) { setError(e instanceof Error ? e.message : "Couldn't load the history."); }
  };
  const restore = async (v: number) => {
    if (!activeId) return;
    try {
      const r = await api.restore(roomId, activeId, v);
      base.current = { body: r.body, version: r.version };
      adopt(r.body); setHistory(null); setStatus("saved"); flash(`Restored version ${v} as the newest.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't restore that version."); }
  };
  const remove = async () => {
    if (!activeId || !window.confirm(`Delete "${title}" for everyone in this room? Its history goes with it.`)) return;
    try { await api.remove(roomId, activeId); setActiveId(null); const l = await refreshList(); setActiveId(l?.[0]?.id ?? null); } catch (e) { setError(e instanceof Error ? e.message : "Couldn't delete it."); }
  };
  const copy = () => { void navigator.clipboard.writeText(textRef.current).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 2000); }, () => {}); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([textRef.current], { type: kind === "html" ? "text/html" : "text/plain" }));
    const a = document.createElement("a"); a.href = url; a.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "document"}.${kind === "html" ? "html" : kind === "code" ? "txt" : "md"}`;
    document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  };

  if (!open) return null;
  const statusText = status === "saving" ? "Saving…" : status === "typing" ? "Editing…" : status === "merged" ? "Merged" : status === "offline" ? "Offline" : status === "error" ? "Not saved" : "Saved";
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <PanelShell open label="Docs">
      <div className="dg-doc-head">
        <div className="dg-doc-pick">
          {docs.length > 0 ? (
            <select value={activeId ?? ""} onChange={(e) => setActiveId(e.target.value)} aria-label="Document">
              {docs.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
            </select>
          ) : <b>Docs</b>}
          <button type="button" className="dg-cv-chip" onClick={() => setCreating((c) => !c)} aria-expanded={creating}>+ New</button>
        </div>
        <div className="dg-doc-meta">
          {present.length > 0 && (
            <span className="dg-doc-people" title={`${present.join(", ")} ${present.length === 1 ? "is" : "are"} here`}>
              {present.slice(0, 4).map((n) => <i key={n} aria-hidden="true">{n.slice(0, 1).toUpperCase()}</i>)}
              <span className="dg-doc-here">{present.length === 1 ? `${present[0]} is here` : `${present.length} people here`}</span>
            </span>
          )}
          <span className="dg-doc-status" data-s={status} role="status" aria-live="polite"><i aria-hidden="true" />{statusText}</span>
        </div>
        <button type="button" className="dg-cv-close" aria-label="Close" onClick={onClose}>
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
        </button>
      </div>

      {creating && (
        <form className="dg-doc-new" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="Document title" maxLength={120} autoFocus aria-label="Document title" />
          <button type="submit" className="btn-ink dg-mini-btn" disabled={!newTitle.trim()}><span>Create</span></button>
        </form>
      )}
      {(error || note) && <p className={error ? "dg-doc-err" : "dg-doc-note"} role={error ? "alert" : "status"}>{error || note}</p>}

      {!activeId ? (
        <div className="dg-doc-empty">
          <p><b>No documents here yet.</b></p>
          <p className="small dim">Make one to write in together, or ask an agent to write one: it appears here for everyone in the room.</p>
          <button type="button" className="btn-ink dg-mini-btn" onClick={() => setCreating(true)}><span>New document</span></button>
        </div>
      ) : (
        <>
          <div className="dg-doc-bar">
            <div className="dg-doc-seg" role="tablist" aria-label="View">
              <button type="button" role="tab" aria-selected={mode === "write"} onClick={() => setMode("write")}>{kind === "html" ? "Code" : "Write"}</button>
              {kind !== "code" && <button type="button" role="tab" aria-selected={mode === "preview"} onClick={() => setMode("preview")}>{kind === "html" ? "Page" : "Read"}</button>}
            </div>
            {mode === "write" && kind === "document" && (
              <div className="dg-doc-fmt" role="toolbar" aria-label="Formatting">
                <button type="button" title="Bold" aria-label="Bold" onClick={() => area.current && wrapSelection(area.current, "**")}><b>B</b></button>
                <button type="button" title="Italic" aria-label="Italic" onClick={() => area.current && wrapSelection(area.current, "*")}><i>I</i></button>
                <button type="button" title="Heading" aria-label="Heading" onClick={() => area.current && prefixLines(area.current, "## ")}>H</button>
                <button type="button" title="Bullet list" aria-label="Bullet list" onClick={() => area.current && prefixLines(area.current, "- ")}>•</button>
                <button type="button" title="Link" aria-label="Link" onClick={() => area.current && wrapSelection(area.current, "[", "](https://)", "link")}>↗</button>
                <button type="button" title="Code" aria-label="Code" onClick={() => area.current && wrapSelection(area.current, "`")}>{"</>"}</button>
              </div>
            )}
          </div>

          <div className="dg-doc-body">
            {mode === "write" ? (
              <textarea
                ref={area} className="dg-doc-text" data-kind={kind} value={text} onChange={(e) => onType(e.target.value)}
                spellCheck={kind === "document"} aria-label={`Edit ${title}`} placeholder="Start writing…"
              />
            ) : kind === "html" ? (
              <iframe title={title} className="dg-canvas-frame" srcDoc={pageDoc(text)} sandbox="allow-scripts allow-downloads allow-forms" referrerPolicy="no-referrer" />
            ) : (
              <div className="dg-canvas-doc" tabIndex={0}><Markdown text={text} /></div>
            )}
            {history && (
              <div className="dg-doc-history" role="dialog" aria-label="History">
                <div className="dg-doc-history-head"><b>History</b><button type="button" className="dg-cv-icon" aria-label="Close history" onClick={() => setHistory(null)}>×</button></div>
                <ul>
                  {history.map((v) => (
                    <li key={v.version}>
                      <span><b>Version {v.version}</b> · {v.by}{v.note ? ` · ${v.note}` : ""}<br /><small>{v.at ? new Date(v.at).toLocaleString() : ""}</small></span>
                      <button type="button" className="dg-linkbtn" onClick={() => void restore(v.version)}>Restore</button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="dg-doc-foot">
            <span className="small dim">{words} {words === 1 ? "word" : "words"} · version {base.current.version}</span>
            <span className="dg-doc-actions">
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={download}>Download</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => void openHistory()} aria-expanded={!!history}>History</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => onAsk(editRequest(title, kind, textRef.current))}>Ask kell to edit</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => void remove()}>Delete</button>
            </span>
          </div>
        </>
      )}
    </PanelShell>
  );
}
