// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { MemoryApiError, knowledgePath, memoryApi, notePath, titleOf } from "@/lib/chat/memory-api";

const reply = (status: number, body: unknown) => Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) } as Response);

describe("memory api", () => {
  it("sends the session token as a Bearer, to the hosted origin only, with no cookies", async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { settings: { enabled: false, share_agents: true, last_dream_at: null }, files: [], dreams: [] }));
    const api = memoryApi({ token: async () => "tok", origin: "https://mcp.example", fetchImpl: f as unknown as typeof fetch });
    await api.overview();
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://mcp.example/v1/memory");
    expect((init!.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(init!.credentials).toBe("omit");
  });

  it("refuses to call without a session, and turns a server refusal into its message", async () => {
    await expect(memoryApi({ token: async () => null }).overview()).rejects.toMatchObject({ status: 401 });
    const f = vi.fn(() => reply(409, { error: "that file changed since you read it" }));
    const api = memoryApi({ token: async () => "t", fetchImpl: f as unknown as typeof fetch });
    await expect(api.write("notes/a.md", "x", 1)).rejects.toThrow("changed since you read it");
    await expect(api.write("notes/a.md", "x", 1)).rejects.toBeInstanceOf(MemoryApiError);
  });

  it("names note and knowledge files safely", () => {
    expect(notePath("Plan: Q4 / launch?")).toBe("notes/Plan Q4  launch.md");
    expect(notePath("")).toBe("notes/untitled.md");
    expect(notePath("../../etc")).toBe("notes/etc.md");
    expect(knowledgePath("Guide v2.TXT")).toBe("knowledge/Guide v2.md");
    expect(titleOf("notes/Plan.md")).toBe("Plan");
  });

  it("writes with the version it read, and dreams only when asked to force", async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { ran: false }));
    const api = memoryApi({ token: async () => "t", fetchImpl: f as unknown as typeof fetch });
    await api.write("MEMORY.md", "- fact", 3);
    expect(JSON.parse(f.mock.calls[0][1]!.body as string)).toEqual({ path: "MEMORY.md", body: "- fact", expected_version: 3 });
    await api.dream();
    expect(JSON.parse(f.mock.calls[1][1]!.body as string)).toEqual({ force: false });
  });
});
