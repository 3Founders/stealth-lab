import type { ChatMessage, ChatPart } from "@/lib/byok/chat";
import { ProviderError, scrub } from "@/lib/byok/chat";
import { Broker } from "../relay/broker";
import type { AgentIdentity, GroupMessage, TransportPacket } from "../types";
import { toMcpPayload } from "./mcp";
import { MAX_IMAGES_PER_REQUEST, SHRINK_LEVELS, hasPictures, shrinkTurns, visionHint, withoutDataImages } from "../images";

export const ASSISTANT_HANDLE = "@assistant";
export const assistantIdentityFor = (handle: string): AgentIdentity => ({ handle, displayName: handle.replace(/^@/, ""), type: "local_agent", status: "online" });
export const assistantIdentity = assistantIdentityFor(ASSISTANT_HANDLE);

export interface AssistantDeps {
  /** What backs an agent right now, or null when nothing is set up. Called per message; nothing here keeps the key or token. */
  resolve(): Promise<{ stream: (messages: ChatMessage[], signal: AbortSignal) => AsyncIterable<string>; secrets: string[] } | null>;
  /** True when the room has other people in it. An agent there answers only when it is mentioned. */
  shared?(roomId: string): boolean;
  /** Spend guard: false means today's soft cap is reached. */
  allowed(): boolean;
  used(): void;
  /** The room's recent messages, oldest first. */
  history(roomId: string): GroupMessage[];
  onStart(roomId: string, handle: string, controller: AbortController): void;
  onDelta(roomId: string, handle: string, text: string): void;
  onEnd(roomId: string, handle: string): void;
  /** Redraws the request's pictures smaller (level 1..3) or removes them (level 4). Defaults to the browser's canvas; tests pass their own. */
  shrink?(turns: ChatMessage[], level: number): Promise<ChatMessage[]>;
}

/**
 * Maps a room's messages to the turns a provider expects (alternating, starting with a user turn, last 30). Messages from `self`
 * are this agent's own earlier replies; everything else, including other agents, is a user turn labelled with its sender.
 */
export function toChatTurns(history: GroupMessage[], self: string = ASSISTANT_HANDLE): ChatMessage[] {
  type Turn = { role: ChatMessage["role"]; parts: ChatPart[] };
  const turns: Turn[] = [];
  // Only the newest few pictures go to the model; older ones are named but not sent, so one long chat does not resend every picture each time.
  let budget = MAX_IMAGES_PER_REQUEST;
  const recent = history.slice(-30);
  const sendable = new Map<string, number>();
  for (let i = recent.length - 1; i >= 0 && budget > 0; i--) {
    const n = recent[i].sender.handle === self ? 0 : recent[i].payload.attachments?.length ?? 0;
    if (n) { const take = Math.min(n, budget); sendable.set(recent[i].id, take); budget -= take; }
  }
  for (const m of recent) {
    const role: ChatMessage["role"] = m.sender.handle === self ? "assistant" : "user";
    // A picture a model made earlier is in its text as a data: address: far too big to send back. It becomes a note.
    const body = withoutDataImages(m.payload.text);
    const text = role === "user" ? `${m.sender.handle}: ${body}` : body;
    const parts: ChatPart[] = [{ type: "text", text }];
    const atts = role === "user" ? m.payload.attachments ?? [] : [];
    const take = sendable.get(m.id) ?? 0;
    atts.forEach((a, i) => {
      if (i >= atts.length - take) parts.push({ type: "image", mime: a.mime, data: a.data });
      else parts.push({ type: "text", text: "[picture omitted]" });
    });
    const last = turns.at(-1);
    if (last && last.role === role) { last.parts.push({ type: "text", text: "\n\n" }, ...parts); }
    else turns.push({ role, parts });
  }
  while (turns.length && turns[0].role !== "user") turns.shift();
  // Text-only turns stay plain strings, so a model with no picture support gets exactly what it did before.
  return turns.map((t) => (t.parts.every((p) => p.type === "text")
    ? { role: t.role, content: t.parts.map((p) => (p as { text: string }).text).join("") }
    : { role: t.role, content: t.parts }));
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
    const addressed = !message.recipients.includes("@all");
    if (!addressed && (others.length > 0 || deps.shared?.(roomId))) return; // not addressed to it; in a room with other people it waits to be mentioned
    let target: Awaited<ReturnType<AssistantDeps["resolve"]>>;
    try { target = await deps.resolve(); } catch (err) { return broker.notice(roomId, err instanceof Error ? err.message : "The agent could not start."); }
    // A line to the whole room is not a request to this agent, so with no model set up it stays quiet; only a mention gets the prompt.
    if (!target) return addressed ? broker.notice(roomId, "Add a key to chat here, or choose keळ models: both are under Models in your account menu.") : undefined;
    if (!deps.allowed()) return broker.notice(roomId, "Today's reply limit is reached. You can raise it under Models.");

    const controller = new AbortController();
    deps.onStart(roomId, handle, controller);
    broker.setActivity(handle, "processing");
    deps.used();
    let text = "";
    let turns: ChatMessage[] = [];
    try {
      // The message that woke the agent is always part of what it is sent, even if the page's history has not caught up with it.
      const history = deps.history(roomId);
      turns = toChatTurns(history.some((m) => m.id === message.id) ? history : [...history, message], handle);
      // A provider that says the request is too big (413) is not the end: the same pictures are sent again, smaller each time, and
      // as a last step left out, so a large photo never stops the conversation. Only before any reply text has arrived.
      let level = 0;
      let sending = turns;
      for (;;) {
        try {
          for await (const piece of target.stream(sending, controller.signal)) {
            text += piece;
            deps.onDelta(roomId, handle, text);
          }
          break;
        } catch (err) {
          const tooBig = err instanceof ProviderError && err.status === 413;
          if (!tooBig || text || !hasPictures(sending) || level > SHRINK_LEVELS.length) throw err;
          level += 1;
          sending = await (deps.shrink ?? shrinkTurns)(turns, level);
          if (level > SHRINK_LEVELS.length) broker.notice(roomId, "The provider would not take the pictures even at the smallest size, so they were left out of this request.");
        }
      }
    } catch (err) {
      if ((err as { name?: string })?.name !== "AbortError") {
        const said = scrub(err instanceof Error ? err.message : "The provider returned an error.", ...target.secrets);
        const hint = turns.some((t) => Array.isArray(t.content) && t.content.some((p) => p.type === "image")) ? visionHint(said) : null;
        broker.notice(roomId, hint ? `${said} ${hint}` : said);
      }
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
