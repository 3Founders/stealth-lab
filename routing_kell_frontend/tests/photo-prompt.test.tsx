// @vitest-environment jsdom
// After sign-in, once per account, ask for a profile photo; never someone who already has one, never twice.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let path = "/";
let signedIn = true;
vi.mock("next/navigation", () => ({ usePathname: () => path }));
vi.mock("@/lib/session", () => ({ getSession: async () => (signedIn ? { subject: "u1", token: "t" } : null), onSessionChange: () => () => {}, getAccessToken: async () => "t" }));
vi.mock("@/lib/device-profile", () => ({ syncDevicePhoto: async () => {}, readDevicePhoto: (s: string) => window.localStorage.getItem(`kel.profile.photo.${s}`), setDevicePhoto: async () => null }));

import PhotoPrompt from "@/components/PhotoPrompt";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<PhotoPrompt />); });
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}
const shown = () => host!.querySelector("dialog")?.hasAttribute("open") ?? false;
beforeEach(() => {
  window.localStorage.clear(); path = "/"; signedIn = true;
  // jsdom has no <dialog> modal support
  (HTMLDialogElement.prototype as unknown as { showModal: () => void }).showModal = function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  (HTMLDialogElement.prototype as unknown as { close: () => void }).close = function (this: HTMLDialogElement) { this.removeAttribute("open"); };
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("profile photo question", () => {
  it("asks a signed-in person with no photo", async () => { await render(); expect(shown()).toBe(true); });

  it("does not ask someone who has a photo, a signed-out visitor, or on the sign-in page", async () => {
    window.localStorage.setItem("kel.profile.photo.u1", "data:image/jpeg;base64,AA==");
    await render(); expect(shown()).toBe(false);
  });
  it("not signed out", async () => { signedIn = false; await render(); expect(shown()).toBe(false); });
  it("not on sign-in", async () => { path = "/sign-in"; await render(); expect(shown()).toBe(false); });

  it("'Not now' is remembered, so it is asked only once", async () => {
    await render();
    await act(async () => { ([...host!.querySelectorAll("button")].find((b) => b.textContent === "Not now") as HTMLButtonElement).click(); });
    expect(shown()).toBe(false);
    expect(window.localStorage.getItem("kel.profile.photo.asked.u1")).toBe("1");
    act(() => root?.unmount()); host?.remove();
    await render(); expect(shown()).toBe(false);
  });
});
