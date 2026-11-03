// @vitest-environment jsdom
// The docs panel: opens a room's document, saves what you type after a pause, merges what others saved without losing your typing,
// shows who is here, and creates, restores and asks kell to edit.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import DocPanel from "@/components/chat/DocPanel";
import type { DocsApi } from "@/lib/chat/docs-api";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; });
const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
const TIMING = { poll: 40, save: 30 };

function fakeApi(over: Partial<DocsApi> = {}) {
  let body = "# Plan\n\nIntro.\n\nStep one.\n", version = 1;
  const api = {
    list: vi.fn(async () => [{ id: "d1", title: "Plan", kind: "document" as const, version, updated_by: "Asha", updated_at: null, size: body.length }]),
    get: vi.fn(async (_r: string, _id: string, since?: number) => since === version
      ? { id: "d1", version, present: ["Asha"], editing: [], unchanged: true as const }
      : { id: "d1", version, present: ["Asha"], editing: [], title: "Plan", kind: "document" as const, body, updated_by: "Asha", updated_at: null }),
    save: vi.fn(async (_r: string, _id: string, text: string, base: number) => { body = text; version = base + 1; return { version, merged: false }; }),
    create: vi.fn(async () => ({ id: "d2", title: "New", kind: "document" as const, version: 1 })),
    remove: vi.fn(async () => ({ deleted: true })),
    history: vi.fn(async () => [{ version: 2, by: "Bo", note: "", size: 10, at: null }, { version: 1, by: "Asha", note: "", size: 8, at: null }]),
    restore: vi.fn(async () => ({ version: 3, body: "# Old\n" })),
    ...over,
  } as unknown as DocsApi;
  return { api, set: (b: string, v: number) => { body = b; version = v; } };
}

async function mount(api: DocsApi, extra: Partial<React.ComponentProps<typeof DocPanel>> = {}) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => { root!.render(<DocPanel open onClose={() => {}} api={api} roomId="r1" wantedTitle={null} onAsk={() => {}} timing={TIMING} {...extra} />); });
  await wait(20);
}
const editor = () => host!.querySelector("textarea.dg-doc-text") as HTMLTextAreaElement;
const type = async (v: string) => {
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  await act(async () => { set.call(editor(), v); editor().dispatchEvent(new Event("input", { bubbles: true })); });
};

describe("docs panel", () => {
  it("opens the room's document, shows who is here, and saves what you type after a pause", async () => {
    const { api } = fakeApi();
    await mount(api, { timing: { poll: 4000, save: 200 } });
    expect(editor().value).toContain("Step one.");
    expect(host!.querySelector(".dg-doc-here")?.textContent).toBe("Asha is here");
    await type(editor().value + "\nStep two.\n");
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Editing…");
    await wait(320);
    expect((api.save as ReturnType<typeof vi.fn>).mock.calls[0].slice(1, 4)).toEqual(["d1", "# Plan\n\nIntro.\n\nStep one.\n\nStep two.\n", 1]);
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Saved");
  });

  it("shows what someone else saved, and keeps what you were typing", async () => {
    const f = fakeApi();
    await mount(f.api, { timing: { poll: 40, save: 5000 } });       // a long pause before saving: my typing is still unsaved when theirs arrives
    await type("# Plan\n\nIntro, mine.\n\nStep one.\n");                     // I edit line 3 and have not saved yet
    f.set("# Plan\n\nIntro.\n\nStep one.\n\nStep two, theirs.\n", 2);        // they add a line meanwhile
    await wait(35);
    expect(editor().value).toBe("# Plan\n\nIntro, mine.\n\nStep one.\n\nStep two, theirs.\n");
  });

  it("when the server merged two saves it shows the merged text quietly (status only, no banner)", async () => {
    const f = fakeApi();
    f.api.save = vi.fn(async () => { f.set("# Plan\n\nMerged text.\n", 3); return { version: 3, merged: true, clash: false, body: "# Plan\n\nMerged text.\n" }; }) as never;
    const { api } = f;
    await mount(api);
    await type("# Plan\n\nMy text.\n");
    await wait(80);
    expect(editor().value).toBe("# Plan\n\nMerged text.\n");
    expect(host!.querySelector(".dg-doc-note")).toBeNull();                       // no banner pops up while others edit
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Saved");
  });

  it("makes a new document, restores a version and asks kell to edit with the current text", async () => {
    const { api } = fakeApi();
    const onAsk = vi.fn();
    await mount(api, { onAsk });
    await act(async () => { (host!.querySelector(".dg-doc-pick .dg-cv-chip") as HTMLElement).click(); });
    const input = host!.querySelector(".dg-doc-new input") as HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => { set.call(input, "New"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => { (host!.querySelector(".dg-doc-new button") as HTMLElement).click(); });
    expect((api.create as ReturnType<typeof vi.fn>).mock.calls[0].slice(1, 3)).toEqual(["New", "document"]);

    const btn = (label: string) => [...host!.querySelectorAll(".dg-doc-actions button")].find((b) => b.textContent === label) as HTMLElement;
    await act(async () => { btn("History").click(); });
    await wait(10);
    expect(host!.querySelector(".dg-doc-history")?.textContent).toContain("Version 2");
    await act(async () => { (host!.querySelector(".dg-doc-history li .dg-linkbtn") as HTMLElement).click(); });
    await wait(10);
    expect((api.restore as ReturnType<typeof vi.fn>)).toHaveBeenCalled();

    await act(async () => { btn("Ask kell to edit").click(); });
    // a short line that names the canvas: the document and its fence are not pasted into the message box (the open canvas travels with the request)
    expect(onAsk.mock.calls[0][0]).toBe('@kell Edit "Plan": ');
    expect(onAsk.mock.calls[0][0]).not.toContain("canvas title=");
  });

  it("offers one-click quick edits that send a short request at once", async () => {
    const onQuick = vi.fn();
    const { api } = fakeApi();
    await mount(api, { onQuick } as never);
    await wait(10);
    const quick = [...host!.querySelectorAll(".dg-doc-quick button")].find((b) => b.textContent === "Polish") as HTMLElement;
    await act(async () => { quick.click(); });
    expect(onQuick).toHaveBeenCalledTimes(1);
    expect(onQuick.mock.calls[0][0]).toMatch(/^@kell Polish "Plan": fix grammar/);
    expect(onQuick.mock.calls[0][0].length).toBeLessThan(160);
  });

  it("says it is offline and keeps the text when a save cannot reach the server", async () => {
    const { DocsApiError } = await import("@/lib/chat/docs-api");
    const save = vi.fn(async () => { throw new DocsApiError("Couldn't reach the keळ server."); });
    const { api } = fakeApi({ save } as never);
    await mount(api);
    await type("# Plan\n\nKept.\n");
    await wait(60);
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Offline");
    expect(editor().value).toBe("# Plan\n\nKept.\n");
  });
});

describe("docs panel over a live socket", () => {
  class FakeWs {
    static last: FakeWs | null = null;
    readyState = 0; sent: Array<Record<string, unknown>> = [];
    onopen: (() => void) | null = null; onmessage: ((e: { data: string }) => void) | null = null; onclose: (() => void) | null = null; onerror: (() => void) | null = null;
    constructor(public url: string) { FakeWs.last = this; setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
    send(d: string) { this.sent.push(JSON.parse(d)); }
    close() { this.readyState = 3; this.onclose?.(); }
    push(m: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(m) }); }
  }

  it("sends edits over the socket with the version it was typing on, and folds in what others save around your typing", async () => {
    const { api } = fakeApi();
    await mount(api, { timing: { poll: 4000, save: 30 }, socket: { token: async () => "tok", origin: "https://mcp.example", socketFactory: (u) => new FakeWs(u) as unknown as WebSocket } });
    await wait(10);
    const ws = FakeWs.last!;
    expect(ws.url).toBe("https://mcp.example/v1/rooms/r1/docs/d1/ws".replace("https", "wss"));
    expect(ws.sent[0]).toEqual({ type: "auth", token: "tok" });
    await act(async () => { ws.push({ type: "hello", id: "d1", version: 1, title: "Plan", kind: "document", body: "# Plan\n\nIntro.\n\nStep one.\n", present: ["Asha", "Bo"], editing: [] }); });
    expect(host!.querySelector(".dg-doc-here")?.textContent).toBe("2 people here");

    await type("# Plan\n\nIntro, mine.\n\nStep one.\n");
    await wait(220);                                                                     // sent soon after typing, not after a long pause
    const edit = ws.sent.find((m) => m.type === "edit")!;
    expect(edit).toEqual({ type: "edit", body: "# Plan\n\nIntro, mine.\n\nStep one.\n", base: 1 });
    expect((api.save as ReturnType<typeof vi.fn>)).not.toHaveBeenCalled();               // the HTTP route was not used

    // while that edit is in flight, Bo saves a change at the end: it is folded in without losing the typing
    await act(async () => { ws.push({ type: "doc", version: 2, body: "# Plan\n\nIntro.\n\nStep one, by Bo.\n", by: "Bo" }); });
    expect(editor().value).toBe("# Plan\n\nIntro, mine.\n\nStep one, by Bo.\n");
    expect(host!.querySelector(".dg-doc-note")).toBeNull();                              // and nothing pops up about it
    await act(async () => { ws.push({ type: "ack", version: 3, body: "# Plan\n\nIntro, mine.\n\nStep one, by Bo.\n" }); });
    expect(editor().value).toBe("# Plan\n\nIntro, mine.\n\nStep one, by Bo.\n");
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Saved");
  });
});
