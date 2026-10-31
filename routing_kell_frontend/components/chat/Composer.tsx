"use client";
import { useId, useMemo, useRef, useState } from "react";
import { sends } from "@/lib/chat/prefs";
import { applyMention, matchHandles, mentionQuery } from "@/lib/chat/ui";
import { ACCEPT, dataUrlOf, prepareImage, whyNotImage } from "@/lib/chat/images";
import type { ImageAttachment } from "@/lib/chat/types";
import { CrossIcon, ImageIcon } from "@/components/chat/icons";

/**
 * The message box. Typing "@" opens a list of the room's handles. It follows the W3C combobox pattern: focus stays in
 * the text box, the list is a listbox, and aria-activedescendant names the highlighted option.
 */
export default function Composer({ handles, onSend, disabled, enterToSend = true }: { handles: string[]; onSend: (text: string, attachments?: ImageAttachment[]) => void; disabled?: boolean; enterToSend?: boolean }) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [pics, setPics] = useState<ImageAttachment[]>([]);
  const [picError, setPicError] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const listId = useId();

  const q = mentionQuery(text, caret);
  const options = useMemo(() => (q && !dismissed ? matchHandles(handles, q.query) : []), [q, dismissed, handles]);
  const open = options.length > 0;
  const idx = Math.min(active, Math.max(0, options.length - 1));

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
    setBusy(true);
    try {
      for (const f of files) {
        const why = whyNotImage(f, pics.length + next.length);
        if (why) { setPicError(why); break; }
        try { next.push(await prepareImage(f)); } catch (err) { setPicError(err instanceof Error ? err.message : "That picture could not be added."); break; }
      }
    } finally { setBusy(false); }
    if (next.length) setPics((p) => [...p, ...next]);
  };
  const send = () => {
    const t = text.trim();
    if ((!t && !pics.length) || busy) return;
    if (pics.length) onSend(t, pics); else onSend(t);
    setText(""); setCaret(0); setActive(0); setDismissed(false); setPics([]); setPicError("");
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
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((idx + 1) % options.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive((idx - 1 + options.length) % options.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(options[idx]); return; }
      if (e.key === "Escape") { e.preventDefault(); setDismissed(true); return; }
    }
    if (sends({ key: e.key, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, composing: e.nativeEvent.isComposing }, enterToSend)) { e.preventDefault(); send(); }
  };

  return (
    <form
      className="dg-composer" data-over={over || undefined} onSubmit={(e) => { e.preventDefault(); send(); }}
      onDragOver={(e) => { if (Array.from(e.dataTransfer.types).includes("Files")) { e.preventDefault(); setOver(true); } }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { setOver(false); const f = imageFiles(e.dataTransfer.files); if (f.length) { e.preventDefault(); void addFiles(f); } }}
    >
      {(pics.length > 0 || picError) && (
        <div className="dg-pics">
          {pics.map((p, i) => (
            <span key={i} className="dg-pic">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={dataUrlOf(p)} alt={p.name} width={56} height={56} />
              <button type="button" onClick={() => setPics((x) => x.filter((_, j) => j !== i))} aria-label={`Remove ${p.name}`} title="Remove"><CrossIcon /></button>
            </span>
          ))}
          {picError && <span className="dg-pic-err" role="status">{picError}</span>}
        </div>
      )}
      {open && (
        <ul id={listId} role="listbox" aria-label="Mention a member" className="dg-mentions">
          {options.map((h, i) => (
            <li key={h} id={`${listId}-${i}`} role="option" aria-selected={i === idx} data-active={i === idx}
                onMouseDown={(e) => { e.preventDefault(); pick(h); }}>
              <code>{h}</code>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={ref} rows={1} value={text} disabled={disabled}
        role="combobox" aria-label="Message" aria-autocomplete="list" aria-expanded={open}
        aria-controls={open ? listId : undefined} aria-activedescendant={open ? `${listId}-${idx}` : undefined}
        placeholder="Message the room. @ to mention a person or an agent, /add-agent to add one, /stop to pause"
        onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); setDismissed(false); setActive(0); }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onPaste={(e) => { const f = pastedImages(e.clipboardData.items); if (f.length) { e.preventDefault(); void addFiles(f); } }}
        onKeyDown={onKeyDown}
      />
      <input ref={fileRef} type="file" accept={ACCEPT} multiple hidden onChange={(e) => { const f = imageFiles(e.target.files); e.target.value = ""; if (f.length) void addFiles(f); }} />
      <button type="button" className="dg-attach" onClick={() => fileRef.current?.click()} disabled={disabled || busy || pics.length >= 4} aria-label="Attach a picture" title="Attach a picture (or paste or drop one)"><ImageIcon /></button>
      <button type="submit" disabled={disabled || busy || (!text.trim() && !pics.length)}>Send</button>
    </form>
  );
}
