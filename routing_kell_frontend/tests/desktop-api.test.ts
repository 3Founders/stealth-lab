// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { DesktopApiError, desktopApi, isActive, type DesktopAsk } from "@/lib/chat/desktop-api";

const reply = (status: number, body: unknown) => Promise.resolve({ ok: status < 300, status, json: () => Promise.resolve(body) } as Response);

describe("desktop api", () => {
  it("asks a session with the session token, to the hosted origin only, with no cookies", async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(201, { id: "a1", status: "queued" }));
    const api = desktopApi({ token: async () => "tok", origin: "https://mcp.example/", fetchImpl: f as unknown as typeof fetch });
    await api.ask({ device_id: "desk-1", session_id: "ses_1" }, "run the tests");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("https://mcp.example/v1/desktop/asks");
    expect(init!.method).toBe("POST");
    expect(JSON.parse(String(init!.body))).toEqual({ device_id: "desk-1", session_id: "ses_1", text: "run the tests" });
    expect((init!.headers as Record<string, string>).authorization).toBe("Bearer tok");
    expect(init!.credentials).toBe("omit");
  });

  it("encodes ids and grantees, and turns a refusal into its message", async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(409, { error: "anuj's desktop is offline" }));
    const api = desktopApi({ token: async () => "t", origin: "https://m", fetchImpl: f as unknown as typeof fetch });
    await expect(api.permission("a/b", "once")).rejects.toThrow("offline");
    expect(f.mock.calls[0][0]).toBe("https://m/v1/desktop/asks/a%2Fb/permission");
    await expect(api.revoke("sub x")).rejects.toBeInstanceOf(DesktopApiError);
    expect(f.mock.calls[1][0]).toBe("https://m/v1/desktop/grants?grantee=sub%20x");
    await expect(desktopApi({ token: async () => null }).overview()).rejects.toMatchObject({ status: 401 });
  });

  it("an ask is active until it finishes", () => {
    const a = (status: DesktopAsk["status"]) => ({ status } as DesktopAsk);
    expect(["needs_approval", "queued", "running", "waiting_permission"].every((s) => isActive(a(s as DesktopAsk["status"])))).toBe(true);
    expect(["done", "failed", "denied", "cancelled"].some((s) => isActive(a(s as DesktopAsk["status"])))).toBe(false);
  });
});
