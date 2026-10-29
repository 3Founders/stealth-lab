import { Broker } from "../relay/broker";
import type { AgentIdentity, GroupMessage, TransportPacket } from "../types";
import { mentionsIn, toMcpPayload } from "./mcp";

/**
 * A stand-in for a real agent bridge (stdio/PTY or MCP). It takes a packet, shows "processing", then replies through
 * the same relay with depth + 1, handing on to any other agent the incoming text mentions. That chain is what lets the
 * sandbox exercise the loop guard. A real adapter replaces `think`; the relay contract stays the same.
 */
export function attachMockAgent(broker: Broker, me: AgentIdentity, opts: { delayMs?: number; think?: (m: GroupMessage) => string } = {}): () => void {
  const delay = opts.delayMs ?? 700;
  broker.registerAgent(me.handle);
  // A canned acknowledgement, not an echo: quoting the previous reply back would nest a little more at every hop.
  const think = opts.think ?? ((m) => `${me.displayName} here (hop ${m.depthCounter + 1}). Looking into it.`);
  return broker.attach(me.handle, async ({ message }: TransportPacket) => {
    broker.setActivity(me.handle, "processing");
    await new Promise((r) => setTimeout(r, delay));
    const others = mentionsIn(message.payload.text).filter((h) => h !== me.handle && broker.isAgent(h));
    const to = others.length ? others : [message.sender.handle];
    const text = `${to.join(" ")} ${think(message)}`;
    void broker.send({
      id: broker.newId(), roomId: message.roomId, sender: me, recipients: to,
      payload: toMcpPayload(text, "reply"), timestamp: Date.now(), depthCounter: message.depthCounter + 1,
    });
  });
}
