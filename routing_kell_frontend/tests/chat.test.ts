import { describe, expect, it } from "vitest";
import { agentIdentity, parseCommand, recipientsFor } from "@/lib/chat/adapters/mcp";
import { attachMockAgent } from "@/lib/chat/adapters/mock";
import { Broker } from "@/lib/chat/relay/broker";
import { MAX_DEPTH, type AgentIdentity, type GroupMessage } from "@/lib/chat/types";

const alice: AgentIdentity = { handle: "@alice", displayName: "Alice", type: "human", status: "online" };
const cc = agentIdentity("@alice", "agent-1");
const oc = agentIdentity("@bob", "agent-2");
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
    expect(parseCommand('/add-agent --name="reviewer"')).toEqual({ cmd: "add-agent", name: "reviewer" });
    expect(parseCommand("/add-agent")).toEqual({ cmd: "add-agent", name: undefined });
    expect(parseCommand("/invite @bob/cursor")).toMatchObject({ cmd: "error" }); // someone else's agent: not available yet
    expect(parseCommand("hello")).toBeNull();
  });
  it("addresses a message to its mentions, or to the room", () => {
    expect(recipientsFor("@alice/agent-1 do it")).toEqual(["@alice/agent-1"]);
    expect(recipientsFor("anyone?")).toEqual(["@all"]);
  });
});

import { applyMention, groupMessages, matchHandles, mentionQuery } from "@/lib/chat/ui";

describe("composer logic", () => {
  it("finds the @mention being typed at the caret", () => {
    expect(mentionQuery("hi @al", 6)).toEqual({ start: 3, query: "al" });
    expect(mentionQuery("@", 1)).toEqual({ start: 0, query: "" });
    expect(mentionQuery("mail me@al", 10)).toBeNull(); // an @ inside a word is not a mention
    expect(mentionQuery("hi @al there", 12)).toBeNull();
  });
  it("completes it with the chosen handle", () => {
    expect(applyMention("hi @al", 6, "@alice/agent-1")).toEqual({ text: "hi @alice/agent-1 ", caret: 18 });
  });
  it("ranks prefix matches before substring matches", () => {
    expect(matchHandles(["@bob/agent-2", "@alice/agent-1", "@alice"], "al")).toEqual(["@alice/agent-1", "@alice"]);
    expect(matchHandles(["@bob/agent-2", "@alice"], "gent")).toEqual(["@bob/agent-2"]);
  });
  it("groups one sender's quick messages under one header", () => {
    const mk = (id: string, who: AgentIdentity, t: number, d = 0): GroupMessage => ({ id, roomId: "#r", sender: who, recipients: ["@all"], payload: { text: id }, timestamp: t, depthCounter: d });
    const runs = groupMessages([mk("a", alice, 0), mk("b", alice, 60_000), mk("c", cc, 61_000), mk("d", alice, 61_000 + 6 * 60_000)]);
    expect(runs.map((r) => r.messages.map((m) => m.id))).toEqual([["a", "b"], ["c"], ["d"]]);
  });
});

import { SPRINGS, SpringValue, rubberBand, settleTime, springCss, stepSpring } from "@/lib/chat/spring";

describe("spring motion", () => {
  it("settles in about the time iOS's presets do (smooth ~0.8s, bouncy ~1s)", () => {
    expect(settleTime(SPRINGS.smooth)).toBeGreaterThan(0.6);
    expect(settleTime(SPRINGS.smooth)).toBeLessThan(1.0);
    expect(settleTime(SPRINGS.bouncy)).toBeGreaterThan(settleTime(SPRINGS.smooth));
    expect(settleTime(SPRINGS.bouncy)).toBeLessThan(1.4);
  });
  it("a critically damped spring never overshoots; a bouncy one does", () => {
    const peak = (spec: { response: number; damping: number }) => { let s = { x: 0, v: 0 }, m = 0; for (let i = 0; i < 300; i++) { s = stepSpring(s, 1, spec, 1 / 60); m = Math.max(m, s.x); } return m; };
    expect(peak(SPRINGS.smooth)).toBeLessThanOrEqual(1.0001);
    expect(peak(SPRINGS.bouncy)).toBeGreaterThan(1.02);
  });
  it("is the same motion at 30, 60 and 120 frames per second", () => {
    const at = (fps: number) => { let s = { x: 0, v: 0 }; for (let i = 0; i < fps * 0.4; i++) s = stepSpring(s, 100, SPRINGS.smooth, 1 / fps); return s.x; };
    expect(Math.abs(at(30) - at(120))).toBeLessThan(0.5);
    expect(Math.abs(at(60) - at(120))).toBeLessThan(0.5);
  });
  it("keeps its velocity when the target moves mid-flight, instead of restarting from rest", () => {
    const seen: number[] = [];
    const v = new SpringValue(0, SPRINGS.smooth, (x) => seen.push(x));
    (v as unknown as { target: () => number }).target = () => 100;
    for (let i = 0; i < 12; i++) v.tick(1 / 60);
    const speedBefore = v.v;
    expect(speedBefore).toBeGreaterThan(50);
    (v as unknown as { target: () => number }).target = () => 300; // retarget
    v.tick(1 / 60);
    expect(v.v).toBeGreaterThan(speedBefore * 0.9); // it did not stop and start over
    for (let i = 0; i < 400 && !v.tick(1 / 60); i++);
    expect(v.x).toBe(300);
  });
  it("exports a CSS linear() easing that starts at 0 and ends at 1", () => {
    const { easing, ms } = springCss(SPRINGS.smooth);
    expect(easing).toMatch(/^linear\(0, .*, 1\)$/);
    expect(ms).toBeGreaterThan(500);
  });
  it("rubber-bands with the post's curve: starts at slope c, grows more slowly, never passes the view size", () => {
    expect(rubberBand(0, 844)).toBe(0);
    expect(rubberBand(1, 844)).toBeCloseTo(0.55, 1);
    expect(rubberBand(844, 844)).toBeCloseTo(0.55 * 844 * 844 / (844 + 0.55 * 844), 5);
    expect(rubberBand(1e6, 844)).toBeLessThan(844);
    expect(rubberBand(200, 844)).toBeLessThan(rubberBand(400, 844));
  });
});
