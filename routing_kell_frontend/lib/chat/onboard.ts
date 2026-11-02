// What "Connect agent" hands over. The website does the one thing an agent cannot do for itself: it registers a name for the agent on the
// signed-in account (POST /v1/delegate/agents). The agent then connects kell's MCP server, signs in as the same person, and calls
// delegate_inbox(agent=<name>): that call is what marks it online (for 60 seconds at a time) and is how it takes work sent to it.
// Claude on the web and ChatGPT cannot be connected from here: their connector is added inside them and approved by the person.
import { ROOM_PROMPT } from "@/lib/connect-guide";
import { connectorUrl } from "@/lib/connector";

export type OnboardKind = "code" | "claude-web" | "chatgpt";

/** The pages where each web tool adds a custom connector (the same links /connect uses). */
export const CONNECTOR_PAGES: Record<"claude-web" | "chatgpt", string> = {
  "claude-web": "https://claude.ai/settings/connectors",
  "chatgpt": "https://chatgpt.com/#settings/Connectors",
};

/** A fresh agent name that passes validAgentName (no "claude", "codex" or "gpt" in it). */
export function newOnboardName(rand: () => number = Math.random): string {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  let s = "";
  for (let i = 0; i < 4; i++) s += alphabet[Math.floor(rand() * alphabet.length)];
  return `agent-${s}`;
}

export function onboardPrompt(a: { name: string; room: string; url?: string | null; kind?: OnboardKind }): string {
  const url = a.url === undefined ? connectorUrl() : a.url;
  const kind = a.kind ?? "code";
  const connect = kind === "code"
    ? (url
      ? `1. Connect kell's MCP server if it is not connected yet. Address: ${url}\n` +
        `   - Claude Code: claude mcp add --transport http kell ${url}\n` +
        `   - Cursor and others: add a remote MCP server named kell with that address.\n` +
        `   Sign in when asked (the same Google or GitHub account as the kell website), and allow the room tools.`
      : "1. Connect the kell MCP server if it is not connected yet (the address is on the Connect page of the kell website), and sign in.")
    : `1. kell's connector should already be added in ${kind === "claude-web" ? "Claude" : "ChatGPT"} (Settings, Connectors) and switched on for this chat. If it is not, tell me and stop.`;
  return [
    `You are being onboarded as an agent in a kell room. Do these steps yourself, then tell me when you are ready.`,
    ``,
    connect,
    `2. Your name is "${a.name}" and the room is "${a.room}". Check your kell inbox as ${a.name} (the tool is delegate_inbox, with agent="${a.name}") and keep checking it for work. People wake you by writing @${a.name} in the room.`,
    `3. Read the room with room_list and room_read, and reply with room_post. Do not post anything yet; wait to be mentioned.`,
    ``,
    ROOM_PROMPT,
  ].join("\n");
}

/** For Claude on the web and ChatGPT: the connector address to paste into their "Add custom connector" form, and what to say after. */
export function connectorInstructions(a: { name: string; room: string; url?: string | null; kind: "claude-web" | "chatgpt" }): string {
  const url = a.url === undefined ? connectorUrl() : a.url;
  const where = a.kind === "claude-web" ? "Claude: Settings, Connectors, Add custom connector" : "ChatGPT: Settings, Connectors (turn on developer mode), Add a connector";
  return [
    `kell connector address: ${url ?? "(open the Connect page on the kell website for the address)"}`,
    ``,
    `1. ${where}. Name it kell and paste the address above. Leave client ID and secret empty.`,
    `2. Press Connect, sign in to kell and approve.`,
    `3. In a chat, turn kell on. Then send this: `,
    ``,
    onboardPrompt({ name: a.name, room: a.room, url: a.url, kind: a.kind }),
  ].join("\n");
}
