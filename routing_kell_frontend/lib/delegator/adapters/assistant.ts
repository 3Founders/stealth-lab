import type { ChatMessage } from "@/lib/byok/chat";
import { scrub } from "@/lib/byok/chat";
import { Broker } from "../relay/broker";
import type { AgentIdentity, GroupMessage, TransportPacket } from "../types";
import { toMcpPayload } from "./mcp";

export const ASSISTANT_HANDLE = "@assistant";
export const assistantIdentity: AgentIdentity = { handle: ASSISTANT_HANDLE, displayName: "assistant", type: "local_agent", status: "online" };

export interface AssistantDeps {
  /** The key to use right now, or null when the person has not added one. Called per message; the key is never stored here. */
  resolve(): Promise<{ stream: (messages: ChatMessage[], signal: AbortSignal) => AsyncIterable<string>; secrets: string[] } | null>;
  /** Spend guard: false means today's soft cap is reached. */
  allowed(): boolean;
  used(): void;
  /** The room's recent messages, oldest first. */
  history(roomId: string): GroupMessage[];
  onStart(roomId: string, controller: AbortController): void;
  onDelta(roomId: string, text: string): void;
  onEnd(roomId: string): void;
}

/** Maps a room's messages to the user/assistant turns a provider expects: alternating, starting with the user, last 30. */
export function toChatTurns(history: GroupMessage[]): ChatMessage[] {
  const turns: ChatMessage[] = [];
  for (const m of history.slice(-30)) {
    const role: ChatMessage["role"] = m.sender.handle === ASSISTANT_HANDLE ? "assistant" : "user";
    const content = role === "user" ? `${m.sender.handle}: ${m.payload.text}` : m.payload.text;
    const last = turns.at(-1);
    if (last && last.role === role) last.content += `\n\n${content}`;
    else turns.push({ role, content });
  }
  while (turns.length && turns[0].role !== "user") turns.shift();
  return turns;
}

/**
 * The built-in assistant member of a room, backed by the person's OWN key (see lib/byok). It answers when mentioned, or when it
 * is the only agent in the room; a plain line to the whole room does not wake it while other agents are present. Its replies
 * are streamed to the page as they arrive and then posted as one message. No key, a reached cap and provider errors become a
 * notice in the room, scrubbed of the key. kell never supplies a key of its own.
 */
export function attachAssistant(broker: Broker, deps: AssistantDeps): () => void {
  broker.registerAgent(ASSISTANT_HANDLE);
  return broker.attach(ASSISTANT_HANDLE, async ({ message }: TransportPacket) => {
    const roomId = message.roomId;
    const others = (broker.room(roomId)?.members ?? []).filter((h) => broker.isAgent(h) && h !== ASSISTANT_HANDLE);
    if (message.recipients.includes("@all") && others.length > 0) return; // not addressed to it
    let target: Awaited<ReturnType<AssistantDeps["resolve"]>>;
    try { target = await deps.resolve(); } catch (err) { return broker.notice(roomId, err instanceof Error ? err.message : "The assistant could not start."); }
    if (!target) return broker.notice(roomId, "Add a key to chat here: the assistant uses your own model key. Open Models in your account menu.");
    if (!deps.allowed()) return broker.notice(roomId, "Today's reply limit for your key is reached. You can raise it under Models.");

    const controller = new AbortController();
    deps.onStart(roomId, controller);
    broker.setActivity(ASSISTANT_HANDLE, "processing");
    deps.used();
    let text = "";
    try {
      for await (const piece of target.stream(toChatTurns(deps.history(roomId)), controller.signal)) {
        text += piece;
        deps.onDelta(roomId, text);
      }
    } catch (err) {
      if ((err as { name?: string })?.name !== "AbortError") broker.notice(roomId, scrub(err instanceof Error ? err.message : "The provider returned an error.", ...target.secrets));
    } finally {
      deps.onEnd(roomId);
      broker.setActivity(ASSISTANT_HANDLE, "idle");
    }
    const final = scrub(text, ...target.secrets).trim();
    if (!final) return;
    await broker.send({
      id: broker.newId(), roomId, sender: assistantIdentity, recipients: [message.sender.handle],
      payload: toMcpPayload(controller.signal.aborted ? `${final}\n\n(stopped)` : final, "reply"),
      timestamp: Date.now(), depthCounter: message.depthCounter + 1,
    });
  });
}
