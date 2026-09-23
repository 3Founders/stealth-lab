import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetSession = vi.fn();
const mockSignOut = vi.fn();

vi.mock("@/lib/supabase", () => ({
  getSupabase: () => ({
    auth: { getSession: mockGetSession, signOut: mockSignOut, onAuthStateChange: vi.fn() },
  }),
  supabaseConfigured: () => true,
}));

import {
  consumePostAuthRedirect, getAccessToken, getSession, isSafeRedirectPath, setPostAuthRedirect, signOut,
} from "@/lib/session";

describe("getAccessToken / getSession — wrap Supabase's own session, never fabricate one", () => {
  beforeEach(() => mockGetSession.mockReset());

  it("is null when there is no Supabase session", async () => {
    mockGetSession.mockResolvedValue({ data: { session: null } });
    expect(await getAccessToken()).toBeNull();
    expect(await getSession()).toBeNull();
  });

  it("returns the real token + subject from a live Supabase session", async () => {
    mockGetSession.mockResolvedValue({ data: { session: { access_token: "tok", user: { id: "user-1" } } } });
    expect(await getAccessToken()).toBe("tok");
    expect(await getSession()).toEqual({ token: "tok", subject: "user-1" });
  });

  it("is null when the session is missing a user id (malformed, not trusted)", async () => {
    mockGetSession.mockResolvedValue({ data: { session: { access_token: "tok", user: null } } });
    expect(await getSession()).toBeNull();
  });
});

describe("signOut", () => {
  it("calls through to Supabase's own signOut, no local state to clear separately", async () => {
    mockSignOut.mockResolvedValue({});
    await signOut();
    expect(mockSignOut).toHaveBeenCalled();
  });
});

describe("isSafeRedirectPath — open-redirect guard, only same-origin paths", () => {
  it("accepts a same-origin path", () => {
    expect(isSafeRedirectPath("/goals/123")).toBe(true);
  });

  it("rejects an absolute URL", () => {
    expect(isSafeRedirectPath("https://evil.example/phish")).toBe(false);
  });

  it("rejects a protocol-relative URL", () => {
    expect(isSafeRedirectPath("//evil.example")).toBe(false);
  });

  it("rejects anything that isn't a bare path", () => {
    expect(isSafeRedirectPath("javascript:alert(1)")).toBe(false);
    expect(isSafeRedirectPath(null)).toBe(false);
    expect(isSafeRedirectPath(undefined)).toBe(false);
    expect(isSafeRedirectPath("")).toBe(false);
  });
});

describe("post-auth redirect round-trip", () => {
  beforeEach(() => { window.sessionStorage.clear(); });

  it("stores and consumes a safe path", () => {
    setPostAuthRedirect("/goals/123/contribute/way");
    expect(consumePostAuthRedirect()).toBe("/goals/123/contribute/way");
  });

  it("consuming clears it — a stale redirect never applies twice", () => {
    setPostAuthRedirect("/goals/123");
    consumePostAuthRedirect();
    expect(consumePostAuthRedirect()).toBe("/");
  });

  it("defaults to / when nothing was stored", () => {
    expect(consumePostAuthRedirect()).toBe("/");
  });

  it("never stores an unsafe path", () => {
    setPostAuthRedirect("https://evil.example");
    expect(consumePostAuthRedirect()).toBe("/");
  });
});
