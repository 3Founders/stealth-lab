import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/session", () => ({ getAccessToken: vi.fn(async () => null) }));

const ORIGINAL_ENV = process.env.NEXT_PUBLIC_KEL_API_URL;

async function freshApi() {
  vi.resetModules();
  const mod = await import("@/lib/api");
  return mod;
}

/** Re-imports the (freshly re-mocked, post-resetModules) session module and
 * configures getAccessToken's next resolution. Must run AFTER freshApi(). */
async function mockToken(token: string | null) {
  const { getAccessToken } = await import("@/lib/session");
  vi.mocked(getAccessToken).mockResolvedValue(token);
}

describe("apiGet — unconfigured backend", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ""; });
  afterEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ORIGINAL_ENV; });

  it("returns unconfigured without ever calling fetch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { apiGet } = await freshApi();
    const result = await apiGet("/v1/problems");
    expect(result).toEqual({ kind: "unconfigured" });
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

describe("apiGet — configured backend", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = "http://backend.test"; });
  afterEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ORIGINAL_ENV; vi.restoreAllMocks(); });

  it("401 becomes an explicit unauthenticated state, not a generic error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const { apiGet } = await freshApi();
    const result = await apiGet("/v1/economy/contributors/x/credits");
    expect(result).toEqual({ kind: "unauthenticated" });
  });

  it("403 becomes an explicit forbidden state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 403 })));
    const { apiGet } = await freshApi();
    const result = await apiGet("/v1/economy/contributors/x/credits");
    expect(result.kind).toBe("forbidden");
  });

  it("ok response carries the real parsed body, not a fabricated one", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ hello: "world" }), { status: 200 })));
    const { apiGet } = await freshApi();
    const result = await apiGet<{ hello: string }>("/v1/problems");
    expect(result).toEqual({ kind: "ok", data: { hello: "world" } });
  });

  it("attaches the session's bearer token when present", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { apiGet } = await freshApi();
    await mockToken("real-token");
    await apiGet("/v1/me");
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer real-token");
  });

  it("sends no Authorization header when signed out", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { apiGet } = await freshApi();
    await mockToken(null);
    await apiGet("/v1/problems");
    const [, init] = fetchMock.mock.calls[0];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});

describe("apiPost — never sends an authenticated write without a session", () => {
  beforeEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = "http://backend.test"; });
  afterEach(() => { process.env.NEXT_PUBLIC_KEL_API_URL = ORIGINAL_ENV; vi.restoreAllMocks(); });

  it("unauthenticated caller gets an explicit state and the request never goes out", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { apiPost } = await freshApi();
    await mockToken(null);
    const result = await apiPost("/v1/economy/procedure-submissions", { name: "x" });
    expect(result).toEqual({ kind: "unauthenticated" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("429 is surfaced as a distinct, human-readable rate-limit state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "too fast" }), { status: 429 })));
    const { apiPost } = await freshApi();
    await mockToken("t");
    const result = await apiPost("/v1/economy/procedure-submissions", { name: "x" });
    expect(result.kind).toBe("error");
    if (result.kind === "error") {
      expect(result.status).toBe(429);
      expect(result.message).toMatch(/too quickly/i);
    }
  });

  it("422 surfaces the backend's own validation detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "goal (problem) x not found" }), { status: 422 })));
    const { apiPost } = await freshApi();
    await mockToken("t");
    const result = await apiPost("/v1/economy/procedure-submissions", { name: "x" });
    expect(result.kind).toBe("error");
    if (result.kind === "error") expect(result.message).toBe("goal (problem) x not found");
  });

  it("a successful submission returns the real server response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "sub-1", status: "candidate" }), { status: 200 })));
    const { apiPost } = await freshApi();
    await mockToken("t");
    const result = await apiPost<{ id: string; status: string }>("/v1/economy/procedure-submissions", { name: "x" });
    expect(result).toEqual({ kind: "ok", data: { id: "sub-1", status: "candidate" } });
  });
});
