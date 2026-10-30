"use client";
import { useEffect, useState } from "react";
import { askPermission, permission, type Permission, type Prefs, type RoomLevel } from "@/lib/chat/notify";

const LEVELS: Array<[RoomLevel, string]> = [["all", "Everything"], ["mentions", "Mentions and results"], ["mute", "Mute (mentions only)"]];

/** The notification switches, shared by the bell's small panel and the Settings window. */
export default function NotificationSettings({ prefs, room, onPrefs }: { prefs: Prefs; room: string; onPrefs: (p: Prefs) => void }) {
  const [perm, setPerm] = useState<Permission>("default");
  useEffect(() => { setPerm(permission()); }, []);

  async function toggleDesktop(on: boolean) {
    if (!on) return onPrefs({ ...prefs, desktop: false });
    const p = perm === "granted" ? "granted" : await askPermission();
    setPerm(p);
    onPrefs({ ...prefs, desktop: p === "granted" });
  }

  return (
    <div className="dg-bell-set-body">
      <label><input type="checkbox" checked={prefs.desktop && perm === "granted"} onChange={(e) => void toggleDesktop(e.target.checked)} disabled={perm === "unsupported" || perm === "denied"} /> Alerts when this tab is in the background</label>
      {perm === "denied" && <p className="dg-hint">Your browser is blocking alerts for this site. Allow them in the site settings, then turn this on.</p>}
      {perm === "unsupported" && <p className="dg-hint">This browser can&rsquo;t show alerts. The bell and tab title still update.</p>}
      <label><input type="checkbox" checked={prefs.sound} onChange={(e) => onPrefs({ ...prefs, sound: e.target.checked })} /> Sound</label>
      <label><input type="checkbox" checked={prefs.previews} onChange={(e) => onPrefs({ ...prefs, previews: e.target.checked })} /> Show message text in alerts</label>
      <label>
        <input type="checkbox" checked={prefs.quiet.on} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, on: e.target.checked } })} /> Quiet hours{" "}
        <input type="time" aria-label="Quiet hours start" value={prefs.quiet.from} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, from: e.target.value } })} />
        {" – "}<input type="time" aria-label="Quiet hours end" value={prefs.quiet.to} onChange={(e) => onPrefs({ ...prefs, quiet: { ...prefs.quiet, to: e.target.value } })} />
      </label>
      <label>This room ({room}){" "}
        <select value={prefs.rooms[room] ?? "all"} onChange={(e) => onPrefs({ ...prefs, rooms: { ...prefs.rooms, [room]: e.target.value as RoomLevel } })}>
          {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <p className="dg-hint">Alerts work while keळ is open in a tab, even in the background. They can&rsquo;t reach you with the browser closed yet.</p>
    </div>
  );
}
