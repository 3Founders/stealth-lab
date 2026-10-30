"use client";
import { useEffect, useRef, useState } from "react";
import NotificationSettings from "@/components/chat/NotificationSettings";
import type { Note, Prefs } from "@/lib/chat/notify";

const LABEL: Record<Note["kind"], string> = {
  mention: "Mention", message: "Message", agent: "Agent", task: "Task", approval: "Needs approval", room: "Room",
};

/** The bell: recent notifications, and the settings that decide which ones reach you and how. */
export default function NotificationBell({ notes, prefs, room, onPrefs, onOpen, onReadAll, onClear }: {
  notes: Note[]; prefs: Prefs; room: string;
  onPrefs: (p: Prefs) => void; onOpen: (room: string | null) => void; onReadAll: () => void; onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const unread = notes.filter((n) => !n.read).length;

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="dg-bell" ref={box}>
      <button type="button" className="dg-mini" aria-haspopup="dialog" aria-expanded={open} aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"} onClick={() => { setOpen((o) => !o); }}>
        <span aria-hidden="true">Alerts</span>{unread > 0 && <span className="dg-unread" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="dg-bell-pop" role="dialog" aria-label="Notifications">
          <div className="dg-bell-head">
            <b>Notifications</b>
            <span><button type="button" className="dg-mini" onClick={onReadAll} disabled={!unread}>Mark all read</button> <button type="button" className="dg-mini" onClick={onClear} disabled={!notes.length}>Clear</button></span>
          </div>
          {notes.length === 0
            ? <p className="dg-hint">Nothing yet. Mentions, agent results, approvals and new messages in rooms you are not looking at show up here.</p>
            : <ul className="dg-bell-list">{notes.map((n) => (
                <li key={n.id} data-read={n.read}>
                  <button type="button" onClick={() => { onOpen(n.room); setOpen(false); }}>
                    <span className="dg-bell-kind">{LABEL[n.kind]}{n.room ? ` · ${n.room}` : ""}</span>
                    <b>{n.title}</b>
                    <span>{n.body}</span>
                  </button>
                </li>))}</ul>}
          <fieldset className="dg-bell-set">
            <legend>Settings</legend>
            <NotificationSettings prefs={prefs} room={room} onPrefs={onPrefs} />
          </fieldset>
        </div>
      )}
    </div>
  );
}
