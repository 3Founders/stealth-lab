// @vitest-environment jsdom
// Markdown in a chat message: rendered as elements, links only for http(s), no raw HTML, and MessageText uses it.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import Markdown from "@/components/chat/Markdown";
import MessageText from "@/components/chat/MessageText";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("markdown in chat", () => {
  it("renders headings, emphasis, code, lists, quotes and safe links as elements", async () => {
    await mount(<Markdown text={"# Title\n\n**bold** and *italic* and `code`\n\n- one\n- two\n\n> quoted\n\n[site](https://example.com)"} />);
    expect(host!.querySelector("h3")?.textContent).toBe("Title");
    expect(host!.querySelector("strong")?.textContent).toBe("bold");
    expect(host!.querySelector("em")?.textContent).toBe("italic");
    expect(host!.querySelector("code")?.textContent).toBe("code");
    expect(host!.querySelectorAll("li").length).toBe(2);
    expect(host!.querySelector("blockquote")?.textContent).toContain("quoted");
    const a = host!.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://example.com");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("never makes a javascript: link and never inserts raw HTML", async () => {
    await mount(<Markdown text={"[x](javascript:alert(1))\n\n<script>alert(2)</script>"} />);
    expect(host!.querySelector("a")).toBeNull();
    expect(host!.querySelector("script")).toBeNull();
    expect(host!.textContent).toContain("x");
    expect(host!.textContent).toContain("alert(2)");
  });

  it("renders a fenced code block", async () => {
    await mount(<Markdown text={"```\nconst x = 1;\n```"} />);
    expect(host!.querySelector("pre code")?.textContent).toContain("const x = 1;");
  });

  it("MessageText renders markdown in a message", async () => {
    await mount(<MessageText text={"**bold** and a list:\n\n1. one\n2. two"} />);
    expect(host!.querySelector("strong")?.textContent).toBe("bold");
    expect(host!.querySelectorAll("ol li").length).toBe(2);
  });
});
