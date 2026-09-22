import { beforeEach, describe, expect, it } from "vitest";
import { clearAccessToken, decodeJwtSubject, getAccessToken, getSession, setAccessToken } from "@/lib/session";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64url = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url(JSON.stringify({ alg: "none" }))}.${b64url(JSON.stringify(payload))}.sig`;
}

describe("session token storage", () => {
  beforeEach(() => clearAccessToken());

  it("returns null when nothing is stored — never fabricates a session", () => {
    expect(getAccessToken()).toBeNull();
    expect(getSession()).toBeNull();
  });

  it("round-trips a stored token", () => {
    setAccessToken("abc.def.ghi");
    expect(getAccessToken()).toBe("abc.def.ghi");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
  });
});

describe("decodeJwtSubject — display/URL-building only, never authoritative", () => {
  it("extracts the sub claim from a real-shaped JWT", () => {
    const token = fakeJwt({ sub: "user-123", exp: 9999999999 });
    expect(decodeJwtSubject(token)).toBe("user-123");
  });

  it("returns null for garbage input rather than throwing", () => {
    expect(decodeJwtSubject("not-a-jwt")).toBeNull();
    expect(decodeJwtSubject("")).toBeNull();
    expect(decodeJwtSubject("a.b")).toBeNull();
  });

  it("returns null when the payload has no sub claim", () => {
    const token = fakeJwt({ exp: 123 });
    expect(decodeJwtSubject(token)).toBeNull();
  });
});

describe("getSession", () => {
  beforeEach(() => clearAccessToken());

  it("is null when no token is stored", () => {
    expect(getSession()).toBeNull();
  });

  it("is null when a token is stored but doesn't parse as a JWT with a subject", () => {
    setAccessToken("garbage");
    expect(getSession()).toBeNull();
  });

  it("returns {token, subject} for a real token", () => {
    const token = fakeJwt({ sub: "sub-alice" });
    setAccessToken(token);
    expect(getSession()).toEqual({ token, subject: "sub-alice" });
  });
});
