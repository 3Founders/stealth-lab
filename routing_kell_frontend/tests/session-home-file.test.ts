// @vitest-environment node
// The head script that sends a signed-in visitor from the home page to /chat (public/session-home.js): it runs before the first
// paint, so it must parse on its own and read Supabase's own localStorage entry, while leaving signed-out visitors and other pages alone.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const file = fs.readFileSync(path.join(process.cwd(), "public", "session-home.js"), "utf8").trim();

const run = (pathname: string, store: Record<string, string>) => {
  let replaced: string | null = null;
  const localStorage = {
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    getItem: (k: string) => store[k] ?? null,
  };
  new Function("location", "localStorage", "JSON", "Array", file)(
    { pathname, replace: (p: string) => { replaced = p; } }, localStorage, JSON, Array,
  );
  return replaced;
};

describe("session-home.js", () => {
  it("parses, and sends a signed-in visitor on / to /chat", () => {
    expect(() => new Function(file)).not.toThrow();
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) })).toBe("/chat");
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ currentSession: { access_token: "t" } }) })).toBe("/chat");
  });
  it("leaves a signed-out visitor, another page, and a verifier key alone", () => {
    expect(run("/", {})).toBeNull();
    expect(run("/pricing", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) })).toBeNull();
    expect(run("/", { "sb-demo-auth-token-code-verifier": JSON.stringify({ access_token: "t" }) })).toBeNull();
    expect(run("/", { "sb-demo-auth-token": "null" })).toBeNull();
  });
});
