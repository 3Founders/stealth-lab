"use client";
import { useId, useMemo, useRef, useState } from "react";
import { sends } from "@/lib/chat/prefs";
import { applyMention, matchHandles, mentionQuery } from "@/lib/chat/ui";

/**
 * The message box. Typing "@" opens a list of the room's handles. It follows the W3C combobox pattern: focus stays in
 * the text box, the list is a listbox, and aria-activedescendant names the highlighted option.
 */
export default function Composer({ handles, onSend, disabled, enterToSend = true }: { handles: string[]; onSend: (text: string) => void; disabled?: boolean; enterToSend?: boolean }) {
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
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
  const send = () => {
    const t = text.trim();
    if (!t) return;
    onSend(t);
    setText(""); setCaret(0); setActive(0); setDismissed(false);
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
    <form className="dg-composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
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
        placeholder="Message the room. @ to mention an agent, /add-agent to add one, /stop to pause"
        onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); setDismissed(false); setActive(0); }}
        onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" disabled={disabled || !text.trim()}>Send</button>
    </form>
  );
}
