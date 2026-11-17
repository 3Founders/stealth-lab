// @vitest-environment node
// The head script that sends a signed-in visitor from the home page to /chat (public/session-home.js): it runs before the first
// paint, so it must parse on its own and read Supabase's own localStorage entry, while leaving signed-out visitors and other pages
// alone. The redirect is once per tab: a later Back to home (or a refresh) must show the landing page.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const file = fs.readFileSync(path.join(process.cwd(), "public", "session-home.js"), "utf8").trim();

const run = (pathname: string, store: Record<string, string>, session: Record<string, string> = {}, search = "") => {
  let replaced: string | null = null;
  const localStorage = {
    get length() { return Object.keys(store).length; },
    key: (i: number) => Object.keys(store)[i] ?? null,
    getItem: (k: string) => store[k] ?? null,
  };
  const sessionStorage = {
    getItem: (k: string) => session[k] ?? null,
    setItem: (k: string, v: string) => { session[k] = v; },
  };
  new Function("location", "localStorage", "sessionStorage", "JSON", "Array", file)(
    { pathname, search, replace: (p: string) => { replaced = p; } }, localStorage, sessionStorage, JSON, Array,
  );
  return { replaced, session };
};

describe("session-home.js", () => {
  it("parses, and sends a signed-in visitor on / to /chat the first time in a tab", () => {
    expect(() => new Function(file)).not.toThrow();
    const first = run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) });
    expect(first.replaced).toBe("/chat");
    expect(first.session["kell:home:redirected"]).toBe("1");
    // A later visit in the same tab (Back to home, or a refresh) is left alone.
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }, first.session).replaced).toBeNull();
    // A new tab starts fresh and redirects once.
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }, {}).replaced).toBe("/chat");
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ currentSession: { access_token: "t" } }) }, {}).replaced).toBe("/chat");
  });
  it("always shows the page for an explicit home link, and marks the tab as having seen it", () => {
    const explicit = run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }, {}, "?home=1");
    expect(explicit.replaced).toBeNull();
    expect(explicit.session["kell:home:redirected"]).toBe("1");
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }, {}, "?utm=x&home=1").replaced).toBeNull();
    expect(run("/", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }, {}, "?home=0").replaced).toBe("/chat");
  });
  it("leaves a signed-out visitor, another page, and a verifier key alone", () => {
    expect(run("/", {}).replaced).toBeNull();
    expect(run("/pricing", { "sb-demo-auth-token": JSON.stringify({ access_token: "t" }) }).replaced).toBeNull();
    expect(run("/", { "sb-demo-auth-token-code-verifier": JSON.stringify({ access_token: "t" }) }).replaced).toBeNull();
    expect(run("/", { "sb-demo-auth-token": "null" }).replaced).toBeNull();
  });
});
