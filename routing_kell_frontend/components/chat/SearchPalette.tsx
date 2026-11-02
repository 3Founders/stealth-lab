"use client";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { roomName } from "@/components/chat/bare";
import { useSlideHighlight } from "@/components/chat/useSlideHighlight";
import { searchMessages, snippet } from "@/lib/chat/search";
import type { GroupMessage } from "@/lib/chat/types";

/**
 * Search the rooms on this device (Cmd+K or Ctrl+K). Type words; every word has to appear. Up and Down choose, Enter opens the room
 * at that message, Escape closes. With nothing typed it lists your rooms. Messages are read from this device only.
 */
export default function SearchPalette({ open, onClose, rooms, currentRoom, load, onPick }: {
  open: boolean; onClose: () => void; rooms: string[]; currentRoom: string;
  load: () => Promise<Array<{ room: string; m: GroupMessage }>>;
  onPick: (room: string, id: string | null) => void;
}) {
  const [q, setQ] = useState("");
  const [all, setAll] = useState<Array<{ room: string; m: GroupMessage }> | null>(null);
  const [here, setHere] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const slide = useSlideHighlight();

  useEffect(() => {
    if (!open) return;
    setQ(""); setActive(0); setAll(null);
    let live = true;
    void load().then((x) => { if (live) setAll(x); }).catch(() => { if (live) setAll([]); });
    requestAnimationFrame(() => input.current?.focus());
    return () => { live = false; };
  }, [open, load]);

  const hits = useMemo(() => (all ? searchMessages(all, q, { room: here ? currentRoom : undefined, limit: 30 }) : []), [all, q, here, currentRoom]);
  const roomRows = useMemo(() => (q.trim() ? [] : rooms), [q, rooms]);
  const count = q.trim() ? hits.length : roomRows.length;
  const idx = Math.min(active, Math.max(0, count - 1));

  if (!open) return null;
  const pick = (i: number) => {
    if (q.trim()) { const h = hits[i]; if (h) { onPick(h.room, h.id); onClose(); } }
    else { const r = roomRows[i]; if (r) { onPick(r, null); onClose(); } }
  };
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "ArrowDown" && count) { e.preventDefault(); setActive((idx + 1) % count); }
    else if (e.key === "ArrowUp" && count) { e.preventDefault(); setActive((idx - 1 + count) % count); }
    else if (e.key === "Enter" && count) { e.preventDefault(); pick(idx); }
  };

  return (
    <div className="dg-search-wrap" data-lenis-prevent onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dg-search" role="dialog" aria-modal="true" aria-label="Search" onKeyDown={key}>
        <input
          ref={input} value={q} onChange={(e) => { setQ(e.target.value); setActive(0); }} placeholder="Search your rooms and messages"
          role="combobox" aria-expanded aria-controls={listId} aria-activedescendant={count ? `${listId}-${idx}` : undefined} aria-autocomplete="list" autoComplete="off"
        />
        <button type="button" role="switch" aria-checked={here} className="dg-search-here" onClick={() => { setHere(!here); setActive(0); }}>
          <span className="dg-switch" aria-hidden="true"><i /></span>Only {roomName(currentRoom)}
        </button>
        <ul id={listId} role="listbox" className="dg-search-list" ref={slide('li[role="option"]')}>
          {q.trim() === "" && roomRows.map((r, i) => (
            <li key={r} id={`${listId}-${i}`} role="option" aria-selected={i === idx} data-active={i === idx} onMouseDown={(e) => { e.preventDefault(); pick(i); }}>
              <b>{roomName(r)}</b><span className="dim small">Open room</span>
            </li>
          ))}
          {q.trim() !== "" && hits.map((h, i) => (
            <li key={`${h.room}:${h.id}`} id={`${listId}-${i}`} role="option" aria-selected={i === idx} data-active={i === idx} onMouseDown={(e) => { e.preventDefault(); pick(i); }}>
              <span className="dim small">{roomName(h.room)} · {h.who} · {new Date(h.t).toLocaleDateString()}</span>
              <span>{snippet(h.text, q).map((p, j) => (p.hit ? <mark key={j}>{p.s}</mark> : <span key={j}>{p.s}</span>))}</span>
            </li>
          ))}
        </ul>
        {q.trim() !== "" && all !== null && hits.length === 0 && <p className="dg-search-none small dim" role="status">Nothing matches. Only messages saved on this device are searched.</p>}
        {all === null && <p className="dg-search-none small dim" role="status">Reading your rooms…</p>}
      </div>
    </div>
  );
}
