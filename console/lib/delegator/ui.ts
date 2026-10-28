import type { GroupMessage } from "./types";

/** The @mention being typed at the caret, if any: where it starts and what has been typed after the @. */
export function mentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const m = /(^|\s)@([\w/.-]*)$/.exec(before);
  if (!m) return null;
  return { start: before.length - m[2].length - 1, query: m[2] };
}

/** Replaces the @mention being typed with a chosen handle and a trailing space; returns the new text and caret. */
export function applyMention(text: string, caret: number, handle: string): { text: string; caret: number } | null {
  const q = mentionQuery(text, caret);
  if (!q) return null;
  const next = `${text.slice(0, q.start)}${handle} ${text.slice(caret)}`;
  return { text: next, caret: q.start + handle.length + 1 };
}

/** Handles that match what was typed: prefix matches first, then matches anywhere, at most `limit`. */
export function matchHandles(handles: string[], query: string, limit = 6): string[] {
  const q = query.toLowerCase();
  const starts = handles.filter((h) => h.slice(1).toLowerCase().startsWith(q));
  const inside = handles.filter((h) => !starts.includes(h) && h.toLowerCase().includes(q));
  return [...starts, ...inside].slice(0, limit);
}

export const GROUP_WINDOW_MS = 5 * 60 * 1000;

export interface MessageRun {
  key: string;
  sender: GroupMessage["sender"];
  messages: GroupMessage[];
}

/** Consecutive messages from one sender within five minutes share one header, as in Slack and Discord. */
export function groupMessages(list: GroupMessage[]): MessageRun[] {
  const runs: MessageRun[] = [];
  for (const m of list) {
    const last = runs.at(-1);
    const prev = last?.messages.at(-1);
    if (last && prev && last.sender.handle === m.sender.handle && m.timestamp - prev.timestamp <= GROUP_WINDOW_MS && m.depthCounter === prev.depthCounter) last.messages.push(m);
    else runs.push({ key: m.id, sender: m.sender, messages: [m] });
  }
  return runs;
}

export const initials = (handle: string): string => {
  const parts = handle.replace(/^@/, "").split("/");
  return (parts[parts.length - 1].slice(0, 2) || "?").toUpperCase();
};
