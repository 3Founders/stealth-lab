/**
 * Your rooms. A room is a label on messages and tasks (the server keeps no room object), so the list is yours: it is kept in this
 * browser per account. Removing a room removes the label from your list; tasks already sent under it stay on the server until
 * they expire (7 days).
 */
import type { KeyValue } from "@/lib/byok/spend";

export const DEFAULT_ROOM = "#general";
export const MAX_ROOMS = 12;
const key = (subject: string) => `kel.delegator.rooms.${subject}`;
const ROOM = /^#[a-z0-9][a-z0-9_-]{0,29}$/;

export interface RoomList { rooms: string[]; current: string }

export function readRooms(kv: KeyValue, subject: string): RoomList {
  try {
    const v = JSON.parse(kv.getItem(key(subject)) ?? "null") as { rooms?: unknown; current?: unknown } | null;
    const rooms = Array.isArray(v?.rooms) ? v!.rooms.filter((r): r is string => typeof r === "string" && ROOM.test(r)) : [];
    if (rooms.length) return { rooms, current: typeof v?.current === "string" && rooms.includes(v.current) ? v.current : rooms[0] };
  } catch { /* fall through */ }
  return { rooms: [DEFAULT_ROOM], current: DEFAULT_ROOM };
}

export function writeRooms(kv: KeyValue, subject: string, list: RoomList): void {
  try { kv.setItem(key(subject), JSON.stringify(list)); } catch { /* storage blocked: the list lasts until the page closes */ }
}

export function newRoomName(existing: string[], raw: string): { name: string } | { error: string } {
  if (existing.length >= MAX_ROOMS) return { error: `You can have at most ${MAX_ROOMS} rooms.` };
  const name = `#${raw.trim().replace(/^#/, "").toLowerCase().replace(/\s+/g, "-")}`;
  if (!ROOM.test(name)) return { error: "Use up to 30 letters, numbers, dashes or underscores." };
  if (existing.includes(name)) return { error: `${name} already exists.` };
  return { name };
}

/** The list after removing `room`, and the room to show. The last room can't be removed. */
export function withoutRoom(list: RoomList, room: string): RoomList {
  if (list.rooms.length <= 1 || !list.rooms.includes(room)) return list;
  const rooms = list.rooms.filter((r) => r !== room);
  return { rooms, current: list.current === room ? rooms[0] : list.current };
}

// The in-tab agents you have added (their handles; they are not secret), so a reload brings the same agents back.
const agentsKey = (subject: string) => `kel.delegator.agents.${subject}`;
const AGENT = /^@[a-z0-9][a-z0-9_-]{0,23}$/i;
export const DEFAULT_AGENTS = ["@assistant-1"];

export function readAgents(kv: KeyValue, subject: string, max = 5): string[] {
  try {
    const v = JSON.parse(kv.getItem(agentsKey(subject)) ?? "null") as unknown;
    if (Array.isArray(v)) return [...new Set(v.filter((h): h is string => typeof h === "string" && AGENT.test(h)))].slice(0, max);
  } catch { /* fall through */ }
  return [...DEFAULT_AGENTS];
}

export function writeAgents(kv: KeyValue, subject: string, agents: string[]): void {
  try { kv.setItem(agentsKey(subject), JSON.stringify(agents)); } catch { /* storage blocked */ }
}
