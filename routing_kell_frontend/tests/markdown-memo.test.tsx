// @vitest-environment jsdom
// Streaming markdown: finished blocks keep their DOM (block memoization), and raw HTML/links stay inert.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import Markdown from "@/components/chat/Markdown";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

function mount(text: string) {
  host = document.createElement("div"); document.body.appendChild(host);
  act(() => { root = createRoot(host!); root.render(<Markdown text={text} />); });
}
function rerender(text: string) { act(() => { root!.render(<Markdown text={text} />); }); }

describe("streaming markdown", () => {
  it("a finished block keeps the same DOM node when more text arrives", () => {
    mount("First paragraph.\n\nSecond.");
    const first = host!.querySelector("p")!;
    rerender("First paragraph.\n\nSecond.\n\nThird.");
    expect(host!.querySelector("p")).toBe(first);
    expect(host!.querySelectorAll("p").length).toBe(3);
  });

  it("raw HTML is shown as text, not rendered", () => {
    mount('<img src=x onerror="alert(1)"> and <b>bold</b>');
    expect(host!.querySelector("img")).toBeNull();
    expect(host!.querySelector("b")).toBeNull();
    expect(host!.textContent).toContain("<img");
  });

  it("only http(s) links become links", () => {
    mount("[ok](https://example.com) [bad](javascript:alert(1))");
    const links = [...host!.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://example.com"]);
    expect(host!.textContent).toContain("bad");
  });

  it("renders code fences as code, not as markup", () => {
    mount("```ts\nconst x = 1 < 2;\n```");
    expect(host!.querySelector("pre code")!.textContent).toContain("const x = 1 < 2;");
  });
});
