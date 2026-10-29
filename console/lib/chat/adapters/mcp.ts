import type { AgentIdentity, DualPayload } from "../types";

const HANDLE = /^@[a-z0-9][a-z0-9_-]*(\/[a-z0-9][a-z0-9_.-]*)?$/i;
export const isHandle = (s: string) => HANDLE.test(s);

/** Every @handle written in a line of prose. */
export function mentionsIn(text: string): string[] {
  const out = text.match(/@[a-z0-9][a-z0-9_-]*(?:\/[a-z0-9][a-z0-9_.-]*)?/gi) ?? [];
  return [...new Set(out)];
}

/** Who a message is for: its mentions, or the whole room when it mentions nobody. */
export function recipientsFor(text: string): string[] {
  const m = mentionsIn(text);
  return m.length ? m : ["@all"];
}

/** The agent-readable half of a message: an MCP-style envelope that carries the same words as the Markdown. */
export function toMcpPayload(text: string, action = "message", locators?: Array<{ path: string; lines?: string }>): DualPayload {
  return { text, structuredPayload: { protocol: "application/mcp+json", action, ...(locators ? { locators } : {}), data: { text } } };
}

export type Command =
  | { cmd: "stop" }
  | { cmd: "add-agent"; name: string; type: string }
  | { cmd: "invite"; handle: string }
  | { cmd: "error"; message: string };

/** Slash commands typed into the room. Anything that is not one returns null and is sent as an ordinary message. */
export function parseCommand(line: string): Command | null {
  const t = line.trim();
  if (!t.startsWith("/")) return null;
  const [word, ...rest] = t.split(/\s+/);
  if (word === "/stop") return { cmd: "stop" };
  if (word === "/invite") {
    const h = rest[0] ?? "";
    return isHandle(h) ? { cmd: "invite", handle: h } : { cmd: "error", message: "Usage: /invite @owner/agent" };
  }
  if (word === "/add-agent") {
    const arg = (k: string) => new RegExp(`--${k}=(?:"([^"]*)"|(\\S+))`).exec(t);
    const n = arg("name"), ty = arg("type");
    const name = n?.[1] ?? n?.[2];
    if (!name || !/^[a-z0-9][a-z0-9_.-]*$/i.test(name)) return { cmd: "error", message: 'Usage: /add-agent --name="opencode" --type="cli"' };
    return { cmd: "add-agent", name, type: ty?.[1] ?? ty?.[2] ?? "cli" };
  }
  return { cmd: "error", message: `Unknown command ${word}.` };
}

/** Builds the identity of an agent registered into a room by its owner and name. */
export function agentIdentity(owner: string, name: string, type: "local_agent" | "remote_agent" = "local_agent"): AgentIdentity {
  return { handle: `${owner}/${name}`, displayName: name, type, status: "online" };
}
