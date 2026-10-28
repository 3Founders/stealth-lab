// @vitest-environment jsdom
// /account/models: signed-in only; saves a key through the vault (never to localStorage or the page); Test reports pass or fail
// without the key; Delete removes it; the copy never asks for a chat-app login.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SECRET = "sk-ant-MODELSPAGESECRET-0123456789";
let session: { token: string; subject: string } | null = { token: "t", subject: "u1" };
const rows: Array<Record<string, string>> = [];
let models: string[] | Error = ["model-a", "model-b"];

vi.mock("@/lib/session", () => ({
  getSession: () => Promise.resolve(session),
  onSessionChange: () => () => {},
}));
vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/byok/vault", () => ({
  idbStore: () => ({}),
  Vault: class {
    async add(i: { provider: string; baseUrl: string; label: string; apiKey: string }) {
      const meta = { id: `k${rows.length + 1}`, provider: i.provider, baseUrl: i.baseUrl, label: i.label, last4: i.apiKey.slice(-4), model: "", createdAt: new Date().toISOString() };
      rows.push(meta); (this as unknown as { _k: Record<string, string> })._k = { ...((this as unknown as { _k?: Record<string, string> })._k ?? {}), [meta.id]: i.apiKey };
      keyOf[meta.id] = i.apiKey;
      return meta;
    }
    async list() { return rows.map((r) => ({ ...r })); }
    async reveal(id: string) { return keyOf[id] ?? null; }
    async setModel(id: string, m: string) { const r = rows.find((x) => x.id === id); if (r) r.model = m; }
    async remove(id: string) { const i = rows.findIndex((x) => x.id === id); if (i >= 0) rows.splice(i, 1); }
  },
}));
const keyOf: Record<string, string> = {};
vi.mock("@/lib/byok/chat", async (orig) => ({
  ...(await orig<typeof import("@/lib/byok/chat")>()),
  listModels: async () => { if (models instanceof Error) throw models; return models; },
}));

import ModelsPage from "@/app/account/models/page";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
async function mount() {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<ModelsPage />); });
  await flush(); await flush();
}
const type = async (el: HTMLInputElement, v: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => { setter.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); });
};
const click = async (el: Element) => { await act(async () => { (el as HTMLElement).click(); }); await flush(); };
const byText = (t: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.trim() === t)!;

afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; rows.length = 0; for (const k of Object.keys(keyOf)) delete keyOf[k]; session = { token: "t", subject: "u1" }; models = ["model-a", "model-b"]; vi.restoreAllMocks(); });

describe("Models page", () => {
  it("shows the kell-hosted models as a labelled pilot that is not available yet, with the data warning, and makes no network call", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await mount();
    const text = host!.textContent ?? "";
    expect(text).toMatch(/Pilot/);
    expect(text).toMatch(/Public material only/);
    expect(text).toMatch(/no data-processing agreement/);
    expect(text).toMatch(/Not available yet/);
    expect(text).toMatch(/never used instead of one/);
    expect(f).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("signed out: only a sign-in notice that returns here", async () => {
    session = null;
    await mount();
    expect(host!.textContent).toContain("Sign in to add a key");
    expect(host!.querySelector('a[href="/sign-in?redirect=/account/models"]')).not.toBeNull();
    expect(host!.querySelector("form")).toBeNull();
  });

  it("says plainly where the key goes, and never asks for a chat-app login", async () => {
    await mount();
    const text = host!.textContent ?? "";
    expect(text).toMatch(/encrypted in this browser/);
    expect(text).toMatch(/never sees it, never pays/);
    expect(text).not.toMatch(/sign in with claude/i);
    expect(text).not.toMatch(/claude\.ai/i);
    expect(text).toMatch(/API key/);
  });

  it("saves a key without writing it to storage or leaving it on the page, then Test, pick a model, and Delete", async () => {
    const writes: string[] = [];
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((k, v) => { writes.push(`${k}=${v}`); });
    await mount();
    const key = host!.querySelector<HTMLInputElement>("#m-key")!;
    expect(key.type).toBe("password");
    await type(key, SECRET);
    await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush(); await flush();
    expect(rows).toHaveLength(1);
    expect(host!.textContent).toContain("ends in " + SECRET.slice(-4));
    expect(host!.innerHTML).not.toContain(SECRET);
    expect(writes.join("|")).not.toContain(SECRET);
    expect(document.querySelector("#m-key") === null || (document.querySelector<HTMLInputElement>("#m-key")!.value === "")).toBe(true);

    await click(byText("Test"));
    expect(host!.textContent).toMatch(/The key works\. 2 models available/);
    await click(byText("Use this model"));
    expect(rows[0].model).toBe("model-a");
    expect(host!.textContent).toContain("model-a");

    await click(byText("Delete"));
    expect(rows).toHaveLength(0);
    expect(host!.textContent).toMatch(/No keys yet/);
  });

  it("a failed Test shows the reason and never the key", async () => {
    models = new Error(`The provider answered 401: Incorrect key ${SECRET}`);
    await mount();
    await type(host!.querySelector<HTMLInputElement>("#m-key")!, SECRET);
    await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush(); await flush();
    await click(byText("Test"));
    expect(host!.querySelector('[role="alert"]')!.textContent).toContain("401");
    expect(host!.innerHTML).not.toContain(SECRET);
  });

  it("an 'other' provider needs an https address", async () => {
    await mount();
    const select = host!.querySelector<HTMLSelectElement>("#m-provider")!;
    await act(async () => { select.value = "other"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await type(host!.querySelector<HTMLInputElement>("#m-key")!, SECRET);
    await type(host!.querySelector<HTMLInputElement>("#m-base")!, "http://insecure.example.com/v1");
    await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(rows).toHaveLength(0);
    expect(host!.querySelector('[role="alert"]')!.textContent).toMatch(/https/);
  });
});
