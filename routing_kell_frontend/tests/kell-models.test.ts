// @vitest-environment node
// keळ-hosted models: an opt-in choice, never a fallback; the request carries the session token and the data-class header.
import { describe, expect, it, vi } from "vitest";
import { ProviderError, listModels, streamChat } from "@/lib/byok/chat";
import type { KeyValue } from "@/lib/byok/spend";
import type { KeyMeta } from "@/lib/byok/vault";
import { makeResolver } from "@/lib/chat/adapters/resolve";
import { KELL_MODEL_IDS, kellBase, kellModelsAvailable, kellTarget, onlyKellModels, readChoice, writeChoice } from "@/lib/kell-models";

const mem = (): KeyValue => { const m = new Map<string, string>(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) }; };
const sse = (events: string[]) => new Response(new ReadableStream({ start(c) { for (const e of events) c.enqueue(new TextEncoder().encode(e)); c.close(); } }), { status: 200 });
const collect = async (g: AsyncIterable<string>) => { let t = ""; for await (const p of g) t += p; return t; };
const TOKEN = "eyJhbGciOi.SESSIONTOKEN.sig";

describe("addresses", () => {
  it("uses the hosted server's origin, whether or not the configured address ends in /mcp", () => {
    expect(kellBase("https://mcp.example.org")).toBe("https://mcp.example.org/v1");
    expect(kellBase("https://mcp.example.org/mcp")).toBe("https://mcp.example.org/v1");
    expect(kellBase("http://127.0.0.1:8765")).toBe("http://127.0.0.1:8765/v1");
    expect(kellBase("http://mcp.example.org")).toBeNull();
    expect(kellBase("")).toBeNull();
  });
  it("is offered only when a server address is configured, and can be switched off", () => {
    expect(kellModelsAvailable("", undefined)).toBe(false);
    expect(kellModelsAvailable("https://mcp.example.org", undefined)).toBe(true);
    expect(kellModelsAvailable("https://mcp.example.org", "off")).toBe(false);
  });
  it("only the two pilot models are offered", () => {
    expect(onlyKellModels(["x", "glm-5.3-flash", "gemma-4-31b-it", "other"])).toEqual(["glm-5.3-flash", "gemma-4-31b-it"]);
    expect([...KELL_MODEL_IDS]).toEqual(["glm-5.3-flash", "gemma-4-31b-it"]);
  });
});

describe("the request", () => {
  const target = kellTarget(TOKEN, "https://mcp.example.org")!;
  it("sends the session token as a Bearer and the public-data header, and no organisation header", async () => {
    const f = vi.fn(async () => sse(['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', "data: [DONE]\n\n"]));
    expect(await collect(streamChat(target, "glm-5.3-flash", [{ role: "user", content: "yo" }], { fetchImpl: f as unknown as typeof fetch }))).toBe("hi");
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe("https://mcp.example.org/v1/chat/completions");
    expect(init.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(init.headers["x-stealthlab-data-class"]).toBe("PUBLIC_SOURCE");
    expect(Object.keys(init.headers).some((h) => h.toLowerCase() === "x-stealthlab-org")).toBe(false);
  });
  it("a cap hit or policy refusal shows the server's sentence and status, never the token", async () => {
    const body = JSON.stringify({ error: { message: `Daily budget reached for ${TOKEN}`, type: "policy_denied", code: "policy_denied" } });
    const err = (await listModels(target, (async () => new Response(body, { status: 403 })) as unknown as typeof fetch).catch((e) => e)) as Error;
    expect(err).toBeInstanceOf(ProviderError);
    expect(err.message).toContain("403");
    expect(err.message).toContain("Daily budget reached");
    expect(err.message).not.toContain("SESSIONTOKEN");
  });
  it("lists models at /v1/models", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ data: [{ id: "glm-5.3-flash" }, { id: "x" }] }), { status: 200 }));
    expect(await listModels(target, f as unknown as typeof fetch)).toEqual(["glm-5.3-flash", "x"]);
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe("https://mcp.example.org/v1/models");
  });
  it("no token, no target", () => {
    expect(kellTarget("", "https://mcp.example.org")).toBeNull();
    expect(kellTarget(TOKEN, "")).toBeNull();
  });
});

describe("the choice", () => {
  it("defaults to the person's own key, and only a valid kell choice is honoured", () => {
    const kv = mem();
    expect(readChoice(kv, "u")).toEqual({ source: "key" });
    writeChoice(kv, "u", { source: "kell", model: "glm-5.3-flash" });
    expect(readChoice(kv, "u")).toEqual({ source: "kell", model: "glm-5.3-flash" });
    kv.setItem("kel.assistant.source.v", JSON.stringify({ source: "kell", model: "some-other-model" }));
    expect(readChoice(kv, "v")).toEqual({ source: "key" });
    expect(readChoice(kv, "other-account")).toEqual({ source: "key" });
  });
});

describe("what backs the assistant (no fallback between the two)", () => {
  const meta: KeyMeta = { id: "k1", provider: "openai", baseUrl: "https://api.openai.com/v1", label: "mine", last4: "wxyz", model: "m-own", createdAt: "" };
  const vault = (list: KeyMeta[], key: string | null = "sk-own-KEY1234567890") => ({ list: async () => list, reveal: async () => key });

  it("own key (the default): uses the vault key and no token is read", async () => {
    const token = vi.fn(async () => TOKEN);
    const r = await makeResolver({ vault: vault([meta]), token, kv: mem(), subject: "u", origin: "https://mcp.example.org" })();
    expect(r?.secrets).toEqual(["sk-own-KEY1234567890"]);
    expect(token).not.toHaveBeenCalled();
  });
  it("own key chosen but none saved: null, so the room says 'Add a key to chat here'. It does NOT fall back to keळ models", async () => {
    const kv = mem();
    const token = vi.fn(async () => TOKEN);
    expect(await makeResolver({ vault: vault([]), token, kv, subject: "u", origin: "https://mcp.example.org" })()).toBeNull();
    expect(token).not.toHaveBeenCalled();
  });
  it("keळ models chosen: uses the session token and ignores the person's keys", async () => {
    const kv = mem();
    writeChoice(kv, "u", { source: "kell", model: "gemma-4-31b-it" });
    const list = vi.fn(async () => [meta]);
    const r = await makeResolver({ vault: { list, reveal: async () => "sk-own-KEY1234567890" }, token: async () => TOKEN, kv, subject: "u", origin: "https://mcp.example.org" })();
    expect(r?.secrets).toEqual([TOKEN]);
    expect(list).not.toHaveBeenCalled();
  });
  it("keळ models chosen but signed out: an error that says so, not a silent switch to a key", async () => {
    const kv = mem();
    writeChoice(kv, "u", { source: "kell", model: "gemma-4-31b-it" });
    await expect(makeResolver({ vault: vault([meta]), token: async () => null, kv, subject: "u", origin: "https://mcp.example.org" })()).rejects.toThrow(/Sign in again/);
  });
  it("a key with no model chosen tells the person to press Test", async () => {
    await expect(makeResolver({ vault: vault([{ ...meta, model: "" }]), token: async () => TOKEN, kv: mem(), subject: "u" })()).rejects.toThrow(/press Test/);
  });
});
