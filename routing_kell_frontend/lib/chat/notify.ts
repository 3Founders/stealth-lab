/**
 * Notifications for Chat: what is worth telling a person about, how loudly, and how it is delivered. Design notes and the research
 * behind the rules are in docs/chat_notifications_e2ee_mcp.md.
 *
 * The rules (`decide`) are pure so they can be tested without a browser:
 *   - never notify about your own messages, or about the room you are looking at in a visible tab;
 *   - a room has a level: "all", "mentions" (only mentions, agent results and approvals) or "mute" (nothing, except a direct mention);
 *   - something that needs a person (an approval, a mention) outranks the room level;
 *   - quiet hours silence the sound and the operating-system alert but not the in-app bell;
 *   - an OS alert is shown only when the tab is hidden; a visible tab gets the bell, the title count and an optional sound.
 *
 * Delivery (`deliver`) uses the Notification API (one alert per room, replaced rather than stacked), the tab title and the app badge
 * where the browser has one. It works while a tab is open, hidden or in the background. It does NOT wake a closed browser: that
 * needs Web Push with a service worker and a push sender on the server, which is designed in the doc and not built yet.
 *
 * Privacy: previews are on by default and can be turned off, in which case the alert says only who and where. The list of recent
 * notifications is kept in memory and never written to disk, because it contains message text and the history on disk is encrypted.
 */
export type NoteKind = "mention" | "message" | "agent" | "task" | "approval" | "room";
export type RoomLevel = "all" | "mentions" | "mute";

export interface Prefs {
  /** Operating-system alerts when the tab is hidden. Needs the browser's permission, asked on the click that turns it on. */
  desktop: boolean;
  sound: boolean;
  previews: boolean;
  quiet: { on: boolean; from: string; to: string };   // "HH:MM" local time
  rooms: Record<string, RoomLevel>;
}

export const DEFAULT_PREFS: Prefs = { desktop: false, sound: true, previews: true, quiet: { on: false, from: "22:00", to: "07:00" }, rooms: {} };

export interface Note { id: string; kind: NoteKind; room: string | null; title: string; body: string; at: number; read: boolean }

export interface Event {
  kind: NoteKind;
  room: string | null;
  /** The room is on screen in a visible tab. */
  onScreen: boolean;
  hidden: boolean;
  mine?: boolean;
}

export interface Decision { record: boolean; os: boolean; sound: boolean }

const minutes = (hhmm: string): number => { const [h, m] = hhmm.split(":").map(Number); return (h || 0) * 60 + (m || 0); };

export function inQuietHours(q: Prefs["quiet"], now: Date): boolean {
  if (!q.on) return false;
  const t = now.getHours() * 60 + now.getMinutes(), a = minutes(q.from), b = minutes(q.to);
  if (a === b) return false;
  return a < b ? t >= a && t < b : t >= a || t < b;   // a window that crosses midnight
}

export function levelOf(prefs: Prefs, room: string | null): RoomLevel {
  return (room && prefs.rooms[room]) || "all";
}

export function decide(e: Event, prefs: Prefs, now: Date = new Date()): Decision {
  const none: Decision = { record: false, os: false, sound: false };
  if (e.mine) return none;
  if (e.onScreen && !e.hidden) return none;
  const level = levelOf(prefs, e.room);
  const urgent = e.kind === "approval" || e.kind === "mention";
  if (level === "mute" && e.kind !== "mention") return none;
  if (level === "mentions" && !urgent && e.kind !== "task" && e.kind !== "agent" && e.kind !== "room") return none;
  const quiet = inQuietHours(prefs.quiet, now);
  return { record: true, os: prefs.desktop && e.hidden && !quiet, sound: prefs.sound && !quiet };
}

export function previewText(prefs: Prefs, text: string): string {
  if (!prefs.previews) return "New message";
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > 140 ? `${t.slice(0, 137)}...` : t;
}

export function titleWithCount(base: string, unread: number): string {
  return unread > 0 ? `(${unread > 99 ? "99+" : unread}) ${base}` : base;
}

// ---- preferences storage (no secrets: switches and room levels) ----------------------------------------------------------

const prefsKey = (subject: string) => `kel.chat.notify.${subject}`;

export function loadPrefs(subject: string, store: Pick<Storage, "getItem"> = window.localStorage): Prefs {
  try {
    const v = JSON.parse(store.getItem(prefsKey(subject)) ?? "null") as Partial<Prefs> | null;
    if (!v || typeof v !== "object") return DEFAULT_PREFS;
    const q = (v.quiet ?? {}) as Partial<Prefs["quiet"]>;
    const rooms: Record<string, RoomLevel> = {};
    for (const [k, lv] of Object.entries(v.rooms ?? {})) if (lv === "all" || lv === "mentions" || lv === "mute") rooms[k] = lv;
    return {
      desktop: v.desktop === true, sound: v.sound !== false, previews: v.previews !== false,
      quiet: { on: q.on === true, from: /^\d\d:\d\d$/.test(q.from ?? "") ? q.from! : "22:00", to: /^\d\d:\d\d$/.test(q.to ?? "") ? q.to! : "07:00" },
      rooms,
    };
  } catch { return DEFAULT_PREFS; }
}

export function savePrefs(subject: string, prefs: Prefs, store: Pick<Storage, "setItem"> = window.localStorage): void {
  try { store.setItem(prefsKey(subject), JSON.stringify(prefs)); } catch { /* storage may be blocked */ }
}

// ---- delivery ------------------------------------------------------------------------------------------------------------

export type Permission = "granted" | "denied" | "default" | "unsupported";
export const permission = (): Permission => (typeof Notification === "undefined" ? "unsupported" : Notification.permission);

/** Asks the browser for permission. Call it from a click: browsers ignore or block it otherwise. */
export async function askPermission(): Promise<Permission> {
  if (typeof Notification === "undefined") return "unsupported";
  try { return await Notification.requestPermission(); } catch { return Notification.permission; }
}

let audio: AudioContext | null = null;
/** A short two-note chime made in the browser (no sound file). Silent if the browser has not allowed audio yet. */
export function chime(): void {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    audio = audio ?? new Ctx();
    const t0 = audio.currentTime;
    [[660, 0], [880, 0.11]].forEach(([hz, dt]) => {
      const o = audio!.createOscillator(), g = audio!.createGain();
      o.type = "sine"; o.frequency.value = hz;
      g.gain.setValueAtTime(0.0001, t0 + dt); g.gain.exponentialRampToValueAtTime(0.08, t0 + dt + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.22);
      o.connect(g); g.connect(audio!.destination); o.start(t0 + dt); o.stop(t0 + dt + 0.25);
    });
  } catch { /* sound is a nicety */ }
}

export function setBadge(unread: number): void {
  const nav = navigator as Navigator & { setAppBadge?: (n?: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
  try { if (unread > 0) void nav.setAppBadge?.(unread); else void nav.clearAppBadge?.(); } catch { /* not supported */ }
}

/** Shows an operating-system alert. One per room: a newer one replaces the older instead of stacking. */
export function showOs(note: Note, onOpen: (room: string | null) => void): void {
  if (permission() !== "granted") return;
  try {
    const n = new Notification(note.title, { body: note.body, tag: note.room ?? "kel-chat", icon: "/kel-mark.png", silent: true });
    n.onclick = () => { try { window.focus(); } catch { /* none */ } onOpen(note.room); n.close(); };
  } catch { /* some browsers only allow alerts from a service worker */ }
}
