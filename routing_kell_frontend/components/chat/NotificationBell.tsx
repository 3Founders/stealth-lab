"use client";
import NotificationSettings from "@/components/chat/NotificationSettings";
import type { Note, Prefs } from "@/lib/chat/notify";

const LABEL: Record<Note["kind"], string> = {
  mention: "Mention", message: "Message", agent: "Agent", task: "Task", approval: "Needs approval", room: "Room",
};

export interface AlertsProps {
  notes: Note[]; prefs: Prefs; room: string;
  onPrefs: (p: Prefs) => void; onOpen: (room: string | null) => void; onRead: (id: string) => void;
  onClear: () => void;
}

/** The alerts panel: recent notifications, and the settings that decide which ones reach you and how. It has no button of its own; the
 * chat menu opens it (components/chat/HamburgerMenu.tsx). An alert is removed as soon as the person sees its room or clicks it. */
export default function NotificationPanel({ notes, prefs, room, onPrefs, onOpen, onRead, onClear, onClose, onBack }: AlertsProps & { onClose: () => void; onBack: () => void }) {
  return (
    <div className="dg-bell-pop dg-bell-pop--menu" role="dialog" aria-label="Notifications">
      <div className="dg-bell-head">
        <b>Notifications</b>
        <span>
          <button type="button" className="dg-mini" onClick={onBack}>Back</button>{" "}
          <button type="button" className="dg-mini" onClick={onClear} disabled={!notes.length}>Clear all</button>
        </span>
      </div>
      {notes.length === 0
        ? <p className="dg-hint">Nothing yet. Mentions, agent results, approvals and new messages in rooms you are not looking at show up here.</p>
        : <ul className="dg-bell-list">{notes.map((n) => (
            <li key={n.id} data-read={n.read}>
              <button type="button" onClick={() => { onRead(n.id); onOpen(n.room); onClose(); }}>
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
  );
}
