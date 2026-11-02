// Lines sent while an agent is still answering wait in a queue and go out as one follow-up when it has finished (the "keep typing while
// the AI responds" behaviour, an idea from Open WebUI, https://github.com/open-webui/open-webui; no code copied). Pure functions over a
// plain map so the rule is testable; the key is "<room>|<agent handle>".
export type Queues = Record<string, string[]>;

export const queueKey = (room: string, who: string): string => `${room}|${who}`;

export function enqueue(q: Queues, key: string, text: string): Queues {
  return { ...q, [key]: [...(q[key] ?? []), text] };
}

/** Everything waiting for `key`, oldest first, joined into one message; and the queues without it. */
export function drain(q: Queues, key: string): { text: string | null; rest: Queues } {
  const list = q[key];
  if (!list?.length) return { text: null, rest: q };
  const { [key]: _gone, ...rest } = q;
  return { text: list.join("\n\n"), rest };
}

export function removeAt(q: Queues, key: string, index: number): Queues {
  const list = (q[key] ?? []).filter((_, i) => i !== index);
  if (list.length) return { ...q, [key]: list };
  const { [key]: _gone, ...rest } = q;
  return rest;
}

/** The waiting lines of one room, with the agent each is waiting for. */
export function inRoom(q: Queues, room: string): Array<{ key: string; who: string; index: number; text: string }> {
  return Object.entries(q).filter(([k]) => k.startsWith(`${room}|`)).flatMap(([k, list]) => list.map((text, index) => ({ key: k, who: k.slice(room.length + 1), index, text })));
}
