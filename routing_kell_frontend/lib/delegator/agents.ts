import type { AgentIdentity } from "./types";

/** The person using the page. There are no pretend people in the room. */
export const HUMAN: AgentIdentity = { handle: "@you", displayName: "You", type: "human", status: "online" };
export const ROOM_ID = "#room";
/** Every agent is a real model call that costs the person (or keळ's pilot allowance) money, so there is a ceiling. */
export const MAX_AGENTS = 5;

/** The handle for a new agent: the name asked for, or the first free "@assistant-N". */
export function newAgentHandle(existing: string[], name?: string): { handle: string } | { error: string } {
  if (existing.length >= MAX_AGENTS) return { error: `A room holds at most ${MAX_AGENTS} agents.` };
  if (name !== undefined && name !== "") {
    if (!/^[a-z0-9][a-z0-9_-]{0,23}$/i.test(name)) return { error: "Use up to 24 letters, numbers, dashes or underscores for an agent's name." };
    const handle = `@${name.toLowerCase()}`;
    if (handle === HUMAN.handle || existing.map((h) => h.toLowerCase()).includes(handle)) return { error: `${handle} is already in the room.` };
    return { handle };
  }
  for (let n = 1; ; n++) if (!existing.includes(`@assistant-${n}`)) return { handle: `@assistant-${n}` };
}
