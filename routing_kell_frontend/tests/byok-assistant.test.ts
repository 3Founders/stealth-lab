// @vitest-environment node
// The built-in assistant: answers when addressed, says why when it cannot, and never lets the key reach the room.
import { describe, expect, it } from "vitest";
import { DEFAULT_DAILY_CAP, readCap, recordUse, usedToday, withinCap, writeCap, type KeyValue } from "@/lib/byok/spend";
import { ASSISTANT_HANDLE, attachAssistant, toChatTurns, type AssistantDeps } from "@/lib/chat/adapters/assistant";
import { attachMockAgent } from "@/lib/chat/adapters/mock";
import { agentIdentity } from "@/lib/chat/adapters/mcp";
import { Broker } from "@/lib/chat/relay/broker";
import type { AgentIdentity, GroupMessage, RelayEvent } from "@/lib/chat/types";

const SECRET = "sk-live-ROOMLEAKSECRET-0123456789";
const alice: AgentIdentity = { handle: "@alice", displayName: "Alice", type: "human", status: "online" };

function world(members = ["@alice", ASSISTANT_HANDLE]) {
  const b = new Broker();
  b.addRoom({ id: "#r", title: "r", members });
  const events: RelayEvent[] = [];
  b.on((e) => events.push(e));
  return { b, events };
}
const say = (b: Broker, text: string, recipients: string[] = [ASSISTANT_HANDLE]): GroupMessage => ({
  id: b.newId(), roomId: "#r", sender: alice, recipients, payload: { text }, timestamp: Date.now(), depthCounter: 0,
});
const stream = (pieces: string[], fail?: Error) => ({
  secrets: [SECRET],
  stream: async function* () { for (const p of pieces) yield p; if (fail) throw fail; },
});
function deps(over: Partial<AssistantDeps> = {}): AssistantDeps & { started: string[]; ended: string[]; deltas: string[]; usedCount: number } {
  const d = {
    started: [] as string[], ended: [] as string[], deltas: [] as string[], usedCount: 0,
    resolve: async () => stream(["Hel", "lo"]),
    allowed: () => true,
    used() { d.usedCount++; },
    history: () => [] as GroupMessage[],
    onStart(r: string) { d.started.push(r); },
    onDelta(_r: string, _h: string, t: string) { d.deltas.push(t); },
    onEnd(r: string) { d.ended.push(r); },
  };
  return Object.assign(d, over) as never;
}
const texts = (events: RelayEvent[]) => events.flatMap((e) => (e.kind === "message" ? [e.message.payload.text] : e.kind === "notice" ? [e.text] : []));

describe("assistant", () => {
  it("answers a mention: streams as it arrives, then posts one message and counts one use", async () => {
    const { b, events } = world();
    const d = deps();
    attachAssistant(b, d);
    await b.send(say(b, "@assistant hi"));
    await new Promise((r) => setTimeout(r, 20));
    expect(d.started).toEqual(["#r"]);
    expect(d.deltas).toEqual(["Hel", "Hello"]);
    expect(d.ended).toEqual(["#r"]);
    expect(d.usedCount).toBe(1);
    const reply = events.find((e) => e.kind === "message" && e.message.sender.handle === ASSISTANT_HANDLE);
    expect(reply && reply.kind === "message" && reply.message.payload.text).toBe("Hello");
  });

  it("the message that woke it is always sent, even when the page's history has not caught up yet", async () => {
    const { b } = world();
    const seen: string[][] = [];
    const d = deps({ history: () => [], resolve: async () => ({ secrets: [SECRET], stream: async function* (messages: { role: string; content: string }[]) { seen.push(messages.map((m) => `${m.role}:${m.content}`)); yield "ok"; } }) });
    attachAssistant(b, d);
    await b.send(say(b, "@assistant hello there"));
    await new Promise((r) => setTimeout(r, 10));
    expect(seen).toEqual([["user:@alice: @assistant hello there"]]);
  });

  it("with no key it says so in the room and makes no request", async () => {
    const { b, events } = world();
    const d = deps({ resolve: async () => null });
    attachAssistant(b, d);
    await b.send(say(b, "@assistant hi"));
    await new Promise((r) => setTimeout(r, 10));
    expect(texts(events).some((t) => /Add a key to chat here/.test(t))).toBe(true);
    expect(d.started).toEqual([]);
    expect(d.usedCount).toBe(0);
  });

  it("a plain line to the whole room does not wake it while other agents are present, but does when it is the only agent", async () => {
    const withOthers = world(["@alice", "@alice/agent-1", ASSISTANT_HANDLE]);
    attachMockAgent(withOthers.b, agentIdentity("@alice", "agent-1"), { delayMs: 0 });
    const d1 = deps();
    attachAssistant(withOthers.b, d1);
    await withOthers.b.send(say(withOthers.b, "anyone?", ["@all"]));
    await new Promise((r) => setTimeout(r, 20));
    expect(d1.started).toEqual([]);

    const alone = world();
    const d2 = deps();
    attachAssistant(alone.b, d2);
    await alone.b.send(say(alone.b, "anyone?", ["@all"]));
    await new Promise((r) => setTimeout(r, 20));
    expect(d2.started).toEqual(["#r"]);
  });

  it("a reached daily cap is a notice, not a request", async () => {
    const { b, events } = world();
    const d = deps({ allowed: () => false });
    attachAssistant(b, d);
    await b.send(say(b, "@assistant hi"));
    await new Promise((r) => setTimeout(r, 10));
    expect(texts(events).some((t) => /reply limit is reached/.test(t))).toBe(true);
    expect(d.started).toEqual([]);
  });

  it("stopping keeps what had arrived and marks it stopped", async () => {
    const { b, events } = world();
    const d = deps({
      resolve: async () => ({ secrets: [SECRET], stream: async function* (_m: unknown, signal: AbortSignal) { yield "part"; await new Promise<void>((r) => signal.addEventListener("abort", () => r())); throw Object.assign(new Error("aborted"), { name: "AbortError" }); } }),
      onStart: (_r, _h, c) => { setTimeout(() => c.abort(), 10); },
    });
    attachAssistant(b, d);
    await b.send(say(b, "@assistant go"));
    await new Promise((r) => setTimeout(r, 60));
    expect(texts(events).some((t) => t === "part\n\n(stopped)")).toBe(true);
  });

  it("the key never reaches the room: a provider error and the reply are both scrubbed", async () => {
    const { b, events } = world();
    const d = deps({ resolve: async () => stream([`here is your key ${SECRET} in the reply`], new Error(`401 invalid key ${SECRET}`)) });
    attachAssistant(b, d);
    await b.send(say(b, "@assistant hi"));
    await new Promise((r) => setTimeout(r, 20));
    const everything = JSON.stringify(events) + d.deltas.join("");
    // what the page streams while the reply arrives is allowed to be raw only for the reply text; the room's messages and notices must be clean
    expect(JSON.stringify(events)).not.toContain(SECRET);
    expect(JSON.stringify(events)).not.toContain("ROOMLEAKSECRET");
    expect(everything.length).toBeGreaterThan(0);
  });

  it("a failure to start (a key that cannot be read) is a notice with no secret", async () => {
    const { b, events } = world();
    attachAssistant(b, deps({ resolve: async () => { throw new Error("That key can't be read in this browser any more."); } }));
    await b.send(say(b, "@assistant hi"));
    await new Promise((r) => setTimeout(r, 10));
    expect(texts(events).some((t) => /can't be read/.test(t))).toBe(true);
  });
});

describe("several agents", () => {
  it("a second agent is its own instance: only the mentioned one answers, as itself, and it sees the other as a user turn", async () => {
    const { b, events } = world(["@alice", "@assistant-1", "@assistant-2"]);
    const heard: string[] = [];
    const msg = say(b, "@assistant-2 hello", ["@assistant-2"]);
    const mk = (name: string) => deps({ history: () => [msg], resolve: async () => ({ secrets: [SECRET], stream: async function* (messages: { role: string; content: string }[]) { heard.push(`${name}:${messages.map((m) => m.role).join(",")}`); yield `hi from ${name}`; } }) });
    attachAssistant(b, mk("one"), "@assistant-1");
    attachAssistant(b, mk("two"), "@assistant-2");
    await b.send(msg);
    await new Promise((r) => setTimeout(r, 20));
    const replies = events.flatMap((e) => (e.kind === "message" && e.message.sender.handle.startsWith("@assistant") ? [[e.message.sender.handle, e.message.payload.text]] : []));
    expect(replies).toEqual([["@assistant-2", "hi from two"]]);
    expect(heard).toEqual(["two:user"]);
  });
  it("history turns are labelled from each agent's own point of view", () => {
    const m = (who: string, text: string): GroupMessage => ({ id: text, roomId: "#r", sender: { ...alice, handle: who }, recipients: ["@all"], payload: { text }, timestamp: 0, depthCounter: 0 });
    const history = [m("@alice", "q"), m("@assistant-1", "a1"), m("@assistant-2", "a2")];
    expect(toChatTurns(history, "@assistant-1").map((t) => `${t.role}:${t.content}`)).toEqual(["user:@alice: q", "assistant:a1", "user:@assistant-2: a2"]);
    expect(toChatTurns(history, "@assistant-2").map((t) => t.role)).toEqual(["user", "assistant"]);
  });
});

describe("conversation turns", () => {
  const m = (who: string, text: string): GroupMessage => ({ id: text, roomId: "#r", sender: { ...alice, handle: who }, recipients: ["@all"], payload: { text }, timestamp: 0, depthCounter: 0 });
  it("alternates user and assistant, merges neighbours, starts with the user, and prefixes the sender", () => {
    const turns = toChatTurns([m(ASSISTANT_HANDLE, "stray"), m("@alice", "a"), m("@bob", "b"), m(ASSISTANT_HANDLE, "c"), m("@alice", "d")]);
    expect(turns.map((t) => t.role)).toEqual(["user", "assistant", "user"]);
    expect(turns[0].content).toBe("@alice: a\n\n@bob: b");
  });
});

describe("daily cap", () => {
  const mem = (): KeyValue => { const m = new Map<string, string>(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) }; };
  it("counts today's replies against a default or chosen cap, and resets on a new day", () => {
    const kv = mem();
    const day1 = new Date(2026, 9, 28, 10);
    expect(readCap(kv, "u")).toBe(DEFAULT_DAILY_CAP);
    writeCap(kv, "u", 2);
    expect(withinCap(kv, "u", day1)).toBe(true);
    recordUse(kv, "u", day1); recordUse(kv, "u", day1);
    expect(usedToday(kv, "u", day1)).toBe(2);
    expect(withinCap(kv, "u", day1)).toBe(false);
    expect(withinCap(kv, "u", new Date(2026, 9, 29, 9))).toBe(true);
  });
  it("is per account and ignores nonsense", () => {
    const kv = mem();
    writeCap(kv, "a", 1); recordUse(kv, "a");
    expect(withinCap(kv, "b")).toBe(true);
    kv.setItem("kel.byok.cap.c", "-5");
    expect(readCap(kv, "c")).toBe(DEFAULT_DAILY_CAP);
  });
});
