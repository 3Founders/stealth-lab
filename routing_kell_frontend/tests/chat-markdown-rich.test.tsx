// @vitest-environment jsdom
// What the 2026-11-05 basics check found in a chat answer: maths shown as raw $$...$$, a code block with no Copy button, checklist items shown as
// plain bullets with "[]", and a web link that must be clickable. Rendered as elements, never raw HTML.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { marked } from "marked";
import Markdown from "@/components/chat/Markdown";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("maths in chat", () => {
  it("typesets $$...$$ as a display formula, not raw text", async () => {
    await mount(<Markdown text={"The area is:\n\n$$A = \\pi r^2$$"} />);
    expect(host!.querySelector(".katex")).not.toBeNull();
    expect(host!.querySelector(".dg-md-math-d")).not.toBeNull();
    expect(host!.textContent).not.toContain("$$");
  });

  it("typesets inline $x$, \\(x\\) and \\[x\\]", async () => {
    await mount(<Markdown text={"Inline $a^2 + b^2 = c^2$ and \\(e^{i\\pi}\\) here.\n\n\\[\\int_0^1 x\\,dx\\]"} />);
    expect(host!.querySelectorAll(".katex").length).toBe(3);
    expect(host!.querySelectorAll(".dg-md-math-d").length).toBe(1);
    expect(host!.textContent).not.toContain("\\(");
  });

  it("leaves money alone: '$5 and $10' is text", async () => {
    await mount(<Markdown text={"It costs $5 and then $10 more."} />);
    expect(host!.querySelector(".katex")).toBeNull();
    expect(host!.textContent).toContain("$5 and then $10");
  });

  it("does not typeset inside code", async () => {
    await mount(<Markdown text={"Use `$x$` in LaTeX.\n\n```\n$$not math$$\n```"} />);
    expect(host!.querySelector(".katex")).toBeNull();
    expect(host!.textContent).toContain("$$not math$$");
  });

  it("a bad formula shows its source, never an exception or HTML", async () => {
    await mount(<Markdown text={"$$\\frac{1}{$$"} />);
    expect(host!.querySelector("script")).toBeNull();
    expect(host!.textContent!.length).toBeGreaterThan(0);
  });
});

describe("code blocks and lists in chat", () => {
  it("a fenced block shows its language and a Copy button that copies the code as written", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });
    await mount(<Markdown text={"```python\ndef reverse(s):\n    return s[::-1]\n```"} />);
    expect(host!.querySelector(".dg-md-codehead span")?.textContent).toBe("python");
    const btn = host!.querySelector("button.dg-md-copy") as HTMLButtonElement;
    expect(btn).not.toBeNull();
    await act(async () => { btn.click(); });
    expect(writeText).toHaveBeenCalledWith("def reverse(s):\n    return s[::-1]");
    expect(btn.textContent).toBe("Copied");
  });

  it("checklist items are checkboxes (disabled), done ones ticked", async () => {
    await mount(<Markdown text={"- [ ] passport\n- [x] charger\n- socks"} />);
    const boxes = host!.querySelectorAll("input[type=checkbox]");
    expect(boxes.length).toBe(2);
    expect((boxes[0] as HTMLInputElement).checked).toBe(false);
    expect((boxes[1] as HTMLInputElement).checked).toBe(true);
    expect((boxes[0] as HTMLInputElement).disabled).toBe(true);
    expect(host!.textContent).not.toContain("[ ]");
    expect(host!.querySelectorAll("li").length).toBe(3);
  });

  it("a bare web address is a link that opens in a new tab", async () => {
    await mount(<Markdown text={"Source: https://indianexpress.com/article/india/example-1234/ (5 Nov 2026)"} />);
    const a = host!.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://indianexpress.com/article/india/example-1234/");
    expect(a.getAttribute("rel")).toContain("noopener");
  });
});

describe("the shared marked instance", () => {
  it("a formula in text that other pages parse (the legal pages) stays escaped text and never throws", () => {
    expect(marked.parse("Fees are $x$ <b>bold</b>", { async: false }) as string).toContain("$x$");
  });
});
