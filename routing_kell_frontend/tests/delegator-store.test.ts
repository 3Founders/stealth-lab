// @vitest-environment node
// Room history on this device: it survives a reload, is encrypted at rest, can't be moved or tampered with, and can be exported.
import { describe, expect, it } from "vitest";
import type { KvStore } from "@/lib/byok/vault";
import type { RelayTask } from "@/lib/delegator/relay-client";
import { readAgents, writeAgents } from "@/lib/delegator/rooms";
import { HistoryStore } from "@/lib/delegator/store";
import type { GroupMessage } from "@/lib/delegator/types";

const mapStore = (): KvStore & { map: Map<string, unknown> } => {
  const map = new Map<string, unknown>();
  return { map, get: async (k) => map.get(k), set: async (k, v) => void map.set(k, v), delete: async (k) => void map.delete(k), keys: async () => [...map.keys()] };
};
const msg = (id: string, room: string, t: number, text: string): GroupMessage => ({
  id, roomId: room, sender: { handle: "@you", displayName: "You", type: "human", status: "online" }, recipients: ["@all"], payload: { text }, timestamp: t, depthCounter: 0,
});
const task = (id: string, room: string, over: Partial<RelayTask> = {}): RelayTask => ({
  id, agent_id: "a", room, from: "u", text: "do it", locators: [], depth: 0, status: "queued", created_at: "2026-10-28T10:00:00Z", claimed_at: null, finished_at: null, result: null, check: null, ...over,
});
const kv = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }; };

describe("history store", () => {
  it("survives a reload: a new store over the same storage reads the same messages, oldest first", async () => {
    const s = mapStore();
    const a = new HistoryStore(s, "u1");
    await a.appendMessage(msg("m2", "#general", 2000, "second"));
    await a.appendMessage(msg("m1", "#general", 1000, "first"));
    await a.appendMessage(msg("m3", "#design", 1500, "elsewhere"));
    const b = new HistoryStore(s, "u1");
    expect((await b.load("#general")).messages.map((m) => m.payload.text)).toEqual(["first", "second"]);
    expect((await b.load("#design")).messages.map((m) => m.payload.text)).toEqual(["elsewhere"]);
    expect(await b.rooms()).toEqual(["#design", "#general"]);
  });

  it("is encrypted at rest: no message text or task text is readable in what is stored", async () => {
    const s = mapStore();
    const h = new HistoryStore(s, "u1");
    await h.appendMessage(msg("m1", "#general", 1000, "the launch code is swordfish"));
    await h.saveTask(task("t1", "#general", { text: "summarise the secret plan", result: "answer: hunter2" }));
    const dump = JSON.stringify([...s.map.entries()].filter(([k]) => k !== "device-key"));
    for (const word of ["swordfish", "secret plan", "hunter2"]) expect(dump).not.toContain(word);
  });

  it("writing a message twice keeps one; a task keeps its latest state", async () => {
    const h = new HistoryStore(mapStore(), "u1");
    await h.appendMessage(msg("m1", "#r", 1, "x")); await h.appendMessage(msg("m1", "#r", 1, "x"));
    await h.saveTask(task("t1", "#r")); await h.saveTask(task("t1", "#r", { status: "done", result: "ok" }));
    const l = await h.load("#r");
    expect(l.messages).toHaveLength(1);
    expect(l.tasks).toHaveLength(1);
    expect(l.tasks[0]).toMatchObject({ status: "done", result: "ok" });
  });

  it("a record moved to another room or tampered with is skipped, never shown", async () => {
    const s = mapStore();
    const h = new HistoryStore(s, "u1");
    await h.appendMessage(msg("m1", "#general", 1000, "hello"));
    await h.appendMessage(msg("m2", "#general", 2000, "world"));
    const [k1, k2] = [...s.map.keys()].filter((k) => k.startsWith("ev:")).sort();
    const sealed = s.map.get(k1) as { room: string };
    s.map.set(`ev:u1:#other:000000000001000:m1`, { ...sealed, room: "#other" }); // moved
    const t = s.map.get(k2) as { cipher: string };
    s.map.set(k2, { ...t, cipher: t.cipher.slice(0, -4) + "AAAA" }); // tampered
    expect((await h.load("#other")).messages).toEqual([]);
    expect((await h.load("#general")).messages.map((m) => m.payload.text)).toEqual(["hello"]);
  });

  it("one account's history is invisible to another account on the same browser", async () => {
    const s = mapStore();
    await new HistoryStore(s, "u1").appendMessage(msg("m1", "#general", 1, "private"));
    const other = new HistoryStore(s, "u2");
    expect((await other.load("#general")).messages).toEqual([]);
    expect(await other.rooms()).toEqual([]);
  });

  it("trim keeps the newest; clearRoom and clearAll remove, and the device key stays", async () => {
    const s = mapStore();
    const h = new HistoryStore(s, "u1");
    for (let i = 1; i <= 5; i++) await h.appendMessage(msg(`m${i}`, "#r", i * 1000, `n${i}`));
    expect(await h.trim("#r", 2)).toBe(3);
    expect((await h.load("#r")).messages.map((m) => m.payload.text)).toEqual(["n4", "n5"]);
    await h.appendMessage(msg("x", "#keep", 1, "k")); await h.saveTask(task("t", "#r"));
    await h.clearRoom("#r");
    expect(await h.rooms()).toEqual(["#keep"]);
    await h.clearAll();
    expect(await h.rooms()).toEqual([]);
    expect(s.map.has("device-key")).toBe(true);
  });

  it("export then import into a fresh device restores messages and tasks, and rejects other files", async () => {
    const a = new HistoryStore(mapStore(), "u1");
    await a.appendMessage(msg("m1", "#general", 1000, "hello")); await a.saveTask(task("t1", "#general", { status: "done", result: "ok" }));
    const bundle = JSON.parse(JSON.stringify(await a.exportAll()));
    expect(bundle.rooms["#general"].messages[0].payload.text).toBe("hello");
    const b = new HistoryStore(mapStore(), "u1");
    expect(await b.importAll(bundle)).toEqual({ messages: 1, tasks: 1 });
    const l = await b.load("#general");
    expect(l.messages[0].payload.text).toBe("hello");
    expect(l.tasks[0].result).toBe("ok");
    await expect(b.importAll({ nope: true })).rejects.toThrow(/history export/);
    await expect(b.importAll(null)).rejects.toThrow(/history export/);
  });
});

describe("in-tab agents survive a reload", () => {
  it("defaults to one assistant, remembers the list (including none), and ignores junk", () => {
    const s = kv();
    expect(readAgents(s, "u1")).toEqual(["@assistant-1"]);
    writeAgents(s, "u1", ["@assistant-1", "@reviewer"]);
    expect(readAgents(s, "u1")).toEqual(["@assistant-1", "@reviewer"]);
    writeAgents(s, "u1", []);
    expect(readAgents(s, "u1")).toEqual([]);
    s.setItem("kel.delegator.agents.u2", JSON.stringify(["@ok", "bad name", 5, "@ok"]));
    expect(readAgents(s, "u2")).toEqual(["@ok"]);
  });
});
