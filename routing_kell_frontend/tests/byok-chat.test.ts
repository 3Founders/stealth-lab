// @vitest-environment node
// Talking to a provider from the browser: the right request for each protocol, streaming, and a key that never leaks into an error.
import { describe, expect, it, vi } from "vitest";
import { ProviderError, listModels, scrub, streamChat } from "@/lib/byok/chat";
import { PROVIDERS, cleanBaseUrl, providerById } from "@/lib/byok/providers";

const KEY = "sk-live-AbCdEf0123456789-SECRET";
const anthropic = { provider: providerById("anthropic")!, baseUrl: "https://api.anthropic.com", apiKey: KEY };
const openai = { provider: providerById("openai")!, baseUrl: "https://api.openai.com/v1", apiKey: KEY };

const sse = (events: string[]) => new Response(new ReadableStream({
  start(c) { for (const e of events) c.enqueue(new TextEncoder().encode(e)); c.close(); },
}), { status: 200 });
const collect = async (g: AsyncGenerator<string>) => { let t = ""; for await (const p of g) t += p; return t; };

describe("requests", () => {
  it("Anthropic: x-api-key, a version, and the direct-browser-access header, to /v1/messages", async () => {
    const f = vi.fn(async () => sse(['data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n']));
    await collect(streamChat(anthropic, "m1", [{ role: "user", content: "yo" }], { system: "be brief", fetchImpl: f as unknown as typeof fetch }));
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe("https://api.anthropic.com/v1/messages");
    expect(init.headers["x-api-key"]).toBe(KEY);
    expect(init.headers["anthropic-dangerous-direct-browser-access"]).toBe("true");
    expect(init.headers["anthropic-version"]).toBeTruthy();
    expect(init.headers.authorization).toBeUndefined();
    expect(init.credentials).toBe("omit");
    expect(JSON.parse(String(init.body))).toMatchObject({ model: "m1", stream: true, system: "be brief", messages: [{ role: "user", content: "yo" }] });
  });
  it("OpenAI-compatible: a bearer token to /chat/completions, with the system text as the first message", async () => {
    const f = vi.fn(async () => sse(['data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', "data: [DONE]\n\n"]));
    await collect(streamChat(openai, "m2", [{ role: "user", content: "yo" }], { system: "be brief", fetchImpl: f as unknown as typeof fetch }));
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }];
    expect(url).toBe("https://api.openai.com/v1/chat/completions");
    expect(init.headers.authorization).toBe(`Bearer ${KEY}`);
    expect(JSON.parse(String(init.body)).messages[0]).toEqual({ role: "system", content: "be brief" });
  });
  it("lists models at the right path for each protocol", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ data: [{ id: "a" }, { id: "b" }] }), { status: 200 }));
    expect(await listModels(anthropic, f as unknown as typeof fetch)).toEqual(["a", "b"]);
    expect(await listModels(openai, f as unknown as typeof fetch)).toEqual(["a", "b"]);
    expect(f.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual(["https://api.anthropic.com/v1/models", "https://api.openai.com/v1/models"]);
  });
});

describe("streaming", () => {
  it("yields text pieces in order across both protocols, ignoring other events", async () => {
    const a = await collect(streamChat(anthropic, "m", [], { fetchImpl: (async () => sse([
      'data: {"type":"message_start"}\n\n', 'data: {"type":"content_block_delta","delta":{"text":"Hel"}}\n\n', 'data: {"type":"content_block_delta","delta":{"text":"lo"}}\n\n'])) as unknown as typeof fetch }));
    expect(a).toBe("Hello");
    const o = await collect(streamChat(openai, "m", [], { fetchImpl: (async () => sse([
      'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n', 'data: {"choices":[{"delta":{"content":"Wor"}}]}\n\n', 'data: {"choices":[{"delta":{"content":"ld"}}]}\n\n', "data: [DONE]\n\n"])) as unknown as typeof fetch }));
    expect(o).toBe("World");
  });
  it("stops when the signal aborts", async () => {
    const ac = new AbortController();
    const f = async (_u: unknown, init?: RequestInit) => {
      if (init?.signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return sse(['data: {"choices":[{"delta":{"content":"x"}}]}\n\n']);
    };
    ac.abort();
    await expect(collect(streamChat(openai, "m", [], { signal: ac.signal, fetchImpl: f as unknown as typeof fetch }))).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("a key never leaks", () => {
  it("scrub removes the key and anything key-shaped", () => {
    expect(scrub(`bad key ${KEY} and Bearer abcdefghijklmnop`, KEY)).not.toContain("SECRET");
    expect(scrub("x sk-proj-abcdefghijk12345 y")).toBe("x [key removed] y");
    expect(scrub("gsk_abcdefghijklmnop1234")).toBe("[key removed]");
    expect(scrub("plain text, nothing to hide")).toBe("plain text, nothing to hide");
  });
  it("a provider error that echoes the key back is shown without it", async () => {
    const f = async () => new Response(`{"error":"Incorrect API key provided: ${KEY}"}`, { status: 401 });
    const err = await collect(streamChat(openai, "m", [], { fetchImpl: f as unknown as typeof fetch })).catch((e) => e as Error);
    expect(err).toBeInstanceOf(ProviderError);
    expect((err as Error).message).toContain("401");
    expect((err as Error).message).not.toContain(KEY);
    expect((err as Error).message).not.toContain("SECRET");
    const err2 = await listModels(openai, f as unknown as typeof fetch).catch((e) => e as Error);
    expect((err2 as Error).message).not.toContain(KEY);
  });
  it("a blocked or offline request says the provider may not allow browser use, with no key in it", async () => {
    const f = async () => { throw new TypeError("Failed to fetch"); };
    const err = (await listModels(openai, f as unknown as typeof fetch).catch((e) => e)) as Error;
    expect(err.message).toMatch(/browser-only mode/);
    expect(err.message).not.toContain(KEY);
  });
});

describe("providers", () => {
  it("offers the named providers and an OpenAI-compatible 'other'", () => {
    expect(PROVIDERS.map((p) => p.id)).toEqual(["anthropic", "openai", "together", "groq", "other"]);
    expect(providerById("other")!.baseUrl).toBe("");
  });
  it("no provider label carries another company's product name as a feature name we mint", () => {
    for (const p of PROVIDERS) expect(p.label).not.toMatch(/claude|codex|gpt/i);
  });
  it("only https (or http on this machine) addresses are accepted, with no credentials, query or fragment", () => {
    expect(cleanBaseUrl("https://api.example.com/v1/")).toBe("https://api.example.com/v1");
    expect(cleanBaseUrl("http://localhost:11434/v1")).toBe("http://localhost:11434/v1");
    expect(cleanBaseUrl("http://api.example.com/v1")).toBeNull();
    expect(cleanBaseUrl("https://u:p@api.example.com")).toBeNull();
    expect(cleanBaseUrl("https://api.example.com/v1?key=1")).toBeNull();
    expect(cleanBaseUrl("not a url")).toBeNull();
  });
});
