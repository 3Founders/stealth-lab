import { describe, expect, it } from "vitest";
import { agentIdentity, parseCommand, recipientsFor } from "@/lib/delegator/adapters/mcp";
import { attachMockAgent } from "@/lib/delegator/adapters/mock";
import { Broker } from "@/lib/delegator/relay/broker";
import { MAX_DEPTH, type AgentIdentity, type GroupMessage } from "@/lib/delegator/types";

const alice: AgentIdentity = { handle: "@alice", displayName: "Alice", type: "human", status: "online" };
const cc = agentIdentity("@alice", "claude-code");
const oc = agentIdentity("@bob", "opencode");
const msg = (b: Broker, o: Partial<GroupMessage> = {}): GroupMessage => ({
  id: b.newId(), roomId: "#r", sender: alice, recipients: ["@all"], payload: { text: "hi" }, timestamp: 0, depthCounter: 0, ...o,
});
const world = () => { const b = new Broker(); b.addRoom({ id: "#r", title: "r", members: ["@alice", cc.handle, oc.handle] }); return b; };

describe("relay", () => {
  it("hands a packet over and keeps nothing", async () => {
    const b = world(); b.registerAgent(cc.handle);
    const got: string[] = [];
    b.attach(cc.handle, (p) => { got.push(p.message.id); });
    await b.send(msg(b, { recipients: [cc.handle] }));
    expect(got).toHaveLength(1);
    expect(b.pendingCount()).toBe(0);
  });
  it("holds a packet for an absent recipient only until it attaches, then purges it", async () => {
    const b = world(); b.registerAgent(cc.handle);
    await b.send(msg(b, { recipients: [cc.handle] }));
    expect(b.pendingCount()).toBe(1);
    const got: string[] = [];
    b.attach(cc.handle, (p) => { got.push(p.message.id); });
    expect(got).toHaveLength(1);
    expect(b.pendingCount()).toBe(0);
  });
  it("does not wake agents at max depth, and says so; people still see the message", async () => {
    const b = world(); b.registerAgent(cc.handle);
    const events: string[] = []; b.on((e) => events.push(e.kind === "notice" ? e.text : e.kind));
    let woke = 0; b.attach(cc.handle, () => { woke++; });
    const r = await b.send(msg(b, { recipients: [cc.handle], depthCounter: MAX_DEPTH }));
    expect(woke).toBe(0);
    expect(r.held).toEqual([cc.handle]);
    expect(events).toContain("message");
    expect(events).toContain("Max agent delegation depth reached. Human approval required to continue.");
    await b.send(msg(b, { recipients: [cc.handle], depthCounter: MAX_DEPTH - 1 }));
    expect(woke).toBe(1);
  });
  it("pause holds every agent dispatch", async () => {
    const b = world(); b.registerAgent(cc.handle);
    let woke = 0; b.attach(cc.handle, () => { woke++; });
    b.setPaused(true);
    await b.send(msg(b, { recipients: [cc.handle] }));
    expect(woke).toBe(0);
    b.setPaused(false);
    await b.send(msg(b, { recipients: [cc.handle] }));
    expect(woke).toBe(1);
  });
  it("two agents handing a task back and forth are stopped at the cap", async () => {
    const b = world();
    attachMockAgent(b, cc, { delayMs: 0 }); attachMockAgent(b, oc, { delayMs: 0 });
    const hops: number[] = []; b.on((e) => { if (e.kind === "message") hops.push(e.message.depthCounter); });
    await b.send(msg(b, { recipients: [cc.handle], payload: { text: `${cc.handle} ask ${oc.handle}` } }));
    await new Promise((r) => setTimeout(r, 80));
    expect(Math.max(...hops)).toBe(MAX_DEPTH);
    expect(hops.filter((h) => h === MAX_DEPTH)).toHaveLength(1);
  });
});

describe("commands", () => {
  it("parses the room commands", () => {
    expect(parseCommand("/stop")).toEqual({ cmd: "stop" });
    expect(parseCommand('/add-agent --name="opencode" --type="cli"')).toEqual({ cmd: "add-agent", name: "opencode", type: "cli" });
    expect(parseCommand("/invite @bob/cursor")).toEqual({ cmd: "invite", handle: "@bob/cursor" });
    expect(parseCommand("/invite bob")).toMatchObject({ cmd: "error" });
    expect(parseCommand("hello")).toBeNull();
  });
  it("addresses a message to its mentions, or to the room", () => {
    expect(recipientsFor("@alice/claude-code do it")).toEqual(["@alice/claude-code"]);
    expect(recipientsFor("anyone?")).toEqual(["@all"]);
  });
});

import { applyMention, groupMessages, matchHandles, mentionQuery } from "@/lib/delegator/ui";

describe("composer logic", () => {
  it("finds the @mention being typed at the caret", () => {
    expect(mentionQuery("hi @al", 6)).toEqual({ start: 3, query: "al" });
    expect(mentionQuery("@", 1)).toEqual({ start: 0, query: "" });
    expect(mentionQuery("mail me@al", 10)).toBeNull(); // an @ inside a word is not a mention
    expect(mentionQuery("hi @al there", 12)).toBeNull();
  });
  it("completes it with the chosen handle", () => {
    expect(applyMention("hi @al", 6, "@alice/claude-code")).toEqual({ text: "hi @alice/claude-code ", caret: 22 });
  });
  it("ranks prefix matches before substring matches", () => {
    expect(matchHandles(["@bob/opencode", "@alice/claude-code", "@alice"], "al")).toEqual(["@alice/claude-code", "@alice"]);
    expect(matchHandles(["@bob/opencode", "@alice"], "code")).toEqual(["@bob/opencode"]);
  });
  it("groups one sender's quick messages under one header", () => {
    const mk = (id: string, who: AgentIdentity, t: number, d = 0): GroupMessage => ({ id, roomId: "#r", sender: who, recipients: ["@all"], payload: { text: id }, timestamp: t, depthCounter: d });
    const runs = groupMessages([mk("a", alice, 0), mk("b", alice, 60_000), mk("c", cc, 61_000), mk("d", alice, 61_000 + 6 * 60_000)]);
    expect(runs.map((r) => r.messages.map((m) => m.id))).toEqual([["a", "b"], ["c"], ["d"]]);
  });
});
