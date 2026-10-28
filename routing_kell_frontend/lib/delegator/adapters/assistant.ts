import type { ChatMessage } from "@/lib/byok/chat";
import { scrub } from "@/lib/byok/chat";
import { Broker } from "../relay/broker";
import type { AgentIdentity, GroupMessage, TransportPacket } from "../types";
import { toMcpPayload } from "./mcp";

export const ASSISTANT_HANDLE = "@assistant";
export const assistantIdentityFor = (handle: string): AgentIdentity => ({ handle, displayName: handle.replace(/^@/, ""), type: "local_agent", status: "online" });
export const assistantIdentity = assistantIdentityFor(ASSISTANT_HANDLE);

export interface AssistantDeps {
  /** What backs an agent right now, or null when nothing is set up. Called per message; nothing here keeps the key or token. */
  resolve(): Promise<{ stream: (messages: ChatMessage[], signal: AbortSignal) => AsyncIterable<string>; secrets: string[] } | null>;
  /** Spend guard: false means today's soft cap is reached. */
  allowed(): boolean;
  used(): void;
  /** The room's recent messages, oldest first. */
  history(roomId: string): GroupMessage[];
  onStart(roomId: string, handle: string, controller: AbortController): void;
  onDelta(roomId: string, handle: string, text: string): void;
  onEnd(roomId: string, handle: string): void;
}

/**
 * Maps a room's messages to the turns a provider expects (alternating, starting with a user turn, last 30). Messages from `self`
 * are this agent's own earlier replies; everything else, including other agents, is a user turn labelled with its sender.
 */
export function toChatTurns(history: GroupMessage[], self: string = ASSISTANT_HANDLE): ChatMessage[] {
  const turns: ChatMessage[] = [];
  for (const m of history.slice(-30)) {
    const role: ChatMessage["role"] = m.sender.handle === self ? "assistant" : "user";
    const content = role === "user" ? `${m.sender.handle}: ${m.payload.text}` : m.payload.text;
    const last = turns.at(-1);
    if (last && last.role === role) last.content += `\n\n${content}`;
    else turns.push({ role, content });
  }
  while (turns.length && turns[0].role !== "user") turns.shift();
  return turns;
}

/**
 * One agent of a room, backed by a real model call (the person's own key, or the opt-in keळ models: see resolve.ts). Each agent is
 * its own instance: its own handle, its own replies, the same room history. It answers when mentioned, or when it is the only agent
 * in the room; a plain line to the whole room does not wake it while other agents are present. Its reply is streamed to the page
 * as it arrives and then posted as one message. Nothing set up, a reached cap and provider errors become a notice in the room,
 * scrubbed of the key or token.
 */
export function attachAssistant(broker: Broker, deps: AssistantDeps, handle: string = ASSISTANT_HANDLE): () => void {
  broker.registerAgent(handle);
  const identity = assistantIdentityFor(handle);
  return broker.attach(handle, async ({ message }: TransportPacket) => {
    const roomId = message.roomId;
    const others = (broker.room(roomId)?.members ?? []).filter((h) => broker.isAgent(h) && h !== handle);
    if (message.recipients.includes("@all") && others.length > 0) return; // not addressed to it
    let target: Awaited<ReturnType<AssistantDeps["resolve"]>>;
    try { target = await deps.resolve(); } catch (err) { return broker.notice(roomId, err instanceof Error ? err.message : "The agent could not start."); }
    if (!target) return broker.notice(roomId, "Add a key to chat here, or choose keळ models: both are under Models in your account menu.");
    if (!deps.allowed()) return broker.notice(roomId, "Today's reply limit is reached. You can raise it under Models.");

    const controller = new AbortController();
    deps.onStart(roomId, handle, controller);
    broker.setActivity(handle, "processing");
    deps.used();
    let text = "";
    try {
      for await (const piece of target.stream(toChatTurns(deps.history(roomId), handle), controller.signal)) {
        text += piece;
        deps.onDelta(roomId, handle, text);
      }
    } catch (err) {
      if ((err as { name?: string })?.name !== "AbortError") broker.notice(roomId, scrub(err instanceof Error ? err.message : "The provider returned an error.", ...target.secrets));
    } finally {
      deps.onEnd(roomId, handle);
      broker.setActivity(handle, "idle");
    }
    const final = scrub(text, ...target.secrets).trim();
    if (!final) return;
    await broker.send({
      id: broker.newId(), roomId, sender: identity, recipients: [message.sender.handle],
      payload: toMcpPayload(controller.signal.aborted ? `${final}\n\n(stopped)` : final, "reply"),
      timestamp: Date.now(), depthCounter: message.depthCounter + 1,
    });
  });
}
