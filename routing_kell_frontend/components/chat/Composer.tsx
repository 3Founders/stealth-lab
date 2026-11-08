"use client";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { sends } from "@/lib/chat/prefs";
import { applyMention, matchCommands, matchHandles, mentionQuery, slashQuery } from "@/lib/chat/ui";
import { ACCEPT, dataUrlOf, prepareImage, whyNotImage } from "@/lib/chat/images";
import type { ImageAttachment } from "@/lib/chat/types";
import { CrossIcon, ImageIcon } from "@/components/chat/icons";
import { KellMark } from "@/components/chat/brand";
import { useSlideHighlight } from "@/components/chat/useSlideHighlight";
import { fileKind, formatSize, whyNotFile } from "@/lib/chat/files";

/**
 * The message box. Typing "@" opens a list of the room's handles. It follows the W3C combobox pattern: focus stays in
 * the text box, the list is a listbox, and aria-activedescendant names the highlighted option.
 */
/** The server keeps at most this many characters of a message; the box stops here so nothing is cut off silently. */
const MAX_MESSAGE = 8000;
const wordCount = (t: string): number => (t.trim() ? t.trim().split(/\s+/).length : 0);

export default function Composer({ handles, agentHandles = [], onSend, disabled, enterToSend = true, draft, onEscape }: { handles: string[]; agentHandles?: string[];
  /** Escape in the box when no suggestion list is open (cancels a reply). */
  onEscape?: () => void;
  /** Puts text in the box (a new `n` replaces what is there): "Ask Kell to revise", a suggestion. */
  draft?: { text: string; n: number }; onSend: (text: string, attachments?: ImageAttachment[], files?: File[]) => void; disabled?: boolean; enterToSend?: boolean }) {
  const [text, setText] = useState("");
  const [big, setBig] = useState(false);
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [pics, setPics] = useState<ImageAttachment[]>([]);
  const [docs, setDocs] = useState<File[]>([]);
  const [picError, setPicError] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const listId = useId();
  const slide = useSlideHighlight();
  useEffect(() => {
    if (!draft || draft.n === 0) return;
    setText(draft.text); setCaret(draft.text.length); setActive(0); setDismissed(true);
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.setSelectionRange(0, 0); });
  }, [draft?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  const q = mentionQuery(text, caret);
  const options = useMemo(() => (q && !dismissed ? matchHandles(handles, q.query) : []), [q, dismissed, handles]);
  const sq = slashQuery(text, caret);
  const cmds = useMemo(() => (sq !== null && !dismissed ? matchCommands(sq) : []), [sq, dismissed]);
  const slashOpen = cmds.length > 0;
  const open = slashOpen || options.length > 0;
  const count = slashOpen ? cmds.length : options.length;
  const idx = Math.min(active, Math.max(0, count - 1));

  const pickCommand = (name: string) => {
    const next = name === "/stop" ? name : `${name} `;
    setText(next); setCaret(next.length); setActive(0);
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.setSelectionRange(next.length, next.length); });
  };

  const pick = (h: string) => {
    const r = applyMention(text, caret, h);
    if (!r) return;
    setText(r.text);
    setCaret(r.caret);
    setActive(0);
    requestAnimationFrame(() => { ref.current?.focus(); ref.current?.setSelectionRange(r.caret, r.caret); });
  };
  /** Pictures from the file picker, a paste or a drop: checked, scaled down and added (at most four). */
  const addFiles = async (files: File[]) => {
    setPicError("");
    const next: ImageAttachment[] = [];
    const nextDocs: File[] = [];
    setBusy(true);
    try {
      for (const f of files) {
        if (ACCEPT.split(",").includes(f.type)) {            // a picture: scaled down and sent inside the message, as before
          const why = whyNotImage(f, pics.length + next.length);
          if (why) { setPicError(why); break; }
          try { next.push(await prepareImage(f)); } catch (err) { setPicError(err instanceof Error ? err.message : "That picture could not be added."); break; }
        } else {                                             // anything else: a file, uploaded to the room when you press Send
          const why = whyNotFile(f, docs.length + nextDocs.length);
          if (why) { setPicError(why); break; }
          nextDocs.push(f);
        }
      }
    } finally { setBusy(false); }
    if (next.length) setPics((p) => [...p, ...next]);
    if (nextDocs.length) setDocs((d) => [...d, ...nextDocs]);
  };
  const send = () => {
    const t = text.trim();
    if ((!t && !pics.length && !docs.length) || busy) return;
    if (docs.length) onSend(t, pics.length ? pics : undefined, docs); else if (pics.length) onSend(t, pics); else onSend(t);
    setText(""); setCaret(0); setActive(0); setDismissed(false); setPics([]); setDocs([]); setPicError("");
  };
  /** Drag the small pill at the top of the box to set its height. The native corner grip still works; this is the
   *  obvious one, and it resizes upward the way a bottom-anchored composer should. */
  const startResize = (e: React.PointerEvent<HTMLSpanElement>) => {
    e.preventDefault();
    const el = ref.current;
    if (!el) return;
    const startY = e.clientY;
    const startH = el.getBoundingClientRect().height;
    const max = Math.max(120, window.innerHeight * 0.5);
    const move = (ev: PointerEvent) => {
      const h = Math.max(44, Math.min(max, startH + (startY - ev.clientY)));
      el.style.height = `${h}px`;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
  };
  /** Files from the picker or a drop: all of them, so one that is not a picture is reported instead of silently ignored. */
  const imageFiles = (files: FileList | null | undefined): File[] => Array.from(files ?? []);
  const pastedImages = (items: DataTransferItemList): File[] => {
    const out: File[] = [];
    for (const it of Array.from(items)) { const f = it.kind === "file" ? it.getAsFile() : null; if (f && f.type.startsWith("image/")) out.push(f); }
    return out;
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (open) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((idx + 1) % count); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive((idx - 1 + count) % count); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); if (slashOpen) pickCommand(cmds[idx].name); else pick(options[idx]); return; }
      if (e.key === "Escape") { e.preventDefault(); setDismissed(true); return; }
    }
    if (e.key === "Escape" && onEscape) { e.preventDefault(); onEscape(); return; }
    if (sends({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, composing: e.nativeEvent.isComposing }, enterToSend)) { e.preventDefault(); send(); }
  };

  return (
    <form
      className="dg-composer" data-over={over || undefined} onSubmit={(e) => { e.preventDefault(); send(); }}
      onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { setOver(false); const f = imageFiles(e.dataTransfer.files); if (f.length) { e.preventDefault(); void addFiles(f); } }}
    >
      <span className="dg-composer-grip" role="separator" aria-orientation="horizontal" aria-label="Resize the message box" onPointerDown={startResize} />
      {(pics.length > 0 || docs.length > 0 || picError) && (
        <div className="dg-pics">
          {docs.map((f, i) => (
            <span key={`d${i}`} className="dg-doc" title={f.name}>
              <span className="dg-file-kind" aria-hidden="true">{fileKind({ name: f.name, mime: f.type })}</span>
              <span className="dg-file-meta"><b>{f.name}</b><span className="small dim">{formatSize(f.size)}</span></span>
              <button type="button" onClick={() => setDocs((x) => x.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`} title="Remove"><CrossIcon /></button>
            </span>
          ))}
          {pics.map((p, i) => (
            <span key={i} className="dg-pic">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={dataUrlOf(p)} alt={p.name} width={56} height={56} />
              <button type="button" onClick={() => setPics((x) => x.filter((_, j) => j !== i))} aria-label={`Remove ${p.name}`} title="Remove"><CrossIcon /></button>
            </span>
          ))}
          {docs.length > 0 && <span className="small dim dg-doc-note">Files are kept on keळ's server for 30 days and are not end-to-end encrypted.</span>}
          {picError && <span className="dg-pic-err" role="status">{picError}</span>}
        </div>
      )}
      {slashOpen && (
        <ul id={listId} role="listbox" aria-label="Commands" className="dg-mentions dg-commands" ref={slide('li[role="option"]')}>
          {cmds.map((c, i) => (
            <li key={c.name} id={`${listId}-${i}`} role="option" aria-selected={i === idx} data-active={i === idx}
                onMouseDown={(e) => { e.preventDefault(); pickCommand(c.name); }}>
              <code>{c.name}</code> <span className="dim small">{c.hint}</span>
            </li>
          ))}
        </ul>
      )}
      {!slashOpen && open && (
        <ul id={listId} role="listbox" aria-label="Mention a member" className="dg-mentions" ref={slide('li[role="option"]')}>
          {options.map((h, i) => (
            <li key={h} id={`${listId}-${i}`} role="option" aria-selected={i === idx} data-active={i === idx}
                onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
              {agentHandles.includes(h) && <KellMark size={14} />}<code>{h}</code>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={ref} rows={1} data-big={big || undefined} value={text} disabled={disabled} maxLength={MAX_MESSAGE}
        role="combobox" aria-label="Message" aria-autocomplete="list" aria-expanded={open}
        aria-controls={open ? listId : undefined} aria-activedescendant={open ? `${listId}-${idx}` : undefined}
        placeholder="Message the room. @ to mention a person or an agent, /add-agent to add one, /stop to pause"
        onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); setDismissed(false); setActive(0); }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onPaste={(e) => { const f = pastedImages(e.clipboardData.items); if (f.length) { e.preventDefault(); void addFiles(f); } }}
        onKeyDown={onKeyDown}
      />
      <div className="dg-composer-meta">
      {text.trim() && <span className="dg-count" aria-live="off">{wordCount(text)} {wordCount(text) === 1 ? "word" : "words"}{text.length >= MAX_MESSAGE - 1000 ? ` · ${text.length} / ${MAX_MESSAGE}` : ""}</span>}
        <button type="button" className="dg-expand" onClick={() => { setBig((b) => !b); if (ref.current) ref.current.style.height = ""; ref.current?.focus(); }} aria-pressed={big} aria-label={big ? "Make the message box smaller" : "Make the message box bigger"} title={big ? "Smaller" : "Bigger"}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{big ? <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" /> : <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />}</svg>
      </button>
      </div>
      <input ref={fileRef} type="file" multiple hidden onChange={(e) => { const f = imageFiles(e.target.files); e.target.value = ""; if (f.length) void addFiles(f); }} />
      <button type="button" className="dg-attach" onClick={() => fileRef.current?.click()} disabled={disabled || busy} aria-label="Attach a file" title="Attach a picture or a file (or paste or drop one)"><ImageIcon /></button>
      <button type="submit" disabled={disabled || busy || (!text.trim() && !pics.length && !docs.length)}>Send</button>
    </form>
  );
}
