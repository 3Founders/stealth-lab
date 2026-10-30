"use client";
import { useEffect, useRef, useState } from "react";
import { askPermission, permission, type Note, type Permission, type Prefs, type RoomLevel } from "@/lib/chat/notify";

const LABEL: Record<Note["kind"], string> = {
  mention: "Mention", message: "Message", agent: "Agent", task: "Task", approval: "Needs approval", room: "Room",
};
const LEVELS: Array<[RoomLevel, string]> = [["all", "Everything"], ["mentions", "Mentions and results"], ["mute", "Mute (mentions only)"]];

/** The bell: recent notifications, and the settings that decide which ones reach you and how. */
export default function NotificationBell({ notes, prefs, room, onPrefs, onOpen, onReadAll, onClear }: {
  notes: Note[]; prefs: Prefs; room: string;
  onPrefs: (p: Prefs) => void; onOpen: (room: string | null) => void; onReadAll: () => void; onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [perm, setPerm] = useState<Permission>("default");
  const box = useRef<HTMLDivElement>(null);
  const unread = notes.filter((n) => !n.read).length;

  useEffect(() => { setPerm(permission()); }, [open]);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);

  async function toggleDesktop(on: boolean) {
    if (!on) return onPrefs({ ...prefs, desktop: false });
    const p = perm === "granted" ? "granted" : await askPermission();
    setPerm(p);
    onPrefs({ ...prefs, desktop: p === "granted" });
  }

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
            <label><input type="checkbox" checked={prefs.desktop && perm === "granted"} onChange={(e) => void toggleDesktop(e.target.checked)} disabled={perm === "unsupported" || perm === "denied"} /> Alerts when this tab is in the background</label>
            {perm === "denied" && <p className="dg-hint">Your browser is blocking alerts for this site. Allow them in the site settings, then turn this on.</p>}
            {perm === "unsupported" && <p className="dg-hint">This browser can&rsquo;t show alerts. The bell and tab title still update.</p>}
            <label><input type="checkbox" checked={prefs.sound} onChange={(e) => onPrefs({ ...prefs, sound: e.target.checked })} /> Sound</label>
            <label><input type="checkbox" checked={prefs.previews} onChange={(e) => onPrefs({ ...prefs, previews: e.target.checked })} /> Show message text in alerts</label>
            <label><input type="checkbox" checked={prefs.quiet.on} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, on: e.target.checked } })} /> Quiet hours
              {" "}<input type="time" aria-label="Quiet hours start" value={prefs.quiet.from} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, from: e.target.value } })} />
              {" – "}<input type="time" aria-label="Quiet hours end" value={prefs.quiet.to} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, to: e.target.value } })} /></label>
            <label>This room ({room}){" "}
              <select value={prefs.rooms[room] ?? "all"} onChange={(e) => onPrefs({ ...prefs, rooms: { ...prefs.rooms, [room]: e.target.value as RoomLevel } })}>
                {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></label>
            <p className="dg-hint">Alerts work while keळ is open in a tab, even in the background. They can&rsquo;t reach you with the browser closed yet.</p>
          </fieldset>
        </div>
      )}
    </div>
  );
}
