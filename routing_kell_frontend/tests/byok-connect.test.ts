// @vitest-environment node
// The two extra ways to add a key: "Connect with OpenRouter" (OAuth with PKCE) and importing a pasted .env block.
import { describe, expect, it, vi } from "vitest";
import { parseEnvBlock } from "@/lib/byok/envimport";
import { OPENROUTER_EXCHANGE_URL, beginConnect, challengeFor, finishConnect, type PkceStore } from "@/lib/byok/openrouter";

const store = (): PkceStore & { raw: Map<string, string> } => {
  const raw = new Map<string, string>();
  return { raw, getItem: (k) => raw.get(k) ?? null, setItem: (k, v) => void raw.set(k, v), removeItem: (k) => void raw.delete(k) };
};

describe("OpenRouter connect (PKCE)", () => {
  it("the S256 challenge matches the RFC 7636 test vector", async () => {
    expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("begins with S256, a callback address, a state, and a challenge that matches the stored verifier", async () => {
    const s = store();
    const url = new URL(await beginConnect("https://www.example.com/account/models", s));
    expect(url.origin + url.pathname).toBe("https://openrouter.ai/auth");
    expect(url.searchParams.get("callback_url")).toBe("https://www.example.com/account/models");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    const saved = JSON.parse([...s.raw.values()][0]);
    expect(saved.verifier.length).toBeGreaterThanOrEqual(43);
    expect(saved.verifier.length).toBeLessThanOrEqual(128);
    expect(url.searchParams.get("code_challenge")).toBe(await challengeFor(saved.verifier));
    expect(url.searchParams.get("state")).toBe(saved.state);
    expect(url.toString()).not.toContain(saved.verifier); // the verifier never leaves the browser until the exchange
  });

  it("exchanges the code with the verifier and returns the key; the attempt is used up", async () => {
    const s = store();
    const url = new URL(await beginConnect("https://x.example/cb", s));
    const state = url.searchParams.get("state")!;
    const f = vi.fn(async () => new Response(JSON.stringify({ key: "sk-or-v1-NEWKEY", user_id: "u" }), { status: 200 }));
    const r = await finishConnect("the-code", state, s, f as unknown as typeof fetch);
    expect(r).toEqual({ ok: true, key: "sk-or-v1-NEWKEY" });
    const [u, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(u).toBe(OPENROUTER_EXCHANGE_URL);
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({ code: "the-code", code_challenge_method: "S256" });
    expect(body.code_verifier.length).toBeGreaterThanOrEqual(43);
    expect(init.credentials).toBe("omit");
    expect([...s.raw.keys()]).toEqual([]);
    expect((await finishConnect("the-code", state, s, f as unknown as typeof fetch)).ok).toBe(false); // replay: nothing stored any more
  });

  it("refuses a wrong or missing state without calling OpenRouter", async () => {
    const s = store();
    await beginConnect("https://x.example/cb", s);
    const f = vi.fn();
    const r = await finishConnect("c", "not-the-state", s, f as unknown as typeof fetch);
    expect(r.ok).toBe(false);
    expect(f).not.toHaveBeenCalled();
    expect((await finishConnect("c", "", store(), f as unknown as typeof fetch)).ok).toBe(false);
  });

  it("refuses an attempt older than ten minutes", async () => {
    const s = store();
    const t0 = 1_000_000;
    const state = new URL(await beginConnect("https://x.example/cb", s, t0)).searchParams.get("state")!;
    const f = vi.fn();
    const r = await finishConnect("c", state, s, f as unknown as typeof fetch, t0 + 10 * 60 * 1000 + 1);
    expect(r).toMatchObject({ ok: false });
    expect(f).not.toHaveBeenCalled();
  });

  it("explains a rejected exchange and a network failure, and never throws", async () => {
    const s1 = store();
    const st1 = new URL(await beginConnect("https://x.example/cb", s1)).searchParams.get("state")!;
    const rejected = await finishConnect("c", st1, s1, (async () => new Response("no", { status: 400 })) as unknown as typeof fetch);
    expect(rejected).toMatchObject({ ok: false });
    expect((rejected as { message: string }).message).toContain("400");
    const s2 = store();
    const st2 = new URL(await beginConnect("https://x.example/cb", s2)).searchParams.get("state")!;
    const down = await finishConnect("c", st2, s2, (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch);
    expect((down as { message: string }).message).toMatch(/Paste a key instead/);
  });
});

describe("pasted .env import", () => {
  it("reads the standard provider variables, with export, quotes, comments and CRLF", () => {
    const r = parseEnvBlock([
      "# my keys", "export ANTHROPIC_API_KEY='sk-ant-AAAA1111'", 'OPENAI_API_KEY="sk-oai-BBBB2222" # work', "OPENROUTER_API_KEY = sk-or-CCCC3333", "GROQ_API_KEY=gsk_DDDD4444  # trailing", "TOGETHER_API_KEY=tg-EEEE5555", "",
    ].join("\r\n"));
    expect(r.found.map((f) => [f.varName, f.provider, f.apiKey])).toEqual([
      ["ANTHROPIC_API_KEY", "anthropic", "sk-ant-AAAA1111"],
      ["OPENAI_API_KEY", "openai", "sk-oai-BBBB2222"],
      ["OPENROUTER_API_KEY", "openrouter", "sk-or-CCCC3333"],
      ["GROQ_API_KEY", "groq", "gsk_DDDD4444"],
      ["TOGETHER_API_KEY", "together", "tg-EEEE5555"],
    ]);
  });

  it("ignores everything else, including other secrets, and returns none of it", () => {
    const r = parseEnvBlock("DATABASE_URL=postgres://u:pw@h/db\nSTRIPE_SECRET=sk_live_zzz\nnot a line\nOPENAI_API_KEY=sk-ok-12345678");
    expect(r.found).toHaveLength(1);
    expect(r.ignored).toBe(3);
    expect(JSON.stringify(r)).not.toContain("postgres");
    expect(JSON.stringify(r)).not.toContain("sk_live_zzz");
  });

  it("skips empty values, ${REFERENCES} and repeats", () => {
    const r = parseEnvBlock("OPENAI_API_KEY=\nANTHROPIC_API_KEY=${SOMETHING}\nGROQ_API_KEY=gsk_one\nGROQ_API_KEY=gsk_two");
    expect(r.found.map((f) => f.apiKey)).toEqual(["gsk_one"]);
  });
});
