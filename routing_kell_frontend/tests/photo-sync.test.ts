// The profile photo follows the account: server wins, an old local photo is uploaded once, a removal elsewhere is honoured.
import { describe, expect, it } from "vitest";
import { dataUrlToBytes, photoSync } from "@/lib/photo-sync";

const URL_A = "data:image/jpeg;base64,/9j/4AAQSkZJRg==";
const URL_B = "data:image/jpeg;base64,AAECAwQFBgc=";

function rig(server: { photo: string | null }, opts: { offline?: boolean; synced?: boolean; local?: string | null } = {}) {
  const mem = new Map<string, string | null>([["u1", opts.local ?? null]]);
  const flags = new Set<string>(opts.synced ? ["u1"] : []);
  const calls: string[] = [];
  const fetchImpl = (async (_u: string, init: RequestInit) => {
    calls.push(String(init.method));
    if (opts.offline) throw new Error("down");
    if (init.method === "GET") {
      if (server.photo === null) return new Response(JSON.stringify({ error: "no photo" }), { status: 404, headers: { "content-type": "application/json" } });
      const img = dataUrlToBytes(server.photo)!;
      return new Response(img.bytes.buffer as ArrayBuffer, { status: 200, headers: { "content-type": img.type } });
    }
    if (init.method === "PUT") { server.photo = "data:image/jpeg;base64," + btoa(String.fromCharCode(...new Uint8Array(init.body as ArrayBuffer))); return new Response(null, { status: 204 }); }
    server.photo = null;
    return new Response(null, { status: 204 });
  }) as unknown as typeof fetch;
  const sync = photoSync({
    token: async () => "tok", origin: "https://mcp.example", fetchImpl,
    store: { read: (s) => mem.get(s) ?? null, write: (s, u) => void mem.set(s, u) },
    flag: { get: (s) => flags.has(s), set: (s, on) => void (on ? flags.add(s) : flags.delete(s)) },
  });
  return { sync, mem, flags, calls };
}

describe("photo sync", () => {
  it("a new device receives the photo from the server", async () => {
    const r = rig({ photo: URL_A });
    expect(await r.sync.pull("u1")).toBe(true);
    expect(r.mem.get("u1")).toBe(URL_A);
    expect(r.flags.has("u1")).toBe(true);
  });

  it("the server's newer photo replaces this browser's", async () => {
    const r = rig({ photo: URL_B }, { local: URL_A, synced: true });
    expect(await r.sync.pull("u1")).toBe(true);
    expect(r.mem.get("u1")).toBe(URL_B);
  });

  it("a photo from before sync existed is uploaded once, not shown as changed", async () => {
    const server = { photo: null as string | null };
    const r = rig(server, { local: URL_A });
    expect(await r.sync.pull("u1")).toBe(false);
    expect(server.photo).toBe(URL_A);
    expect(r.flags.has("u1")).toBe(true);
  });

  it("a photo removed on another device is dropped here too", async () => {
    const r = rig({ photo: null }, { local: URL_A, synced: true });
    expect(await r.sync.pull("u1")).toBe(true);
    expect(r.mem.get("u1")).toBeNull();
  });

  it("an unreachable server changes nothing and does not throw", async () => {
    const r = rig({ photo: URL_B }, { local: URL_A, synced: true, offline: true });
    expect(await r.sync.pull("u1")).toBe(false);
    expect(r.mem.get("u1")).toBe(URL_A);
    expect(await r.sync.push("u1")).toBe("offline");
  });

  it("drop clears the server copy", async () => {
    const server = { photo: URL_A as string | null };
    const r = rig(server, { local: URL_A, synced: true });
    expect(await r.sync.drop("u1")).toBe("ok");
    expect(server.photo).toBeNull();
  });

  it("refuses to upload something that is not a jpeg, png or webp data url", () => {
    expect(dataUrlToBytes("data:image/svg+xml;base64,AAAA")).toBeNull();
    expect(dataUrlToBytes("https://x.test/a.png")).toBeNull();
  });

  it("a 404 that is not the server's own 'no photo' (an old server, a proxy page) never deletes the photo here", async () => {
    const r = rig({ photo: null }, { local: URL_A, synced: true });
    const plain = photoSync({
      token: async () => "tok", origin: "https://mcp.example",
      fetchImpl: (async () => new Response("Not Found", { status: 404, headers: { "content-type": "text/plain" } })) as unknown as typeof fetch,
      store: { read: (s) => r.mem.get(s) ?? null, write: (s, u) => void r.mem.set(s, u) },
      flag: { get: () => true, set: () => {} },
    });
    expect(await plain.pull("u1")).toBe(false);
    expect(r.mem.get("u1")).toBe(URL_A);
  });
});
