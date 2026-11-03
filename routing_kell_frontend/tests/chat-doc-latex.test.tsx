// @vitest-environment jsdom
// A LaTeX canvas: the panel opens in Side by side, typesets the source beside the editor, follows what you type, and offers Write / Side by side / Read.
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

const SRC = String.raw`\documentclass{article}
\title{Sums}
\begin{document}
\maketitle
\section{Intro}
We know $e^{i\pi}+1=0$ and \textbf{more}.
\begin{equation}
  x^2 = 4
\end{equation}
\end{document}`;

function api(): DocsApi {
  let body = SRC;
  return {
    list: vi.fn(async () => [{ id: "t1", title: "Sums", kind: "latex" as const, version: 1, updated_by: "Asha", updated_at: null, size: body.length }]),
    get: vi.fn(async () => ({ id: "t1", version: 1, present: ["Asha"], editing: [], title: "Sums", kind: "latex" as const, body, updated_by: "Asha", updated_at: null })),
    save: vi.fn(async (_r: string, _id: string, text: string, base: number) => { body = text; return { version: base + 1, merged: false }; }),
    create: vi.fn(async () => ({ id: "t2", title: "New", kind: "latex" as const, version: 1 })),
    remove: vi.fn(), history: vi.fn(async () => []), restore: vi.fn(),
  } as unknown as DocsApi;
}

async function mount(a: DocsApi) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => { root!.render(<DocPanel open onClose={() => {}} api={a} roomId="r1" wantedTitle={null} onAsk={() => {}} timing={{ poll: 4000, save: 100 }} />); });
  await wait(30);
}
const tabs = () => [...host!.querySelectorAll('[role="tab"]')].map((b) => b.textContent);

describe("LaTeX canvas", () => {
  it("opens beside the typeset page, with Write, Side by side and Read", async () => {
    await mount(api());
    expect(tabs()).toEqual(["Write", "Side by side", "Read"]);
    const page = host!.querySelector(".dg-tex");
    expect(page).not.toBeNull();
    expect(page!.querySelector("h1")?.textContent).toBe("Sums");
    expect(page!.querySelector(".katex")).not.toBeNull();                         // maths is typeset by KaTeX
    expect(page!.querySelector(".dg-tex-display")).not.toBeNull();                // the equation is a display
    expect(page!.textContent).toContain("more");
    expect(host!.querySelector("textarea.dg-doc-text")?.getAttribute("data-kind")).toBe("latex");
  });

  it("the typeset page follows what you type", async () => {
    await mount(api());
    const ed = host!.querySelector("textarea.dg-doc-text") as HTMLTextAreaElement;
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    await act(async () => { set.call(ed, SRC.replace("\\section{Intro}", "\\section{Background}")); ed.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(host!.querySelector(".dg-tex h2")?.textContent).toContain("Background");
  });

  it("Read shows only the typeset page, and a new canvas can be LaTeX", async () => {
    const a = api();
    await mount(a);
    await act(async () => { (host!.querySelectorAll('[role="tab"]')[2] as HTMLButtonElement).click(); });
    expect(host!.querySelector("textarea.dg-doc-text")).toBeNull();
    expect(host!.querySelector(".dg-tex")).not.toBeNull();
  });

  it("raw HTML in LaTeX text is shown as text, never run", async () => {
    const a = api();
    (a.get as ReturnType<typeof vi.fn>).mockResolvedValue({ id: "t1", version: 1, present: [], editing: [], title: "X", kind: "latex", body: "Hello <img src=x onerror=alert(1)> \\href{javascript:alert(1)}{click}", updated_by: "A", updated_at: null });
    await mount(a);
    expect(host!.querySelector(".dg-tex img")).toBeNull();
    expect(host!.querySelector('.dg-tex a[href^="javascript"]')).toBeNull();
  });
});
