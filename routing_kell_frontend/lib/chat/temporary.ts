// Temporary rooms: a chat that is never saved and never synced. It lives in the page's memory, the agent answers through
// POST /v1/agent/ephemeral (which reads and writes no room), and the room is gone when the page closes. (The idea of a temporary chat
// comes from Open WebUI, https://github.com/open-webui/open-webui; no code copied.)
import { plainText } from "@/lib/chat/search";
import type { GroupMessage } from "@/lib/chat/types";

/** A fresh label for a temporary room, not already in use. */
export function tempLabel(existing: string[], rand: () => number = Math.random): string {
  const alphabet = "abcdefghijkmnpqrstuvwxyz23456789";
  for (let tries = 0; tries < 50; tries++) {
    let s = "";
    for (let i = 0; i < 4; i++) s += alphabet[Math.floor(rand() * alphabet.length)];
    const label = `#temp-${s}`;
    if (!existing.includes(label)) return label;
  }
  return `#temp-${Date.now().toString(36)}`;
}

/** The conversation so far as the agent expects it: its own earlier answers as assistant turns, everyone else's as user turns named
 * "Name (@handle): text". Pictures are not sent; the text of a file message keeps the file's name. */
export function toTurns(messages: GroupMessage[], who: string): Array<{ role: "user" | "assistant"; content: string }> {
  const out: Array<{ role: "user" | "assistant"; content: string }> = [];
  for (const m of messages) {
    const text = plainText(m).trim();
    if (!text) continue;
    if (m.sender.handle.toLowerCase() === who.toLowerCase()) out.push({ role: "assistant", content: text });
    else out.push({ role: "user", content: `${m.sender.displayName || m.sender.handle} (${m.sender.handle}): ${text}` });
  }
  return out.slice(-30);
}
