// The page's client against the REAL helper (packaging/npm/lib/console.mjs) and a fake provider on loopback.
// This is the evidence for the exit criteria about keys and streaming; it is not a mock of the contract.
import { afterAll, beforeAll, describe, expect, it } from "vitest";
// @ts-expect-error plain .mjs harness
import { BAD_KEY, GOOD_KEY, startDevHelper, startFakeProvider } from "../dev/harness.mjs";
import { HelperClient } from "@/lib/helper";
import { formatCost } from "@/lib/format";
import type { ChatEvent } from "@/lib/types";
import { HelperError } from "@/lib/words";

let provider: Awaited<ReturnType<typeof startFakeProvider>>;
let h: Awaited<ReturnType<typeof startDevHelper>>;
const urls: string[] = [];
const bodies: string[] = [];
let client: HelperClient;

beforeAll(async () => {
  provider = await startFakeProvider({ gapMs: 50 });
  h = await startDevHelper({ provider });
  // Every request the page's client makes is recorded, so "no key in any request URL" is checked on the real traffic.
  const recording: typeof fetch = (input, init) => { urls.push(String(input)); bodies.push(String(init?.body ?? "")); return fetch(input, init); };
  client = new HelperClient({ token: h.token, base: h.base, cleanSearch: "" }, recording);
});
afterAll(async () => { await h.close(); await provider.close(); });

describe("access", () => {
  it("answers /health without a token, and refuses everything else without one", async () => {
    expect(await new HelperClient({ token: null, base: h.base, cleanSearch: "" }).health()).toBe(true);
    const err = await new HelperClient({ token: null, base: h.base, cleanSearch: "" }).clients().catch((e) => e);
    expect(err).toBeInstanceOf(HelperError);
    expect(err.kind).toBe("unauthorized");
    const wrong = await new HelperClient({ token: "nope", base: h.base, cleanSearch: "" }).models().catch((e) => e);
    expect(wrong.kind).toBe("unauthorized");
  });
  it("tells 'helper is down' from 'wrong token'", async () => {
    const down = new HelperClient({ token: "x", base: "http://127.0.0.1:1", cleanSearch: "" });
    expect(await down.health()).toBe(false);
    const err = await down.clients().catch((e) => e);
    expect(err.kind).toBe("unreachable");
  });
});

describe("install", () => {
  it("lists the three tools, previews with a dry run, installs, and undoes", async () => {
    const list = await client.clients();
    expect(list.map((c) => c.id)).toEqual(["claude-code", "cursor", "codex"]);
    expect(list.find((c) => c.id === "codex")!.hooks).toBe(false);
    expect(list.find((c) => c.id === "cursor")!.hooks).toBe(true);
    const dry = await client.install(["claude-code"], { dry_run: true });
    expect(dry[0]).toMatchObject({ client: "claude-code", ok: true, changed_files: [] });
    expect(dry[0].diff).toMatch(/dry run/);
    const real = await client.install(["cursor"], { cursor_model: "my-model" });
    expect(real[0]).toMatchObject({ client: "cursor", ok: true });
    expect(real[0].changed_files.length).toBe(1);
    const un = await client.uninstall(["cursor"]);
    expect(un[0].ok).toBe(true);
    expect(h.calls.some((c: string[]) => c.includes("--dry-run"))).toBe(true);
  });
  it("refuses an unknown tool and a bad model name in words", async () => {
    const a = await client.install(["vim" as never]).catch((e) => e);
    expect(a).toBeInstanceOf(HelperError);
    expect(a.kind).toBe("refused");
    const b = await client.install(["cursor"], { cursor_model: "bad name!!" }).catch((e) => e);
    expect(b.message).toBe("that is not a model name");
  });
  it("reports readiness as name / ok / detail / fix", async () => {
    const items = await client.readiness();
    expect(items.length).toBeGreaterThan(2);
    for (const i of items) { expect(typeof i.name).toBe("string"); expect(typeof i.ok).toBe("boolean"); expect(typeof i.detail).toBe("string"); }
    expect(items.find((i) => i.name === "server")!.ok).toBe(true);
    expect(items.some((i) => !i.ok && i.fix)).toBe(true); // the key is not set yet: missing, with a fix beside it
  });
});

describe("keys never come back", () => {
  it("a saved key shows only its last four characters, anywhere the page can read", async () => {
    const saved = await client.saveKey("FAKE_API_KEY", GOOD_KEY);
    expect(saved).toEqual({ ok: true, last4: GOOD_KEY.slice(-4) });
    const everything = JSON.stringify([await client.models(), await client.keys(), await client.readiness(), saved]);
    expect(everything).not.toContain(GOOD_KEY);
    expect(everything).not.toContain(GOOD_KEY.slice(0, -4));
    expect((await client.models()).find((m) => m.name === "fake-remote")).toMatchObject({ key_set: true, key_last4: GOOD_KEY.slice(-4) });
    expect(urls.join("\n")).not.toContain(GOOD_KEY);
  });
  it("the key is in the body of exactly one request, and in no address", () => {
    const withKey = bodies.filter((b) => b.includes(GOOD_KEY));
    expect(withKey.length).toBe(1);
    expect(urls.some((u) => u.includes(GOOD_KEY))).toBe(false);
  });
  it("a refused save does not echo the value back", async () => {
    const err = await client.saveKey("lower_case_is_not_a_key_name", GOOD_KEY).catch((e) => e);
    expect(err).toBeInstanceOf(HelperError);
    expect(String(err.message)).not.toContain(GOOD_KEY);
  });
  it("removing a key leaves no key set", async () => {
    await client.removeKey("FAKE_API_KEY");
    expect((await client.models()).find((m) => m.name === "fake-remote")!.key_set).toBe(false);
    await client.saveKey("FAKE_API_KEY", GOOD_KEY);
  });
});

describe("models", () => {
  it("adds, tests and removes a local model with no key", async () => {
    await client.addModel({ name: "my-local", model_id: "llama3.2", openai_base_url: `${provider.base}/v1` });
    const m = (await client.models()).find((x) => x.name === "my-local")!;
    expect(m).toMatchObject({ local: true, key_set: true, input_per_mtok: null });
    const t = await client.testModel("my-local");
    expect(t.ok).toBe(true);
    expect(t.latency_ms).toBeGreaterThanOrEqual(0);
    await client.removeModel("my-local");
    expect((await client.models()).some((x) => x.name === "my-local")).toBe(false);
  });
  it("a refused provider key is explained in words, never with the provider's body", async () => {
    await client.saveKey("FAKE_API_KEY", BAD_KEY);
    const t = await client.testModel("fake-remote");
    expect(t.ok).toBe(false);
    expect(t.error).toMatch(/refused the key/);
    expect(JSON.stringify(t)).not.toContain(BAD_KEY);
    await client.saveKey("FAKE_API_KEY", GOOD_KEY);
  });
  it("rejects a half-priced model", async () => {
    const err = await client.addModel({ name: "half", model_id: "x", openai_base_url: `${provider.base}/v1`, key_env: "HALF_KEY", input_per_mtok: 1 } as never).catch((e) => e);
    expect(err).toBeInstanceOf(HelperError);
  });
});

describe("streaming", () => {
  it("delivers the first token while the model is still talking (measured against the fake provider)", async () => {
    const stamps: Array<{ at: number; e: ChatEvent }> = [];
    const t0 = Date.now();
    await client.chat(["fake-remote"], [{ role: "user", content: "hi" }], (e) => stamps.push({ at: Date.now() - t0, e }), new AbortController().signal);
    const firstDelta = stamps.find((s) => "delta" in s.e)!;
    const done = stamps.find((s) => "done" in s.e)!;
    expect(firstDelta).toBeTruthy();
    // 6 words, 50 ms apart: the whole reply takes about 300 ms. The first word must be on the page well before that.
    expect(firstDelta.at).toBeLessThan(done.at - 150);
    const d = done.e as Extract<ChatEvent, { done: true }>;
    expect(d.ttft_ms).not.toBeNull();
    expect(d.ttft_ms!).toBeLessThan(d.total_ms);
    expect(d.tokens_in).toBe(12);
    expect(formatCost(d.cost_usd)).toMatch(/^\$/);
    console.log(`first token at ${firstDelta.at} ms on the page; finished at ${done.at} ms; helper ttft ${d.ttft_ms} ms (model fake-remote-1, fake provider, 50 ms gaps)`);
  });
  it("streams several models at once, each tagged, and a model with no prices has no cost", async () => {
    const events: ChatEvent[] = [];
    await client.chat(["fake-remote", "fake-local"], [{ role: "user", content: "hi" }], (e) => events.push(e), new AbortController().signal);
    const text = (m: string) => events.filter((e): e is Extract<ChatEvent, { delta: string }> => e.model === m && "delta" in e).map((e) => e.delta).join("");
    expect(text("fake-remote")).toContain("[fake-remote-1]");
    expect(text("fake-local")).toContain("[fake-local-1]");
    const doneLocal = events.find((e) => e.model === "fake-local" && "done" in e) as Extract<ChatEvent, { done: true }>;
    expect(doneLocal.cost_usd).toBeNull();
    expect(formatCost(doneLocal.cost_usd)).toBe("price not set");
    // interleaved, not one after the other
    const order = events.filter((e) => "delta" in e).map((e) => e.model);
    expect(order.indexOf("fake-local")).toBeLessThan(order.lastIndexOf("fake-remote"));
  });
  it("stops every model when the request is closed", async () => {
    const ac = new AbortController();
    const got: ChatEvent[] = [];
    const run = client.chat(["fake-remote", "fake-local"], [{ role: "user", content: "hi" }], (e) => { got.push(e); if (got.length === 2) ac.abort(); }, ac.signal);
    await run;
    await new Promise((r) => setTimeout(r, 400));
    expect(got.some((e) => "done" in e)).toBe(false);
    expect(got.length).toBeLessThan(6);
  });
  it("shows a provider error in words, with no key and no body", async () => {
    await client.saveKey("FAKE_API_KEY", BAD_KEY);
    const events: ChatEvent[] = [];
    await client.chat(["fake-remote"], [{ role: "user", content: "hi" }], (e) => events.push(e), new AbortController().signal);
    const err = events.find((e) => "error" in e) as Extract<ChatEvent, { error: string }>;
    expect(err.error).toMatch(/refused the key/);
    expect(JSON.stringify(events)).not.toContain(BAD_KEY);
    await client.saveKey("FAKE_API_KEY", GOOD_KEY);
  });
});
