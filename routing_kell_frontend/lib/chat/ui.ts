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

/** The slash commands the message box understands (parseCommand in lib/chat/adapters/mcp.ts runs them). */
export const SLASH_COMMANDS: Array<{ name: string; hint: string }> = [
  { name: "/add-agent", hint: "Add another kell agent to this room (--name=scout --about=\"what it should do\")" },
  { name: "/stop", hint: "Pause every agent until you resume" },
  { name: "/export", hint: "Save this room as a Markdown file" },
  { name: "/temporary", hint: "Start a room that is not saved or synced" },
];

/** The command being typed: the line starts with "/" and the caret is still inside that first word. */
export function slashQuery(text: string, caret: number): string | null {
  const m = /^\/([\w-]*)$/.exec(text.slice(0, caret));
  return m && !/\s/.test(text.slice(0, caret)) ? m[1] : null;
}

/** Commands that match what was typed. A command typed in full matches nothing, so Enter sends it instead of re-picking it. */
export function matchCommands(query: string): Array<{ name: string; hint: string }> {
  const q = query.toLowerCase();
  if (SLASH_COMMANDS.some((c) => c.name.slice(1) === q)) return [];
  return SLASH_COMMANDS.filter((c) => c.name.slice(1).startsWith(q));
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
  remoteKells: string[];    // another person's kell named in the line ("@anuj/kell"): answered on the server from THEIR memory
  defaultToKell: boolean;   // the untagged solo-room rule, for the recipients list
}

/**
 * Who a message without an @name goes to. `follow` is the agent that spoke last in the room ("kell", a connected agent's name, or a local
 * agent's @handle): a reply with no @ continues that conversation, so nobody types @kell or @claude-code before every answer.
 */
export function routeMessage(line: string, opts: { local: string[]; relay: string[]; people: string[]; follow?: string | null; remoteKells?: string[] }): RouteDecision {
  const handles = mentionsIn(line).map((h) => h.toLowerCase());
  const names = handles.map((h) => h.replace(/^@/, ""));
  const local = new Set(opts.local.map((h) => h.toLowerCase()));
  const relay = new Set(opts.relay.map((n) => n.toLowerCase()));
  const people = new Set(opts.people.map((h) => h.replace(/^@/, "").toLowerCase()));
  const anyAt = handles.length > 0 || /(^|\s)@[a-z0-9]/i.test(line);
  const follow = (opts.follow ?? "").replace(/^@/, "").toLowerCase();
  const sticky = !anyAt && follow !== "";
  const toRelay = sticky && relay.has(follow) ? [follow] : names.filter((n) => relay.has(n));
  const localMentions = sticky && local.has(`@${follow}`) && follow !== "kell" ? [`@${follow}`] : handles.filter((h) => local.has(h));
  const remote = new Set((opts.remoteKells ?? []).map((h) => h.replace(/^@/, "").toLowerCase()));
  const remoteKells = handles.filter((h) => remote.has(h.replace(/^@/, "")));
  const unknown = names.filter((n) => n !== "kell" && !local.has(`@${n}`) && !relay.has(n) && !people.has(n) && !remote.has(n));
  const defaultToKell = !anyAt && opts.people.length === 0;
  const asksKell = defaultToKell || handles.includes("@kell") || (sticky && follow === "kell");
  return { toRelay, localMentions, unknown, asksKell, defaultToKell, remoteKells };
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
