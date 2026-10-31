import { describe, expect, it } from "vitest";
import { ProviderError, type ChatMessage } from "@/lib/byok/chat";
import { ASSISTANT_HANDLE, attachAssistant, type AssistantDeps } from "@/lib/chat/adapters/assistant";
import { hasPictures, shrinkTurns } from "@/lib/chat/images";
import { Broker } from "@/lib/chat/relay/broker";
import type { AgentIdentity, GroupMessage, ImageAttachment, RelayEvent } from "@/lib/chat/types";

const alice: AgentIdentity = { handle: "@alice", displayName: "Alice", type: "human", status: "online" };
const pic: ImageAttachment = { kind: "image", mime: "image/jpeg", data: "BIGBIGBIG", name: "compressedbest111.jpg", width: 4000, height: 3000 };

function setup(streamFn: (messages: ChatMessage[]) => AsyncGenerator<string>, shrinkFn?: AssistantDeps["shrink"]) {
  const b = new Broker();
  b.addRoom({ id: "#r", title: "r", members: ["@alice", ASSISTANT_HANDLE] });
  const events: RelayEvent[] = [];
  b.on((e) => events.push(e));
  const msg: GroupMessage = { id: b.newId(), roomId: "#r", sender: alice, recipients: [ASSISTANT_HANDLE], payload: { text: "what is this?", attachments: [pic] }, timestamp: Date.now(), depthCounter: 0 };
  const deps = {
    resolve: async () => ({ secrets: ["sk-test-0123456789"], stream: (m: ChatMessage[]) => streamFn(m) }),
    allowed: () => true, used() {}, history: () => [] as GroupMessage[],
    onStart() {}, onDelta() {}, onEnd() {},
    ...(shrinkFn ? { shrink: shrinkFn } : {}),
  } as AssistantDeps;
  attachAssistant(b, deps);
  return { b, events, msg };
}
const sentTexts = (events: RelayEvent[]) => events.flatMap((e) => (e.kind === "message" ? [e.message.payload.text] : e.kind === "notice" ? [e.text] : []));
const settle = () => new Promise((r) => setTimeout(r, 30));

describe("a provider that says the request is too large (413)", () => {
  it("is asked again with smaller pictures, and the answer arrives", async () => {
    const calls: number[] = [];
    const levels: number[] = [];
    const { b, events, msg } = setup(
      async function* (m) { calls.push(m.length); if (calls.length < 3) throw new ProviderError("The provider answered 413: the request body is too large", 413); yield "a yellow and blue grid"; },
      async (turns, level) => { levels.push(level); return turns; },
    );
    await b.send(msg);
    await settle();
    expect(calls).toHaveLength(3);
    expect(levels).toEqual([1, 2]);
    expect(sentTexts(events)).toContain("a yellow and blue grid");
    expect(sentTexts(events).join(" ")).not.toMatch(/413/);
  });

  it("leaves the pictures out as a last step, and says so", async () => {
    let tries = 0;
    const { b, events, msg } = setup(
      async function* (m) { tries++; if (hasPictures(m)) throw new ProviderError("The provider answered 413: too large", 413); yield "I could not see the picture, but here is an answer."; },
      (turns, level) => shrinkTurns(turns, level, async (p) => p), // pictures stay "too large" at every size until they are removed
    );
    await b.send(msg);
    await settle();
    expect(tries).toBe(5); // as sent, then three smaller sizes, then without pictures
    expect(sentTexts(events).join(" ")).toMatch(/left out of this request/);
    expect(sentTexts(events)).toContain("I could not see the picture, but here is an answer.");
  });

  it("does not retry a different error, or a request with no pictures", async () => {
    let tries = 0;
    const { b, events, msg } = setup(async function* () { tries++; throw new ProviderError("The provider answered 401: bad key", 401); yield ""; });
    await b.send(msg);
    await settle();
    expect(tries).toBe(1);
    expect(sentTexts(events).join(" ")).toMatch(/401/);
  });
});

describe("shrinking a request", () => {
  const turns = [{ role: "user", content: [{ type: "text", text: "hi" }, { type: "image", mime: "image/png", data: "AAAA" }] }, { role: "user", content: "plain" }];
  it("redraws each picture with the shrink function and leaves text alone", async () => {
    const out = await shrinkTurns(turns, 2, async (p, level) => ({ mime: "image/jpeg", data: `${p.data}-L${level}` }));
    expect(out[0].content).toEqual([{ type: "text", text: "hi" }, { type: "image", mime: "image/jpeg", data: "AAAA-L2" }]);
    expect(out[1].content).toBe("plain");
  });
  it("replaces the pictures with a note past the last level", async () => {
    const out = await shrinkTurns(turns, 4, async () => { throw new Error("not called"); });
    expect(JSON.stringify(out[0].content)).toContain("left out");
    expect(JSON.stringify(out[0].content)).not.toContain("AAAA");
  });
  it("leaves a picture out, with a note, if it cannot be redrawn", async () => {
    const out = await shrinkTurns(turns, 1, async () => { throw new Error("no canvas"); });
    expect(JSON.stringify(out[0].content)).toContain("could not be made smaller");
  });
});
