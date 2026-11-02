// Search across the rooms on this device (the idea of a Cmd+K search over chats and messages comes from Open WebUI,
// https://github.com/open-webui/open-webui; no code copied). It runs on the messages already stored on this device, decrypted here,
// and nothing about the query leaves the browser.
import type { GroupMessage } from "@/lib/chat/types";
import { splitFiles } from "@/lib/chat/files";

export interface Hit { room: string; id: string; who: string; text: string; t: number }

const words = (q: string): string[] => q.toLowerCase().split(/\s+/).filter(Boolean).slice(0, 12);

/** What a message says in plain text: file tokens read as the file's name, and the "[n pictures]" placeholder stays. */
export function plainText(m: GroupMessage): string {
  const { text, files } = splitFiles(m.payload.text);
  return [text, ...files.map((f) => f.name)].filter(Boolean).join(" ");
}

/**
 * Messages that contain every word of the query, in any order, case-insensitively, newest first. `room` limits it to one room.
 * An empty query finds nothing (the palette shows recent rooms instead).
 */
export function searchMessages(all: Array<{ room: string; m: GroupMessage }>, query: string, opts: { room?: string; limit?: number } = {}): Hit[] {
  const ws = words(query);
  if (!ws.length) return [];
  const out: Hit[] = [];
  for (const { room, m } of all) {
    if (opts.room && room !== opts.room) continue;
    const text = plainText(m);
    const hay = `${text} ${m.sender.displayName}`.toLowerCase();
    if (ws.every((w) => hay.includes(w))) out.push({ room, id: m.id, who: m.sender.displayName || m.sender.handle, text, t: m.timestamp });
  }
  return out.sort((a, b) => b.t - a.t).slice(0, opts.limit ?? 50);
}

/** A short piece of `text` around the first matching word, with "…" where it was cut, split so the matches can be marked. */
export function snippet(text: string, query: string, width = 120): Array<{ s: string; hit: boolean }> {
  const flat = text.replace(/\s+/g, " ").trim();
  const ws = words(query);
  const lower = flat.toLowerCase();
  const first = ws.map((w) => lower.indexOf(w)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, first - Math.floor(width / 3));
  const end = Math.min(flat.length, start + width);
  const piece = `${start > 0 ? "…" : ""}${flat.slice(start, end)}${end < flat.length ? "…" : ""}`;
  if (!ws.length) return [{ s: piece, hit: false }];
  const re = new RegExp(`(${ws.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return piece.split(re).filter((s) => s !== "").map((s) => ({ s, hit: ws.includes(s.toLowerCase()) }));
}
