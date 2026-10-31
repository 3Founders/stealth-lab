/** The mapping between what the room shows (a GroupMessage) and what the server stores (an event), in both directions. */
import { toMcpPayload } from "./adapters/mcp";
import { HUMAN } from "./agents";
import type { EventIn, ServerEvent } from "./rooms-api";
import type { GroupMessage } from "./types";

/**
 * A message typed or produced on this device, as an event to send. The event id IS the message id (a UUID), so a send that is
 * retried, or a message that comes back from the server, is the same message and never shows twice. People are shown by their
 * account name on other devices; the handle is only sent for agents.
 */
export function toEvent(m: GroupMessage, deviceId: string, lamport: number): EventIn {
  const agent = m.sender.type !== "human";
  return {
    id: m.id, kind: "msg", body: m.payload.text, sender_kind: agent ? "agent" : "person",
    ...(agent ? { sender_handle: m.sender.handle, depth: Math.max(0, Math.min(4, m.depthCounter)) } : {}),
    lamport, device_id: deviceId,
  };
}

/** What to show for someone who isn't you: their account name, else "member". */
export const personHandle = (name: string | null) => `@${(name ?? "member").trim().toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "") || "member"}`;

/** Someone whose account has no name yet: "Member 1a2b" (a few letters of their id), so two such people are not the same name. */
export const fallbackName = (sub: string | null | undefined): string => `Member ${(sub ?? "").replace(/[^a-z0-9]/gi, "").slice(0, 4).toLowerCase() || "?"}`.trim();

/** A stored event as a message in the room, or null for kinds the room doesn't show (yet). */
export function toMessage(e: ServerEvent, roomLabel: string): GroupMessage | null {
  if (e.kind !== "msg" && e.kind !== "system") return null;
  const mine = e.sender.me;
  const agent = e.sender.kind === "agent";
  const sender = agent
    ? { handle: e.sender.handle, displayName: e.sender.handle.replace(/^@/, ""), type: "local_agent" as const, status: "online" as const }
    : mine
      ? HUMAN
      : { handle: personHandle(e.sender.name ?? fallbackName(e.sender.sub)), displayName: e.sender.name ?? fallbackName(e.sender.sub), type: "human" as const, status: "online" as const, sub: e.sender.sub };
  return {
    id: e.id, roomId: roomLabel, sender,
    recipients: e.mentions.length ? e.mentions.map((n) => `@${n}`) : ["@all"],
    payload: toMcpPayload(e.body, e.kind === "system" ? "system" : "message"),
    timestamp: Date.parse(e.created_at) || Date.now(), depthCounter: e.depth,
  };
}

/** A new random id for a message or event. */
export const newEventId = (): string => crypto.randomUUID();
