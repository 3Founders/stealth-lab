// @vitest-environment jsdom
// The canvas panel draws what an agent wrote: a page in a sandboxed frame (even when the agent forgot kind=html), a document as formatted
// text, code as code; steps through versions; closes.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import CanvasPanel from "@/components/chat/CanvasPanel";
import { addVersion, newCanvas, viewOf } from "@/lib/chat/canvas";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
async function mount(node: React.ReactNode) { host = document.createElement("div"); document.body.append(host); root = createRoot(host); await act(async () => { root!.render(node); }); }
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; document.documentElement.style.removeProperty("--dg-canvas-w"); });

describe("canvas panel", () => {
  it("decides how to draw by what the text is, not only by the kind the agent named", () => {
    expect(viewOf("document", "<!DOCTYPE html><html><body>hi</body></html>")).toBe("page");
    expect(viewOf("document", "<svg viewBox='0 0 10 10'></svg>")).toBe("page");
    expect(viewOf("code", "const a = 1;")).toBe("code");
    expect(viewOf("document", "# Plan\n\n- one")).toBe("markdown");
    expect(viewOf("html", "anything")).toBe("page");
  });

  it("draws a page in a sandbox with no same-origin access, even when it was labelled a document", async () => {
    const c = newCanvas("1", "#r", "Landing", "document", "<h1>Hello</h1>", "@kell", 1);
    await mount(<CanvasPanel open onClose={() => {}} canvases={[c]} room="#r" selectedId="1" />);
    const f = host!.querySelector("iframe")!;
    expect(f).not.toBeNull();
    expect(f.getAttribute("sandbox")).toBe("allow-scripts allow-downloads allow-forms");
    expect(f.getAttribute("srcdoc")).toContain("<h1>Hello</h1>");
  });

  it("shows a document as formatted text and steps through versions", async () => {
    let c = newCanvas("1", "#r", "Plan", "document", "# First", "@kell", 1);
    c = addVersion(c, "# Second\n\n- item", "@kell", 2, "m2");
    await mount(<CanvasPanel open onClose={() => {}} canvases={[c]} room="#r" selectedId="1" />);
    expect(host!.textContent).toContain("Version 2 of 2");
    expect(host!.querySelector(".dg-canvas-doc h4, .dg-canvas-doc h3")?.textContent).toBe("Second");
    await act(async () => { (host!.querySelector('button[aria-label="Previous version"]') as HTMLElement).click(); });
    expect(host!.textContent).toContain("Version 1 of 2");
    expect(host!.querySelector(".dg-canvas-doc")?.textContent).toContain("First");
  });

  it("copies, closes, and reserves room beside the chat while open", async () => {
    const onClose = vi.fn();
    const c = newCanvas("1", "#r", "Code", "code", "let x = 1", "@kell", 1);
    const write = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    await mount(<CanvasPanel open onClose={onClose} canvases={[c]} room="#r" selectedId="1" />);
    expect(host!.querySelector("pre.dg-canvas-pre")?.textContent).toBe("let x = 1");
    expect(document.documentElement.style.getPropertyValue("--dg-canvas-w")).toMatch(/px$/);
    await act(async () => { (host!.querySelector(".dg-cv-copy") as HTMLElement).click(); });
    expect(write).toHaveBeenCalledWith("let x = 1");
    await act(async () => { (host!.querySelector('button[aria-label="Close"]') as HTMLElement).click(); });
    expect(onClose).toHaveBeenCalled();
  });
});
