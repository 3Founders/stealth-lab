// @vitest-environment jsdom
// /chat is for signed-in visitors only: signed out sees the notice, never the room; signing out hides the room.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";

type Root = { render(n: React.ReactNode): void; unmount(): void };
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let current: { token: string; subject: string } | null | "pending" = null;
let notify: ((s: { token: string; subject: string } | null) => void) | null = null;

vi.mock("@/lib/session", () => ({
  getSession: () => (current === "pending" ? new Promise(() => {}) : Promise.resolve(current)),
  onSessionChange: (cb: (s: { token: string; subject: string } | null) => void) => { notify = cb; return () => { notify = null; }; },
}));
vi.mock("@/components/chat/RoomView", () => ({ default: () => <div data-testid="room">the room</div> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import ChatPage from "@/app/chat/page";

let host: HTMLElement | null = null;
let root: Root | null = null;
const mount = async () => {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<ChatPage />); });
};
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; notify = null; });

describe("Chat sign-in gate", () => {
  it("signed out: shows a sign-in notice that returns here, and no room", async () => {
    current = null;
    await mount();
    expect(host!.textContent).toContain("Sign in to use Chat");
    expect(host!.querySelector('[data-testid="room"]')).toBeNull();
    expect(host!.querySelector('a[href="/sign-in?redirect=/chat"]')).not.toBeNull();
  });
  it("while the session is still resolving: neither the notice nor the room", async () => {
    current = "pending";
    await mount();
    expect(host!.textContent).toContain("Checking your session");
    expect(host!.textContent).not.toContain("Sign in to use Chat");
    expect(host!.querySelector('[data-testid="room"]')).toBeNull();
  });
  it("signed in: shows the room", async () => {
    current = { token: "t", subject: "u" };
    await mount();
    expect(host!.querySelector('[data-testid="room"]')).not.toBeNull();
    expect(host!.textContent).not.toContain("Sign in to use Chat");
  });
  it("signing out while the page is open removes the room", async () => {
    current = { token: "t", subject: "u" };
    await mount();
    expect(host!.querySelector('[data-testid="room"]')).not.toBeNull();
    await act(async () => { notify!(null); });
    expect(host!.querySelector('[data-testid="room"]')).toBeNull();
    expect(host!.textContent).toContain("Sign in to use Chat");
  });
});
