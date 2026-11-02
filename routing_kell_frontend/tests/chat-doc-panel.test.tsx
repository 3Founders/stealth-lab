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
      ? { id: "d1", version, present: ["Asha"], unchanged: true as const }
      : { id: "d1", version, present: ["Asha"], title: "Plan", kind: "document" as const, body, updated_by: "Asha", updated_at: null }),
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
    await mount(api);
    expect(editor().value).toContain("Step one.");
    expect(host!.querySelector(".dg-doc-here")?.textContent).toBe("Asha is here");
    await type(editor().value + "\nStep two.\n");
    expect(host!.querySelector(".dg-doc-status")?.textContent).toBe("Editing…");
    await wait(80);
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

  it("when the server merged two saves it shows the merged text and says so", async () => {
    const f = fakeApi();
    f.api.save = vi.fn(async () => { f.set("# Plan\n\nMerged text.\n", 3); return { version: 3, merged: true, clash: false, body: "# Plan\n\nMerged text.\n" }; }) as never;
    const { api } = f;
    await mount(api);
    await type("# Plan\n\nMy text.\n");
    await wait(80);
    expect(editor().value).toBe("# Plan\n\nMerged text.\n");
    expect(host!.querySelector(".dg-doc-note")?.textContent).toContain("Merged with someone else");
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
    expect(onAsk.mock.calls[0][0]).toContain('@kell Edit the document');
    expect(onAsk.mock.calls[0][0]).toContain("```canvas title=");
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
