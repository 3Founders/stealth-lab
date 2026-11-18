// @vitest-environment jsdom
// /desktop: the one-line install is the page's offer, not a link to an installer file. The file stays as the small
// fallback in the install foot; the command and its Copy button are what a visitor sees first. The page offers Windows and
// Linux and opens on the visitor's own system (from the user agent), so each test picks the tab it checks: jsdom's user
// agent names the machine the tests run on, and CI runs on Linux (the tests used to assume Windows, 2026-11-18).
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/analytics", () => ({ track: () => {} }));

import DesktopPage from "@/app/desktop/page";
import { DESKTOP_INSTALL_COMMAND, DESKTOP_INSTALL_COMMAND_LINUX } from "@/lib/install-commands";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;

async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<DesktopPage />); });
}

async function pick(label: "Windows" | "Linux") {
  const tab = [...host!.querySelectorAll('[role="tab"]')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
  expect(tab, `the ${label} tab`).toBeDefined();
  await act(async () => { tab!.click(); });
  expect(tab!.getAttribute("aria-selected")).toBe("true");
}

const command = () => host!.querySelector("pre")?.textContent ?? "";
const fileLink = (href: string) => [...host!.querySelectorAll("a")].find((a) => a.getAttribute("href") === href);

afterEach(() => { act(() => root?.unmount()); host?.remove(); root = null; host = null; });

describe("/desktop", () => {
  it("shows the one-line PowerShell command, with a copy button, as the Windows install", async () => {
    await render();
    await pick("Windows");
    expect(command()).toContain(DESKTOP_INSTALL_COMMAND);
    expect(host!.querySelector("button.copy")).not.toBeNull();
  });

  it("shows the one-line shell command as the Linux install", async () => {
    await render();
    await pick("Linux");
    expect(command()).toContain(DESKTOP_INSTALL_COMMAND_LINUX);
    expect(host!.querySelector("button.copy")).not.toBeNull();
  });

  it("keeps the installer file as a secondary link, not the main offer", async () => {
    await render();
    await pick("Windows");
    expect(fileLink("/download")?.textContent).toContain("Download the installer");
    await pick("Linux");
    expect(fileLink("/download?os=linux&format=deb")?.textContent).toContain("Download the .deb");
  });

  it("opens on Linux for a Linux browser and on Windows otherwise", async () => {
    const ua = vi.spyOn(navigator, "userAgent", "get")
    ua.mockReturnValue("Mozilla/5.0 (X11; Linux x86_64)")
    await render()
    expect(command()).toContain(DESKTOP_INSTALL_COMMAND_LINUX)
    act(() => root?.unmount()); host?.remove()
    ua.mockReturnValue("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    await render()
    expect(command()).toContain(DESKTOP_INSTALL_COMMAND)
    ua.mockRestore()
  });
});
