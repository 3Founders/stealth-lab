import { describe, expect, it } from "vitest";
// @ts-expect-error -- next.config.mjs is plain JavaScript with no declaration file
import config, { SENSITIVE_PAGE_HEADERS, SENSITIVE_PAGE_SOURCES } from "../next.config.mjs";
import { isLocalRedirect, safeRedirect } from "@/lib/oauth-consent";

describe("sign-in and consent pages cannot be framed (MCP spec: clickjacking)", () => {
  it("sends frame-ancestors none and X-Frame-Options DENY for the consent page", async () => {
    const rules = await config.headers!();
    const consent = rules.find((r: { source: string }) => r.source === "/oauth/consent");
    const keys = Object.fromEntries(consent!.headers.map((h: { key: string; value: string }) => [h.key, h.value]));
    expect(keys["Content-Security-Policy"]).toBe("frame-ancestors 'none'");
    expect(keys["X-Frame-Options"]).toBe("DENY");
    expect(keys["Referrer-Policy"]).toBe("no-referrer");          // authorization_id travels in the URL
    expect(keys["Cache-Control"]).toBe("no-store");
  });

  it("covers sign-in and the account area too, and keeps the installer headers", async () => {
    expect(SENSITIVE_PAGE_SOURCES).toEqual(expect.arrayContaining(["/oauth/consent", "/sign-in", "/account/:path*"]));
    expect(SENSITIVE_PAGE_HEADERS.length).toBeGreaterThanOrEqual(4);
    const rules = await config.headers!();
    expect(rules.some((r: { source: string }) => r.source === "/install.sh")).toBe(true);
  });
});

describe("localhost return addresses are called out", () => {
  it.each([
    "http://localhost:8787/callback", "http://127.0.0.1:33418/cb", "http://[::1]:9000/cb", "http://app.localhost/cb",
  ])("%s is local", (uri) => expect(isLocalRedirect(uri)).toBe(true));

  it.each([
    "https://www.cursor.com/agents/mcp/oauth/callback", "https://claude.ai/api/mcp/auth_callback",
    "cursor://anysphere.cursor-retrieval/oauth/callback", "", null, undefined, "not a url",
  ])("%s is not", (uri) => expect(isLocalRedirect(uri as string)).toBe(false));
});

describe("redirects that would run script are never followed", () => {
  it.each(["javascript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "blob:https://a/b", "file:///etc/passwd"])(
    "%s is refused", (url) => expect(safeRedirect(url)).toBeNull());

  it("allows an https callback and an app's own scheme", () => {
    expect(safeRedirect("https://www.cursor.com/agents/mcp/oauth/callback?code=1")).not.toBeNull();
    expect(safeRedirect("cursor://anysphere.cursor-retrieval/oauth/callback?code=1")).not.toBeNull();
  });
});
