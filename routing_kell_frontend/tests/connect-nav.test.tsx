// @vitest-environment jsdom
// The Connect subpages: one button per guide under the header, the current one marked, and an overview that links to each guide.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let pathname = "/connect";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("next/link", () => ({ default: ({ href, children, className, ...rest }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className} {...rest}>{children}</a> }));

import ConnectNav, { CONNECT_PAGES } from "@/components/connect/ConnectNav";
import Connect from "@/app/connect/page";
import HelpGuide from "@/app/connect/help/page";
import ClaudeCodeGuide from "@/app/connect/claude-code/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("Connect subpages", () => {
  it("has a button for the overview and each guide, in order", async () => {
    await render(<ConnectNav />);
    const links = [...host!.querySelectorAll("nav a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/connect", "/connect/claude-code", "/connect/claude", "/connect/chatgpt", "/connect/help"]);
    expect(links.map((a) => a.textContent)).toEqual(["Overview", "Claude Code", "Claude on the web", "ChatGPT", "If something goes wrong"]);
  });

  it("marks only the page you are on", async () => {
    pathname = "/connect/claude-code";
    await render(<ConnectNav />);
    const current = [...host!.querySelectorAll('nav a[aria-current="page"]')].map((a) => a.textContent);
    expect(current).toEqual(["Claude Code"]);
    pathname = "/connect";
  });

  it("the overview links to every guide and holds no instructions itself", async () => {
    await render(<Connect />);
    const hrefs = [...host!.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(CONNECT_PAGES.filter((p) => p.href !== "/connect").map((p) => p.href));
    expect(host!.textContent).not.toContain("npm install");
  });

  it("each guide holds its own content", async () => {
    await render(<ClaudeCodeGuide />);
    expect(host!.textContent).toContain("npm install -g kell-mcp");
    act(() => root?.unmount()); host?.remove();
    await render(<HelpGuide />);
    expect(host!.textContent).toContain("If something goes wrong");
    expect(host!.textContent).not.toContain("Open a terminal");
  });
});
