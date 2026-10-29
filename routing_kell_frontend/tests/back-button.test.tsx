// @vitest-environment jsdom
// The Back control: a real link to "/" that goes back in history only after the visitor has moved around this site.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let path = "/contact";
const back = vi.fn();
vi.mock("next/navigation", () => ({ usePathname: () => path, useRouter: () => ({ back, push: () => {} }) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => <a href={href} {...rest}>{children}</a> }));

import BackButton from "@/components/BackButton";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const render = async () => { await act(async () => { root!.render(<BackButton />); }); };
const mount = async () => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); await render(); };
const link = () => host!.querySelector<HTMLAnchorElement>("a.back-btn");
const click = (init: MouseEventInit = {}) => {
  const e = new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init });
  act(() => { link()!.dispatchEvent(e); });
  return e;
};
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; path = "/contact"; back.mockReset(); });

describe("Back control", () => {
  it("opened directly (a new tab or a shared link): a link to the homepage that says so, and a click is left to the browser", async () => {
    await mount();
    expect(link()!.getAttribute("href")).toBe("/");
    expect(link()!.textContent).toBe("Back to home");
    expect(click().defaultPrevented).toBe(false);
    expect(back).not.toHaveBeenCalled();
  });
  it("after moving between pages of this site: says Back and goes back in history", async () => {
    path = "/";
    await mount();
    expect(link()).toBeNull(); // not shown on the homepage
    path = "/contact";
    await render();
    expect(link()!.textContent).toBe("Back");
    expect(click().defaultPrevented).toBe(true);
    expect(back).toHaveBeenCalledTimes(1);
  });
  it("a new-tab or modified click is never intercepted, even with history", async () => {
    path = "/";
    await mount();
    path = "/docs";
    await render();
    for (const init of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }]) {
      expect(click(init).defaultPrevented).toBe(false);
    }
    expect(back).not.toHaveBeenCalled();
  });
  it("is absent on the homepage and on the full-screen Chat", async () => {
    path = "/chat";
    await mount();
    expect(link()).toBeNull();
  });
  it("inside the Chat it is shown in the sidebar (the inline copy), while the floating one stays hidden there", async () => {
    path = "/chat";
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    await act(async () => { root!.render(<><BackButton /><BackButton inline /></>); });
    const links = host.querySelectorAll("a.back-btn");
    expect(links).toHaveLength(1);
    expect(links[0].classList.contains("back-btn--inline")).toBe(true);
    expect(links[0].textContent).toBe("Back to home");
  });
  it("the arrow is hidden from assistive technology and the visible text is the accessible name", async () => {
    await mount();
    expect(link()!.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
    expect(link()!.getAttribute("aria-label")).toBeNull();
  });
});
