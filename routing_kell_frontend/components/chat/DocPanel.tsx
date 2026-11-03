"use client";
import { isRealName } from "@/lib/chat/artifacts";
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "@/components/chat/Markdown";
import Latex from "@/components/chat/Latex";
import PanelShell from "@/components/chat/PanelShell";
import HtmlViewer from "@/components/chat/HtmlViewer";
import CursorLayer, { type RemoteCursor } from "@/components/chat/CursorLayer";
import { DocsApiError, type DocInfo, type DocKind, type DocVersion, type DocsApi } from "@/lib/chat/docs-api";
import { mapCaret, merge3 } from "@/lib/chat/merge3";
import { openDocSocket, socketsAvailable, type DocSocket, type SocketMessage } from "@/lib/chat/docs-socket";

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

/** A steady colour for a person's initial, from their name. */
const personColor = (name: string): string => {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 62%)`;
};

/**
 * What "Ask kell to edit" puts in the message box: one short line that names the canvas, ready for you to finish ("... make it shorter"). The
 * canvas itself is not in the message: while it is open its text travels with your request (RoomView sends it as `canvas`), the way an
 * artifact toolbar in vercel/chatbot sends only "Please add final polish..." and the model gets the document by reference. Pasting the whole
 * document into the box made it show as raw text, a fence and all.
 */
export const editRequest = (label: string): string => `@kell Edit "${label}": `;

/** One-click edits, each a short sentence sent as it is (like the toolbar actions of vercel/chatbot's artifacts). */
export const QUICK_EDITS: Record<DocKind, Array<{ label: string; ask: string }>> = {
  document: [
    { label: "Polish", ask: "fix grammar, tighten the wording and keep my meaning." },
    { label: "Shorter", ask: "make it about half as long and keep the key points." },
    { label: "Add headings", ask: "add section headings and a short summary at the top." },
  ],
  html: [
    { label: "Improve design", ask: "make the design cleaner and more polished: spacing, type and colour." },
    { label: "Make responsive", ask: "make it work well on a phone." },
  ],
  code: [
    { label: "Add comments", ask: "add short, useful comments and keep the behaviour the same." },
    { label: "Find bugs", ask: "find bugs or edge cases and fix them." },
  ],
  latex: [
    { label: "Polish", ask: "fix grammar and tighten the wording; keep the LaTeX valid." },
    { label: "Add abstract", ask: "add an abstract and section headings." },
  ],
};

/** What a canvas is called in the picker: its title, or - when the title is only a number or a key - "Page 2" / "Document 1" by its place in the list. */
function shown(d: { title: string; label?: string; kind: string; id: string }, all: { id: string; kind: string }[]): string {
  if (d.label && isRealName(d.label)) return d.label;                 // the server read a name from the text when the stored title was only an id
  if (isRealName(d.title)) return d.title;
  const same = all.filter((x) => x.kind === d.kind);
  const n = Math.max(1, same.findIndex((x) => x.id === d.id) + 1);
  return `${d.kind === "html" ? "Page" : d.kind === "code" ? "Code" : d.kind === "latex" ? "LaTeX" : "Document"} ${n}`;
}

/** What a new LaTeX canvas starts with: a short article with a section and an equation, so there is something to see at once. */
const latexStarter = (title: string): string =>
  `\\documentclass{article}\n\\usepackage{amsmath}\n\\title{${title.replace(/[\\{}$&#_%^~]/g, " ")}}\n\\author{}\n\\date{\\today}\n\\begin{document}\n\\maketitle\n\n\\section{Introduction}\nWrite here. Maths goes between dollar signs, like $e^{i\\pi} + 1 = 0$, or on its own line:\n\\begin{equation}\n  \\int_0^1 x^2\\,dx = \\frac{1}{3}\n\\end{equation}\n\n\\end{document}\n`;

export default function DocPanel({ open, onClose, api, roomId, wantedTitle, onAsk, onQuick, onDocs, onSeen, onContext, timing, socket }: {
  open: boolean; onClose: () => void; api: DocsApi; roomId: string;
  /** A document to show first (the title on the card the person pressed). */
  wantedTitle: string | null;
  /** Puts text in the message box ("Ask kell to edit this"). */
  onAsk: (text: string) => void;
  /** Sends a short request at once (a quick edit). The open canvas goes with it. */
  onQuick?: (text: string) => void;
  /** The documents of the room, whenever the list is read. */
  onDocs?: (docs: DocInfo[]) => void;
  /** This version of this document is on screen: it no longer counts as new. */
  onSeen?: (id: string, version: number) => void;
  /** What is open (title, kind, text), so the agent can be given it when you ask for a change. */
  onContext?: (c: { title: string; kind: string; text: string } | null) => void;
  /** Shorter waits for tests. */
  timing?: { poll: number; save: number };
  /** How to open the live connection. Without it the panel uses the plain HTTP routes and polls. */
  socket?: { token(): Promise<string | null>; origin?: string; socketFactory?: (url: string) => WebSocket };
}) {
  const pollMs = timing?.poll ?? POLL_MS, saveMs = timing?.save ?? SAVE_AFTER_MS;
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<DocKind>("document");
  const [newKind, setNewKind] = useState<DocKind>("document");
  const rich = kind === "document" || kind === "latex";            // a text you write and read rendered: markdown or LaTeX
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState<Status>("saved");
  const [note, setNote] = useState("");
  const [present, setPresent] = useState<string[]>([]);
  const [mode, setMode] = useState<"write" | "split" | "read" | "browser" | "page">("split");
  const [editing, setEditing] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [linked, setLinked] = useState(true);                 // the two halves of Side by side scroll together
  const sock = useRef<DocSocket | null>(null);
  const readPane = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const inflight = useRef<string | null>(null);              // the text sent over the socket and not yet acknowledged
  const [history, setHistory] = useState<DocVersion[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [copied, setCopied] = useState(false);

  const area = useRef<HTMLTextAreaElement>(null);
  const [remote, setRemote] = useState<RemoteCursor[]>([]);          // other people's carets and selections
  const prevForCursors = useRef("");
  const cursorTimer = useRef<number | undefined>(undefined);
  const lastCursor = useRef("");
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
      onDocs?.(l);
      setError("");
      return l;
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't load the documents."); return null; }
  }, [api, roomId, onDocs]);

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
      setText(d.body); setKind(d.kind); setTitle(d.title); setPresent(d.present); setEditing(d.editing ?? []);
      setMode(d.kind === "html" ? "browser" : d.kind === "document" || d.kind === "latex" ? "split" : "write");
    }).catch((e) => { if (live) setError(e instanceof Error ? e.message : "Couldn't open that document."); });
    return () => { live = false; };
  }, [open, activeId, api, roomId]);

  // ---- Side by side: scrolling one half scrolls the other to the same place (proportionally; the half being touched leads)
  const tie = useCallback((from: HTMLElement | null, to: HTMLElement | null) => {
    if (!linked || !from || !to || syncing.current) return;
    const max = from.scrollHeight - from.clientHeight;
    const ratio = max > 0 ? from.scrollTop / max : 0;
    syncing.current = true;
    to.scrollTop = ratio * Math.max(0, to.scrollHeight - to.clientHeight);
    requestAnimationFrame(() => { syncing.current = false; });
  }, [linked]);

  // ---- what the server tells us (live socket or polling): fold it into what is on screen without moving your cursor
  const applyRemote = useCallback((version: number, body: string) => {
    if (version <= base.current.version) return;
    const mine = textRef.current;
    const next = mine === base.current.body ? body : merge3(base.current.body, mine, body).text;   // typing in the meantime is kept
    base.current = { body, version };
    adopt(next);
    if (next !== body) setStatus((st) => (st === "saving" ? st : "typing"));
  }, [adopt]);

  // ---- save what you typed. Over the socket every few keystrokes; over HTTP a moment after you stop.
  const save = useCallback(async () => {
    const id = activeRef.current;
    if (!id || saving.current) return;
    const sent = textRef.current;
    if (sent === base.current.body) { setStatus("saved"); return; }
    if (sock.current?.connected) {
      if (inflight.current !== null) return;                      // one edit at a time; the acknowledgement sends the next
      if (sock.current.send({ type: "edit", body: sent, base: base.current.version })) { inflight.current = sent; setStatus("saving"); return; }
    }
    saving.current = true;
    setStatus("saving");
    try {
      const r = await api.save(roomId, id, sent, base.current.version);
      if (activeRef.current !== id) return;
      if (r.body !== undefined && r.body !== sent) {
        const keep = textRef.current === sent ? r.body : merge3(sent, textRef.current, r.body).text;
        base.current = { body: r.body, version: r.version };
        adopt(keep);
        setStatus(keep === r.body ? "saved" : "typing");
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
  }, [api, roomId, adopt, saveMs]);

  const onType = (v: string) => {
    textRef.current = v;
    setText(v);
    setStatus("typing");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void save(), sock.current?.connected ? Math.min(saveMs, 140) : saveMs);
  };

  // ---- the live connection for the open document
  useEffect(() => {
    if (!open || !activeId || !socket || !socketsAvailable()) return;
    const id = activeId;
    const onMessage = (m: SocketMessage) => {
      if (activeRef.current !== id) return;
      if (m.type === "hello") {
        setPresent(m.present); setEditing(m.editing ?? []);
        applyRemote(m.version, m.body);
        if (m.version === base.current.version && textRef.current === base.current.body) setStatus("saved");
      } else if (m.type === "doc") {
        applyRemote(m.version, m.body);
      } else if (m.type === "presence") {
        setPresent(m.present); setEditing(m.editing ?? []);
      } else if (m.type === "cursors") {
        setRemote(m.cursors);
      } else if (m.type === "ack") {
        const sent = inflight.current ?? textRef.current;
        inflight.current = null;
        if (m.body !== undefined && m.body !== sent) {
          const keep = textRef.current === sent ? m.body : merge3(sent, textRef.current, m.body).text;
          base.current = { body: m.body, version: m.version };
          adopt(keep);
          setStatus(keep === m.body ? "saved" : "typing");
        } else {
          base.current = { body: m.body ?? sent, version: m.version };
          setStatus(textRef.current === base.current.body ? "saved" : "typing");
        }
        setError("");
        if (textRef.current !== base.current.body) timer.current = window.setTimeout(() => void save(), 60);
      } else if (m.type === "error") {
        inflight.current = null;
        if (m.status !== 429) setError(m.error);
        timer.current = window.setTimeout(() => void save(), 1500);
      }
    };
    const s = openDocSocket({ room: roomId, doc: id, ...socket }, {
      onMessage,
      onState: (up) => { if (!up) inflight.current = null; },
    });
    sock.current = s;
    return () => { s.close(); if (sock.current === s) sock.current = null; inflight.current = null; };
  }, [open, activeId, roomId, socket, applyRemote, adopt, save]);

  // Carets that belong to other people move with the words around them when the text changes (theirs fix themselves a moment later).
  useEffect(() => {
    const prev = prevForCursors.current;
    prevForCursors.current = text;
    if (prev !== text) setRemote((r) => (r.length ? r.map((c) => ({ ...c, from: mapCaret(prev, text, c.from), to: mapCaret(prev, text, c.to) })) : r));
  }, [text]);
  useEffect(() => { setRemote([]); lastCursor.current = ""; }, [activeId]);

  /** Tell the others where my caret or selection is (at most about 15 times a second, and only when it changed). */
  const shareCursor = () => {
    window.clearTimeout(cursorTimer.current);
    cursorTimer.current = window.setTimeout(() => {
      const el = area.current;
      if (!el || !sock.current?.connected) return;
      const key = `${el.selectionStart}:${el.selectionEnd}`;
      if (key === lastCursor.current) return;
      lastCursor.current = key;
      sock.current.send({ type: "cursor", from: el.selectionStart, to: el.selectionEnd });
    }, 60);
  };
  useEffect(() => () => window.clearTimeout(cursorTimer.current), []);

  // ---- without a live connection: see what others save by asking now and then
  useEffect(() => {
    if (!open || !activeId) return;
    const id = activeId;
    const t = window.setInterval(async () => {
      if (document.visibilityState === "hidden" || saving.current || sock.current?.connected) return;
      try {
        const d = await api.get(roomId, id, base.current.version);
        if (activeRef.current !== id) return;
        setPresent(d.present); setEditing(d.editing ?? []);
        if ("unchanged" in d) return;
        setTitle(d.title); setKind(d.kind);
        applyRemote(d.version, d.body);
      } catch { /* the next round tries again */ }
    }, pollMs);
    return () => window.clearInterval(t);
  }, [open, activeId, api, roomId, applyRemote, pollMs]);

  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => { if (open && activeId) onSeen?.(activeId, base.current.version); }, [open, activeId, text, onSeen]);
  useEffect(() => { onContext?.(open && activeId ? { title, kind, text } : null); }, [open, activeId, title, kind, text, onContext]);   // what is on screen is no longer new

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
      const d = await api.create(roomId, t, newKind, newKind === "latex" ? latexStarter(t) : `# ${t}\n\n`);
      setCreating(false); setNewTitle(""); setNewKind("document");
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
    const a = document.createElement("a"); a.href = url; a.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "document"}.${kind === "html" ? "html" : kind === "code" ? "txt" : kind === "latex" ? "tex" : "md"}`;
    document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  };

  const statusText = status === "saving" ? "Saving…" : status === "typing" ? "Editing…" : status === "merged" ? "Merged" : status === "offline" ? "Offline" : status === "error" ? "Not saved" : "Saved";
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <PanelShell open={open} label="Canvas">
      <div className="dg-doc-head">
        <div className="dg-doc-pick">
          <div className="dg-picker">
            <button type="button" className="dg-picker-btn" aria-haspopup="listbox" aria-expanded={pickerOpen} onClick={() => setPickerOpen((o) => !o)} disabled={docs.length === 0}>
              <span className="dg-picker-title">{docs.length > 0 ? shown(docs.find((d) => d.id === activeId) ?? { title, kind, id: activeId ?? "" }, docs) : "Canvas"}</span>
              {docs.length > 1 && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>}
            </button>
            {pickerOpen && docs.length > 1 && (
              <ul className="dg-picker-list" role="listbox" aria-label="Canvas" onMouseLeave={() => undefined}>
                {docs.map((d) => (
                  <li key={d.id} role="option" aria-selected={d.id === activeId}>
                    <button type="button" onClick={() => { setActiveId(d.id); setPickerOpen(false); }}>
                      <span className="dg-picker-kind" aria-hidden="true">{d.kind === "html" ? "PAGE" : d.kind === "code" ? "CODE" : d.kind === "latex" ? "TEX" : "DOC"}</span>
                      <span className="dg-picker-name">{shown(d, docs)}</span>
                      <span className="dg-picker-by small dim">{d.updated_by}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button type="button" className="dg-cv-chip dg-cv-big" onClick={() => setCreating((c) => !c)} aria-expanded={creating}>+ New</button>
        </div>
        <div className="dg-doc-meta">
          {present.length > 0 && (
            <span className="dg-doc-people" title={`${present.join(", ")} ${present.length === 1 ? "is" : "are"} here`}>
              {present.slice(0, 4).map((n) => <i key={n} data-editing={editing.includes(n) || undefined} style={{ background: personColor(n) }} aria-hidden="true">{n.slice(0, 1).toUpperCase()}</i>)}
              <span className="dg-doc-here">{editing.length > 0 ? `${editing.join(", ")} ${editing.length === 1 ? "is" : "are"} editing…` : present.length === 1 ? `${present[0]} is here` : `${present.length} people here`}</span>
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
          <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="Canvas title" maxLength={120} autoFocus aria-label="Canvas title" />
          <select value={newKind} onChange={(e) => setNewKind(e.target.value as DocKind)} aria-label="Type"><option value="document">Markdown</option><option value="latex">LaTeX</option></select>
          <button type="submit" className="btn-ink dg-mini-btn" disabled={!newTitle.trim()}><span>Create</span></button>
        </form>
      )}
      {(error || note) && <p className={error ? "dg-doc-err" : "dg-doc-note"} role={error ? "alert" : "status"}>{error || note}</p>}

      {!activeId ? (
        <div className="dg-doc-empty">
          <p><b>Nothing on the canvas yet.</b></p>
          <p className="small dim">Make one to write in together, or ask an agent to write one: it appears here for everyone in the room.</p>
          <button type="button" className="btn-ink dg-mini-btn" onClick={() => setCreating(true)}><span>New canvas</span></button>
        </div>
      ) : (
        <>
          <div className="dg-doc-bar">
            <div className="dg-doc-seg dg-doc-seg--big" role="tablist" aria-label="View">
              {rich ? (
                <>
                  <button type="button" role="tab" aria-selected={mode === "write"} onClick={() => setMode("write")}>Write</button>
                  <button type="button" role="tab" aria-selected={mode === "split"} onClick={() => setMode("split")}>Side by side</button>
                  <button type="button" role="tab" aria-selected={mode === "read"} onClick={() => setMode("read")}>Read</button>
                </>
              ) : kind === "html" ? (
                <>
                  <button type="button" role="tab" aria-selected={mode === "write"} onClick={() => setMode("write")}>Code</button>
                  <button type="button" role="tab" aria-selected={mode === "split"} onClick={() => setMode("split")}>Side by side</button>
                  <button type="button" role="tab" aria-selected={mode === "browser"} onClick={() => setMode("browser")}>Browser</button>
                  <button type="button" role="tab" aria-selected={mode === "page"} onClick={() => setMode("page")}>Page</button>
                </>
              ) : <button type="button" role="tab" aria-selected="true">Code</button>}
            </div>
            {mode === "split" && rich && (
              <button type="button" className="dg-cv-chip dg-cv-big" aria-pressed={linked} onClick={() => setLinked((l) => !l)} title="Scroll both halves together">{linked ? "Scrolling linked" : "Scrolling free"}</button>
            )}
            {(mode === "write" || mode === "split") && kind === "latex" && (
              <div className="dg-doc-fmt" role="toolbar" aria-label="LaTeX">
                <button type="button" title="Bold" aria-label="Bold" onClick={() => area.current && wrapSelection(area.current, "\\textbf{", "}", "bold")}><b>B</b></button>
                <button type="button" title="Italic" aria-label="Italic" onClick={() => area.current && wrapSelection(area.current, "\\emph{", "}", "italic")}><i>I</i></button>
                <button type="button" title="Inline maths" aria-label="Inline maths" onClick={() => area.current && wrapSelection(area.current, "$", "$", "x^2")}>$x$</button>
                <button type="button" title="Equation" aria-label="Equation" onClick={() => area.current && wrapSelection(area.current, "\\begin{equation}\n", "\n\\end{equation}", "E = mc^2")}>∑</button>
                <button type="button" title="Section" aria-label="Section" onClick={() => area.current && wrapSelection(area.current, "\\section{", "}", "Title")}>§</button>
                <button type="button" title="List" aria-label="List" onClick={() => area.current && wrapSelection(area.current, "\\begin{itemize}\n  \\item ", "\n\\end{itemize}", "item")}>•</button>
              </div>
            )}
            {(mode === "write" || mode === "split") && kind === "document" && (
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
            {(mode === "write" || mode === "split" || (kind === "code")) && (
              <div className="dg-doc-pane" data-split={mode === "split" && kind !== "code" || undefined}>
                <textarea
                  ref={area} className="dg-doc-text" data-kind={kind} value={text} onChange={(e) => onType(e.target.value)} onScroll={() => tie(area.current, readPane.current)} onSelect={shareCursor} onFocus={shareCursor}
                  spellCheck={kind === "document"} aria-label={`Edit ${title}`} placeholder="Start writing…"
                />
                <CursorLayer area={area} text={text} cursors={remote} color={personColor} />
                {mode === "split" && rich && <div ref={readPane} className="dg-canvas-doc dg-doc-read" tabIndex={0} aria-label="Preview" onScroll={() => tie(readPane.current, area.current)}>{kind === "latex" ? <Latex text={text} /> : <Markdown text={text} />}</div>}
                {mode === "split" && kind === "html" && <HtmlViewer html={text} title={title} mode="browser" />}
              </div>
            )}
            {mode === "read" && rich && <div className="dg-canvas-doc" tabIndex={0}>{kind === "latex" ? <Latex text={text} /> : <Markdown text={text} />}</div>}
            {(mode === "browser" || mode === "page") && kind === "html" && <HtmlViewer html={text} title={title} mode={mode === "page" ? "page" : "browser"} />}
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

          {onQuick && (
            <div className="dg-doc-quick" role="group" aria-label="Quick edits by kell">
              <span className="small dim">Ask kell</span>
              {(QUICK_EDITS[kind] ?? QUICK_EDITS.document).map((q) => (
                <button key={q.label} type="button" className="dg-cv-chip" onClick={() => onQuick(`@kell ${q.label} "${shown(docs.find((d) => d.id === activeId) ?? { title, kind, id: activeId ?? "" }, docs)}": ${q.ask}`)}>{q.label}</button>
              ))}
            </div>
          )}
          <div className="dg-doc-foot">
            <span className="small dim">{words} {words === 1 ? "word" : "words"} · version {base.current.version}</span>
            <span className="dg-doc-actions">
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={download}>Download</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => void openHistory()} aria-expanded={!!history}>History</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => onAsk(editRequest(shown(docs.find((d) => d.id === activeId) ?? { title, kind, id: activeId ?? "" }, docs)))}>Ask kell to edit</button>
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={() => void remove()}>Delete</button>
            </span>
          </div>
        </>
      )}
    </PanelShell>
  );
}
