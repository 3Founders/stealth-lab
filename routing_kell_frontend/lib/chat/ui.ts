import type { GroupMessage } from "./types";
import { mentionsIn } from "./adapters/mcp";

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

/**
 * Who a sent line is for. One place, so the rule is testable and cannot drift:
 *  - a line that names an agent goes to those agents only; an unknown @name is refused (never handed to @kell);
 *  - @kell answers when named;
 *  - in a room with no other people, an untagged line goes to @kell (the built-in agent is the room's assistant);
 *  - any @handle at all suppresses that default, so @test1 never becomes @kell's message.
 */
export interface RouteDecision {
  toRelay: string[];        // connector agent names (no @)
  localMentions: string[];  // in-tab agent handles (with @)
  unknown: string[];        // mentioned names that are not in this room
  asksKell: boolean;
  defaultToKell: boolean;   // the untagged solo-room rule, for the recipients list
}

export function routeMessage(line: string, opts: { local: string[]; relay: string[]; people: string[] }): RouteDecision {
  const handles = mentionsIn(line).map((h) => h.toLowerCase());
  const names = handles.map((h) => h.replace(/^@/, ""));
  const local = new Set(opts.local.map((h) => h.toLowerCase()));
  const relay = new Set(opts.relay.map((n) => n.toLowerCase()));
  const people = new Set(opts.people.map((h) => h.replace(/^@/, "").toLowerCase()));
  const anyAt = handles.length > 0 || /(^|\s)@[a-z0-9]/i.test(line);
  const toRelay = names.filter((n) => relay.has(n));
  const localMentions = handles.filter((h) => local.has(h));
  const unknown = names.filter((n) => n !== "kell" && !local.has(`@${n}`) && !relay.has(n) && !people.has(n));
  const defaultToKell = !anyAt && opts.people.length === 0;
  const asksKell = defaultToKell || handles.includes("@kell");
  return { toRelay, localMentions, unknown, asksKell, defaultToKell };
}

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
