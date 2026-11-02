// The text the "Onboard Your Agent" button copies. The person pastes it into their own agent (Claude Code, Cursor, Codex, ...) and the
// agent connects itself: it adds kell's MCP server, signs in, and starts checking its inbox under the name the website already registered
// for it. The website does the one thing an agent cannot (registering the name against the signed-in account); the rest is the agent's job.
import { ROOM_PROMPT } from "@/lib/connect-guide";
import { connectorUrl } from "@/lib/connector";

/** A fresh agent name that passes validAgentName (no "claude", "codex" or "gpt" in it). */
export function newOnboardName(rand: () => number = Math.random): string {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  let s = "";
  for (let i = 0; i < 4; i++) s += alphabet[Math.floor(rand() * alphabet.length)];
  return `agent-${s}`;
}

export function onboardPrompt(a: { name: string; room: string; url?: string | null }): string {
  const url = a.url === undefined ? connectorUrl() : a.url;
  const connect = url
    ? `1. Connect kell's MCP server if it is not connected yet. Address: ${url}\n` +
      `   - Claude Code: claude mcp add --transport http kell ${url}\n` +
      `   - Cursor, Codex and others: add a remote MCP server named kell with that address.\n` +
      `   Sign in when asked (the same Google or GitHub account as the kell website), and allow the room tools.`
    : "1. Connect the kell MCP server if it is not connected yet (the address is on the Connect page of the kell website), and sign in.";
  return [
    `You are being onboarded as an agent in a kell room. Do these steps yourself, then tell me when you are ready.`,
    ``,
    connect,
    `2. Your name is "${a.name}" and the room is "${a.room}". Check your kell inbox as ${a.name} (the tool is delegate_inbox) and keep checking it for work. People wake you by writing @${a.name} in the room.`,
    `3. Read the room with room_list and room_read, and reply with room_post. Do not post anything yet; wait to be mentioned.`,
    ``,
    ROOM_PROMPT,
  ].join("\n");
}
