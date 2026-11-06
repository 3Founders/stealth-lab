// @vitest-environment jsdom
// /desktop: the one-line PowerShell install is the page's offer, not a link to an installer file. The file stays as the
// small fallback in the install foot; the command and its Copy button are what a visitor sees first.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/analytics", () => ({ track: () => {} }));

import DesktopPage from "@/app/desktop/page";
import { DESKTOP_INSTALL_COMMAND } from "@/lib/install-commands";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;

async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<DesktopPage />); });
}

afterEach(() => { act(() => root?.unmount()); host?.remove(); root = null; host = null; });

describe("/desktop", () => {
  it("shows the one-line PowerShell command, with a copy button, as the install", async () => {
    await render();
    expect(host!.textContent).toContain(DESKTOP_INSTALL_COMMAND);
    expect(host!.querySelector("button.copy")).not.toBeNull();
  });

  it("keeps the installer file as a secondary link, not the main offer", async () => {
    await render();
    const links = [...host!.querySelectorAll("a")];
    const installer = links.find((a) => a.getAttribute("href") === "/download");
    expect(installer?.textContent).toContain("Download the installer");
  });
});
