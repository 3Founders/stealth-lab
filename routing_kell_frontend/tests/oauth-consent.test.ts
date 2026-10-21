import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetSession = vi.fn();
const mockDetails = vi.fn();
const mockApprove = vi.fn();
const mockDeny = vi.fn();
const mockListGrants = vi.fn();
const mockRevoke = vi.fn();

vi.mock("@/lib/supabase", () => ({
  getSupabase: () => ({
    auth: {
      getSession: mockGetSession,
      oauth: {
        getAuthorizationDetails: mockDetails, approveAuthorization: mockApprove, denyAuthorization: mockDeny,
        listGrants: mockListGrants, revokeGrant: mockRevoke,
      },
    },
  }),
  supabaseConfigured: () => true,
}));

import { isSafeRedirectPath } from "@/lib/session";
import {
  decide, listConnectedApps, loadConsent, revokeConnectedApp, safeRedirect, signInHref,
} from "@/lib/oauth-consent";

const SIGNED_IN = { data: { session: { access_token: "t", user: { id: "u1" } } } };

describe("loadConsent — what /oauth/consent shows", () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it("asks for an id when opened directly", async () => {
    expect(await loadConsent(null)).toEqual({ kind: "missing-id" });
  });

  it("sends a signed-out user to sign-in and back to this exact request", async () => {
    mockGetSession.mockResolvedValue({ data: { session: null } });
    const s = await loadConsent("auth-1");
    expect(s).toEqual({ kind: "sign-in", href: signInHref("auth-1") });
    const back = new URL(`http://x${signInHref("auth-1")}`).searchParams.get("redirect");
    expect(back).toBe("/oauth/consent?authorization_id=auth-1");
    expect(isSafeRedirectPath(back)).toBe(true);   // the sign-in page will honour it
    expect(mockDetails).not.toHaveBeenCalled();
  });

  it("shows the client, callback and scopes when consent is needed", async () => {
    mockGetSession.mockResolvedValue(SIGNED_IN);
    mockDetails.mockResolvedValue({ data: {
      authorization_id: "auth-1", redirect_uri: "http://localhost:3334/callback",
      client: { id: "c1", name: "Claude", uri: "https://claude.ai", logo_uri: "" },
      user: { id: "u1", email: "a@b.c" }, scope: "openid email",
    }, error: null });
    expect(await loadConsent("auth-1")).toEqual({
      kind: "ask", authorizationId: "auth-1", clientName: "Claude", clientUri: "https://claude.ai", unnamed: false,
      redirectUri: "http://localhost:3334/callback", scopes: ["openid", "email"], email: "a@b.c",
    });
  });

  it("flags an app that registered without a name", async () => {
    mockGetSession.mockResolvedValue(SIGNED_IN);
    for (const client of [{ id: "c2", name: "", uri: "", logo_uri: "" }, { id: "c3", name: "   ", uri: "" }, undefined]) {
      mockDetails.mockResolvedValue({ data: {
        authorization_id: "auth-2", redirect_uri: "http://localhost:8787/callback", client,
        user: { id: "u1", email: "a@b.c" }, scope: "openid",
      }, error: null });
      const s = await loadConsent("auth-2");
      expect(s).toMatchObject({ kind: "ask", clientName: "An app", unnamed: true });
    }
  });

  it("redirects straight back when the user already approved this app", async () => {
    mockGetSession.mockResolvedValue(SIGNED_IN);
    mockDetails.mockResolvedValue({ data: { redirect_url: "https://claude.ai/api/mcp/auth_callback?code=x" }, error: null });
    expect(await loadConsent("auth-1")).toEqual({ kind: "redirect", url: "https://claude.ai/api/mcp/auth_callback?code=x" });
  });

  it("reports an expired or invalid request instead of guessing", async () => {
    mockGetSession.mockResolvedValue(SIGNED_IN);
    mockDetails.mockResolvedValue({ data: null, error: { message: "not found" } });
    expect((await loadConsent("auth-1")).kind).toBe("error");
  });
});

describe("decide — Allow / Deny", () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it("approves or denies without Supabase navigating on its own, and returns the client's redirect", async () => {
    mockApprove.mockResolvedValue({ data: { redirect_url: "cursor://anysphere.cursor-mcp/oauth/callback?code=1" }, error: null });
    mockDeny.mockResolvedValue({ data: { redirect_url: "http://localhost:9/cb?error=access_denied" }, error: null });
    expect(await decide("auth-1", true)).toBe("cursor://anysphere.cursor-mcp/oauth/callback?code=1");
    expect(mockApprove).toHaveBeenCalledWith("auth-1", { skipBrowserRedirect: true });
    expect(await decide("auth-1", false)).toBe("http://localhost:9/cb?error=access_denied");
    expect(mockDeny).toHaveBeenCalledWith("auth-1", { skipBrowserRedirect: true });
  });

  it("never follows a script URL", async () => {
    mockApprove.mockResolvedValue({ data: { redirect_url: "javascript:alert(1)" }, error: null });
    expect(await decide("auth-1", true)).toBeNull();
    expect(safeRedirect("data:text/html,x")).toBeNull();
    expect(safeRedirect("not a url")).toBeNull();
  });
});

describe("connected apps", () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it("lists grants and revokes by client id", async () => {
    mockListGrants.mockResolvedValue({ data: [{ client: { id: "c1", name: "Claude", uri: "", logo_uri: "" },
      scopes: ["email"], granted_at: "2026-09-30T00:00:00Z" }], error: null });
    expect(await listConnectedApps()).toEqual([
      { clientId: "c1", name: "Claude", uri: null, scopes: ["email"], grantedAt: "2026-09-30T00:00:00Z" }]);
    mockRevoke.mockResolvedValue({ data: {}, error: null });
    expect(await revokeConnectedApp("c1")).toBe(true);
    expect(mockRevoke).toHaveBeenCalledWith({ clientId: "c1" });
  });

  it("is null, not empty, when grants can't be read", async () => {
    mockListGrants.mockResolvedValue({ data: null, error: { message: "x" } });
    expect(await listConnectedApps()).toBeNull();
  });
});
