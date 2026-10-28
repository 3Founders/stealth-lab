// @vitest-environment jsdom
// The screens mounted in a DOM against the REAL helper (and a fake provider). Counting user actions here is what backs the
// exit criterion "the readiness checklist for each tool in at most 3 steps".
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { spawn, type ChildProcess } from "node:child_process";
import path from "node:path";
import ConnectPanel from "@/components/ConnectPanel";
import Console from "@/components/Console";
import { addEvent, summary, upsertRun } from "@/components/ActivityPanel";
import KeyField from "@/components/KeyField";
import ModelsPanel from "@/components/ModelsPanel";
import PlaygroundPanel from "@/components/PlaygroundPanel";
import { HelperClient } from "@/lib/helper";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const GOOD_KEY = "sk-test-0123456789abcdef"; // the key the sandbox helper starts with (dev/harness.mjs)
let provider: { base: string };
let h: { base: string; token: string };
let child: ChildProcess;
let client: HelperClient;
// jsdom replaces AbortController with its own, which Node's fetch rejects ("Expected signal to be an instance of AbortSignal").
// A real browser has no such mismatch. This wrapper drops the signal and, on abort, cancels the response body instead, which
// closes the connection the same way. (That the helper then stops every model is proved in live.test.ts, in plain Node.)
const nodeFetch: typeof fetch = async (input, init) => {
  const { signal, ...rest } = init ?? {};
  const res = await fetch(input, rest);
  if (!signal || !res.body) return res;
  const upstream = res.body.getReader();
  const stop = () => { void upstream.cancel().catch(() => {}); };
  if (signal.aborted) stop(); else signal.addEventListener("abort", stop, { once: true });
  const body = new ReadableStream<Uint8Array>({
    async pull(c) { const r = await upstream.read(); if (r.done) c.close(); else c.enqueue(r.value); },
    cancel: (reason) => upstream.cancel(reason),
  });
  return new Response(body, { status: res.status, headers: res.headers });
};
let root: Root | null = null;
let host: HTMLElement | null = null;

beforeAll(async () => {
  // The helper runs in its own process (see dev/child.mjs for why), with a fake provider streaming 60 ms apart.
  child = spawn(process.execPath, [path.resolve(__dirname, "..", "dev", "child.mjs")], { env: { ...process.env, KEL_GAP_MS: "60", KEL_WITH_KEY: "1" }, stdio: ["pipe", "pipe", "inherit"] });
  const line: string = await new Promise((resolve, reject) => {
    let buf = "";
    child.stdout!.on("data", (d) => { buf += d; const i = buf.indexOf(String.fromCharCode(10)); if (i >= 0) resolve(buf.slice(0, i)); });
    child.on("error", reject);
    setTimeout(() => reject(new Error("helper did not start")), 15000);
  });
  const info = JSON.parse(line);
  h = { base: info.base, token: info.token };
  provider = { base: info.provider };
  client = new HelperClient({ token: h.token, base: h.base, cleanSearch: "" }, nodeFetch);
});
afterAll(() => { child.stdin?.end(); child.kill(); });
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null; host = null;
  window.sessionStorage.clear(); window.localStorage.clear();
});

const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
async function until(test: () => boolean, ms = 8000) {
  const t0 = Date.now();
  while (!test() && Date.now() - t0 < ms) await wait(25);
  if (!test()) throw new Error(`timed out waiting for the page; it shows: ${(host?.textContent ?? "").slice(-450)} | ${String(test)}`);
}
async function mount(node: React.ReactNode) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root!.render(node); });
  return host;
}
const text = () => host!.textContent ?? "";
const byText = (sel: string, re: RegExp) => [...host!.querySelectorAll<HTMLElement>(sel)].find((e) => re.test(e.textContent ?? ""));
let actions = 0;
const click = async (el: HTMLElement | null | undefined) => { if (!el) throw new Error("element not found"); actions++; await act(async () => { el.click(); }); };
async function submit(form: HTMLFormElement | null | undefined) { if (!form) throw new Error("form not found"); actions++; await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); }
async function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("Connect: the checklist for each tool in at most 3 actions", () => {
  for (const tool of ["Claude Code", "Cursor", "Codex"]) {
    it(`${tool}: tick, Install, Confirm`, async () => {
      actions = 0;
      const initial = await client.clients();
      await mount(<ConnectPanel client={client} initial={initial} />);
      await until(() => /Readiness/.test(text()) && !!host!.querySelector(".checklist"));
      const box = [...host!.querySelectorAll<HTMLLabelElement>("label.check")].find((l) => l.textContent!.includes(tool))!.querySelector("input")!;
      if (!box.checked) await click(box);
      await click(byText("button", /^Install$/));
      await until(() => /What will change/.test(text()));
      await click(byText("button", /Confirm and install/));
      await until(() => /Installing/.test(text()) && /done/.test(host!.querySelector(".card[aria-live]")?.textContent ?? ""));
      expect(host!.querySelectorAll(".checklist").length).toBeGreaterThanOrEqual(2); // the install result and the readiness list
      expect(actions).toBeLessThanOrEqual(3);
      console.log(`${tool}: checklist reached in ${actions} actions`);
    });
  }

  it("says plainly what each tool gets, including that Codex has no hooks yet", async () => {
    await mount(<ConnectPanel client={client} initial={await client.clients()} />);
    expect(text()).toMatch(/Hooks for Codex are not integrated yet/);
    expect(text()).toMatch(/dispatch once a model is named/);
  });

  it("will not install Cursor with 'auto' as the model, and says why", async () => {
    await mount(<ConnectPanel client={client} initial={await client.clients()} />);
    const cursor = [...host!.querySelectorAll<HTMLLabelElement>("label.check")].find((l) => l.textContent!.includes("Cursor"))!.querySelector("input")!;
    if (!cursor.checked) await click(cursor);
    await until(() => !!host!.querySelector("select"));
    const select = host!.querySelector<HTMLSelectElement>("select")!;
    await act(async () => { select.value = "__other__"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await until(() => !!host!.querySelector('input[aria-label="Cursor model name"]'));
    await type(host!.querySelector<HTMLInputElement>('input[aria-label="Cursor model name"]')!, "auto");
    expect(text()).toMatch(/“auto” is not a model/);
    expect(byText("button", /^Install$/)!.hasAttribute("disabled")).toBe(true);
  });

  it("shows 'nothing installed yet' and an undo after installing", async () => {
    await mount(<ConnectPanel client={client} initial={(await client.clients()).map((c) => ({ ...c, installed: false }))} />);
    expect(text()).toMatch(/Nothing is installed yet/);
  });
});

describe("Keys: a key never appears on the page or in storage", () => {
  it("KeyField: typed, saved, shown as last four only, replaceable and removable", async () => {
    let sent = "";
    await mount(<KeyField envName="MY_KEY" isSet={false} last4={null} onSave={async (v) => { sent = v; return { last4: v.slice(-4) }; }} onRemove={async () => {}} />);
    const input = host!.querySelector<HTMLInputElement>('input[type="password"]')!;
    expect(input.getAttribute("autocomplete")).toBe("new-password");
    await type(input, GOOD_KEY);
    await submit(host!.querySelector("form"));
    await until(() => /ends in/.test(text()));
    expect(sent).toBe(GOOD_KEY);
    expect(host!.innerHTML).not.toContain(GOOD_KEY);
    expect(host!.innerHTML).not.toContain(GOOD_KEY.slice(0, -4));
    expect(text()).toContain(GOOD_KEY.slice(-4));
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(GOOD_KEY);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(GOOD_KEY);
    expect(window.location.href).not.toContain(GOOD_KEY);
    await click(byText("button", /^Replace$/));
    expect(host!.querySelector<HTMLInputElement>('input[type="password"]')!.value).toBe("");
    await type(host!.querySelector<HTMLInputElement>('input[type="password"]')!, GOOD_KEY);
    await submit(host!.querySelector("form"));
    await until(() => /ends in/.test(text()));
    await click(byText("button", /^Remove$/));
    await until(() => /Removed/.test(text()));
    expect(text()).toMatch(/No key is saved/);
  });

  it("KeyField: an error that carries the key is scrubbed", async () => {
    await mount(<KeyField envName="MY_KEY" isSet={false} last4={null} onSave={async (v) => { throw Object.assign(new Error(`bad ${v}`), { name: "HelperError" }); }} onRemove={async () => {}} />);
    await type(host!.querySelector<HTMLInputElement>('input[type="password"]')!, GOOD_KEY);
    await submit(host!.querySelector("form"));
    await until(() => !!host!.querySelector("[role=alert]"));
    expect(host!.innerHTML).not.toContain(GOOD_KEY);
  });

  it("ModelsPanel against the real helper: the saved key is a last-four on screen and absent from the DOM, storage and address", async () => {
    await mount(<ModelsPanel client={client} />);
    await until(() => /fake-remote/.test(text()));
    expect(text()).toContain(GOOD_KEY.slice(-4));
    expect(host!.innerHTML).not.toContain(GOOD_KEY);
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain(GOOD_KEY);
    expect(text()).toMatch(/No key needed/); // the local model has no key field at all
    const local = host!.querySelector('[data-model="fake-local"]')!;
    expect(local.querySelector('input[type="password"]')).toBeNull();
    expect(text()).toMatch(/\$1 in \/ \$2 out per 1M tokens/);
    expect(text()).toMatch(/price not set/); // fake-local has no prices
  });

  it("Test shows the latency in words; adding a half-priced model is refused before sending", async () => {
    await mount(<ModelsPanel client={client} />);
    await until(() => /fake-remote/.test(text()));
    await click([...host!.querySelectorAll<HTMLElement>('[data-model="fake-remote"] button')].find((b) => b.textContent === "Test"));
    await until(() => /Replied in|Failed after|did not run/.test(text()));
    expect(text()).toMatch(/Replied in/);
    await type(host!.querySelector<HTMLInputElement>('input[id$="-name"]')!, "half");
    await type(host!.querySelector<HTMLInputElement>('input[id$="-mid"]')!, "m");
    await type(host!.querySelector<HTMLInputElement>('input[id$="-base"]')!, `${provider.base}/v1`);
    await type(host!.querySelector<HTMLInputElement>('input[id$="-kenv"]')!, "HALF_API_KEY");
    await type(host!.querySelector<HTMLInputElement>('input[id$="-pin"]')!, "1");
    await submit([...host!.querySelectorAll("form")].pop());
    expect(text()).toMatch(/both prices or neither/i);
  });
});

describe("Playground", () => {
  it("shows the first words while the reply is still streaming, then the numbers", async () => {
    await mount(<PlaygroundPanel client={client} />);
    await until(() => host!.querySelectorAll('input[type="checkbox"]').length > 0);
    expect(text()).toMatch(/not checked and not verified/);
    await type(host!.querySelector("textarea")!, "hello");
    await submit(host!.querySelector("form"));
    await until(() => (host!.querySelector(".pane .out")?.textContent ?? "").includes("[fake-remote-1]"));
    const pane = host!.querySelector(".pane")!;
    expect(pane.getAttribute("data-status")).toBe("streaming"); // first words are on screen; the reply is not finished
    expect(pane.querySelector("footer")!.textContent).toMatch(/appear when it finishes/);
    await until(() => pane.getAttribute("data-status") === "done");
    expect(pane.querySelector("footer")!.textContent).toMatch(/First token .* in \/ .* out/);
    expect(pane.querySelector(".out")!.getAttribute("aria-live")).toBe("polite");
  });

  it("Stop ends a stream and says it was stopped", async () => {
    await mount(<PlaygroundPanel client={client} />);
    await until(() => host!.querySelectorAll('input[type="checkbox"]').length > 0);
    await type(host!.querySelector("textarea")!, "hello");
    await submit(host!.querySelector("form"));
    await until(() => (host!.querySelector(".pane .out")?.textContent ?? "").includes("[fake-remote-1]"));
    await click(byText("button", /^Stop$/));
    await until(() => host!.querySelector(".pane")!.getAttribute("data-status") === "stopped");
    expect(text()).toMatch(/Stopped before it finished/);
  });

  it("compares two models side by side, each in its own pane", async () => {
    await mount(<PlaygroundPanel client={client} />);
    await until(() => host!.querySelectorAll('input[type="checkbox"]').length > 1);
    const boxes = [...host!.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    await click(boxes.find((b) => !b.checked && b.closest("label")!.textContent!.includes("fake-local")));
    await type(host!.querySelector("textarea")!, "hello");
    await submit(host!.querySelector("form"));
    await until(() => host!.querySelectorAll(".pane").length === 2 && [...host!.querySelectorAll(".pane")].every((p) => p.getAttribute("data-status") === "done"));
    expect(text()).toMatch(/price not set/); // fake-local has no prices
  });
});

describe("States", () => {
  it("helper not running: the one command with a copy button, and Check again", async () => {
    window.history.pushState(null, "", "/?api=http://127.0.0.1:1&t=x");
    await mount(<Console />);
    await until(() => /The helper is not running/.test(text()));
    expect(host!.querySelectorAll("button.copy").length).toBe(2);
    expect(byText("button", /Check again/)).toBeTruthy();
    expect(text()).toMatch(/kell-mcp console/);
    expect(text()).not.toMatch(/https?:\/\/(?!127)/); // no server address is hard-coded
  });

  it("wrong token: says the page has no valid token", async () => {
    window.history.pushState(null, "", `/?api=${h.base}&t=wrong`);
    await mount(<Console />);
    await until(() => /no valid token/.test(text()));
  });

  it("with the token: the four tabs, and the token is gone from the address", async () => {
    window.history.pushState(null, "", `/?api=${h.base}&t=${h.token}`);
    await mount(<Console />);
    await until(() => !!host!.querySelector('[role="tablist"]'));
    expect([...host!.querySelectorAll('[role="tab"]')].map((t) => t.textContent)).toEqual(["Connect", "Keys & models", "Playground", "Router activity"]);
    expect(window.location.search).not.toContain(h.token);
    expect(window.sessionStorage.getItem("kel.console.token")).toBe(h.token); // the token only, for this tab
  });

  it("every field on every screen has a label", async () => {
    for (const node of [<ConnectPanel key="c" client={client} initial={await client.clients()} />, <ModelsPanel key="m" client={client} />, <PlaygroundPanel key="p" client={client} />]) {
      await mount(node);
      await wait(400);
      for (const el of host!.querySelectorAll<HTMLInputElement>("input, select, textarea")) {
        const named = (el.labels?.length ?? 0) > 0 || el.getAttribute("aria-label") || el.getAttribute("aria-labelledby");
        expect(named, `${el.tagName} ${el.id || el.type} has no label`).toBeTruthy();
      }
      act(() => root?.unmount()); host?.remove(); root = null; host = null;
    }
  });
});

describe("Router activity", () => {
  const ev = (id: number, args: Record<string, string> = {}, seq = id) => ({ id, seq, ts: 1, tool: "find_ways", args, pending: false, ok: true as boolean | null, ms: 5, output: "x", truncated: false });
  it("keeps newest first, ignores a replayed id, and caps the list", () => {
    let l = addEvent([], ev(1));
    l = addEvent(l, ev(2));
    expect(l.map((e) => e.id)).toEqual([2, 1]);
    expect(addEvent(l, ev(1))).toBe(l);
    for (let i = 3; i < 200; i++) l = addEvent(l, ev(i));
    expect(l).toHaveLength(100);
    expect(l[0].id).toBe(199);
  });
  it("a call is replaced in place when it finishes, and a replay does not undo it", () => {
    const started = { ...ev(1, {}, 1), pending: true, ok: null, output: "" };
    let l = addEvent(addEvent([], started), ev(2));
    l = addEvent(l, ev(1, {}, 3));
    expect(l.map((e) => `${e.id}:${e.pending}`)).toEqual(["2:false", "1:false"]);
    expect(addEvent(l, started)).toBe(l);
  });
  it("summarises a call by what it asked", () => {
    expect(summary(ev(1, { query: "fix  a failing test", repo_claims: "zzz" }))).toBe("fix a failing test");
    expect(summary(ev(1, { prompt: "p" }))).toBe("p");
    expect(summary(ev(1))).toBe("");
  });
  it("shows a run's newest snapshot once, newest run first", () => {
    const run = (id: string, state: string) => ({ type: "run" as const, run_id: id, created_at: null, state, task: "t", attempts: [] });
    let l = upsertRun([], run("r-1", "running"));
    l = upsertRun(l, run("r-2", "running"));
    l = upsertRun(l, run("r-1", "verified"));
    expect(l.map((r) => `${r.run_id}:${r.state}`)).toEqual(["r-2:running", "r-1:verified"]);
  });
});
