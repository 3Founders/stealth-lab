// @vitest-environment node
// The relay client and the room list: what is sent, how errors read, and that rooms behave.
import { describe, expect, it } from "vitest";
import { cursorOf, mentionedNames, mergeTasks, relay, validAgentName, type RelayTask } from "@/lib/delegator/relay-client";
import { DEFAULT_ROOM, MAX_ROOMS, newRoomName, readRooms, withoutRoom, writeRooms } from "@/lib/delegator/rooms";
import type { KeyValue } from "@/lib/byok/spend";

const task = (id: string, created: string, over: Partial<RelayTask> = {}): RelayTask => ({ id, agent_id: "a", room: "#general", from: "u", text: "t", locators: [], depth: 0, status: "queued", created_at: created, claimed_at: null, finished_at: null, result: null, check: null, ...over });
const kv = (): KeyValue => { const m = new Map<string, string>(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) }; };

describe("relay client", () => {
  it("sends the token as a Bearer to /v1/delegate and surfaces the server's sentence on an error", async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const f = (async (url: string, init: RequestInit) => { seen.push({ url, init }); return new Response(JSON.stringify({ error: "an agent named web-chat already exists" }), { status: 409 }); }) as unknown as typeof fetch;
    const r = relay({ token: async () => "TOK", origin: "https://mcp.example.org/", fetchImpl: f });
    await expect(r.addAgent("web-chat")).rejects.toMatchObject({ message: "an agent named web-chat already exists", status: 409 });
    expect(seen[0].url).toBe("https://mcp.example.org/v1/delegate/agents");
    expect((seen[0].init.headers as Record<string, string>).authorization).toBe("Bearer TOK");
    expect(seen[0].init.credentials).toBe("omit");
  });
  it("makes no request when signed out", async () => {
    let called = false;
    const r = relay({ token: async () => null, fetchImpl: (async () => { called = true; return new Response("{}"); }) as unknown as typeof fetch });
    await expect(r.agents()).rejects.toMatchObject({ status: 401 });
    expect(called).toBe(false);
  });
  it("asks for a room's tasks since a cursor", async () => {
    let url = "";
    const r = relay({ token: async () => "T", origin: "https://h", fetchImpl: (async (u: string) => { url = u; return new Response(JSON.stringify({ tasks: [] })); }) as unknown as typeof fetch });
    await r.tasks("#design", "2026-10-28T10:00:00.000Z");
    expect(url).toBe("https://h/v1/delegate/tasks?room=%23design&after=2026-10-28T10%3A00%3A00.000Z");
  });
  it("names: @a and @owner/a are the same agent; names that are not ours are refused", () => {
    expect(mentionedNames("hi @web-chat and @alice/web-chat, also @Other")).toEqual(["web-chat", "other"]);
    expect(validAgentName("@Web-Chat")).toEqual({ name: "web-chat" });
    expect("error" in validAgentName("my claude")).toBe(true);
    expect("error" in validAgentName("bad name!")).toBe(true);
  });
  it("merges by id without duplicating, and the cursor sits just before the latest change", () => {
    const a = task("1", "2026-10-28T10:00:00Z"), a2 = { ...a, status: "done" as const, finished_at: "2026-10-28T10:05:00Z" };
    const m = mergeTasks([a, task("2", "2026-10-28T10:01:00Z")], [a2]);
    expect(m.map((t) => `${t.id}:${t.status}`)).toEqual(["1:done", "2:queued"]);
    expect(cursorOf(m)).toBe("2026-10-28T10:04:59.000Z");
    expect(cursorOf([])).toBeUndefined();
  });
});

describe("rooms", () => {
  it("starts with #general, saves, and survives a reload", () => {
    const s = kv();
    expect(readRooms(s, "u")).toEqual({ rooms: [DEFAULT_ROOM], current: DEFAULT_ROOM });
    writeRooms(s, "u", { rooms: ["#general", "#design"], current: "#design" });
    expect(readRooms(s, "u").current).toBe("#design");
    expect(readRooms(s, "someone-else").rooms).toEqual([DEFAULT_ROOM]);
  });
  it("validates names, duplicates and the limit", () => {
    expect(newRoomName(["#general"], "Design Review")).toEqual({ name: "#design-review" });
    expect("error" in newRoomName(["#general"], "general")).toBe(true);
    expect("error" in newRoomName(["#general"], "no/way")).toBe(true);
    expect("error" in newRoomName(Array.from({ length: MAX_ROOMS }, (_, i) => `#r${i}`), "x")).toBe(true);
  });
  it("removing a room moves to another one, and the last room cannot be removed", () => {
    const l = { rooms: ["#general", "#design"], current: "#design" };
    expect(withoutRoom(l, "#design")).toEqual({ rooms: ["#general"], current: "#general" });
    const one = { rooms: ["#general"], current: "#general" };
    expect(withoutRoom(one, "#general")).toBe(one);
  });
});
