// @vitest-environment jsdom
// The header follows the SESSION: a signed-in visitor never sees "Sign in", even when the profile request fails, and gets the Delegator link.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let session: { token: string; subject: string } | null = null;
let profile: { kind: string; data?: { profile: { username: string } } } = { kind: "error" };
let notify: ((s: { token: string; subject: string } | null) => void) | null = null;

vi.mock("@/lib/session", () => ({
  getSession: () => Promise.resolve(session),
  onSessionChange: (cb: (s: { token: string; subject: string } | null) => void) => { notify = cb; return () => { notify = null; }; },
  signOut: () => Promise.resolve(),
}));
vi.mock("@/lib/kel-api", () => ({ getMyProfile: () => Promise.resolve(profile) }));
vi.mock("@/lib/analytics", () => ({ track: () => {} }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ push: () => {}, refresh: () => {} }) }));
vi.mock("next/image", () => ({ default: ({ alt }: { alt: string }) => <span>{alt}</span> }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/components/Avatar", () => ({ default: () => <span /> }));

import Header from "@/components/Header";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const mount = async () => {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<Header />); });
  await act(async () => { await Promise.resolve(); });
};
const text = () => host!.textContent ?? "";
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; notify = null; });

describe("Header sign-in state", () => {
  it("signed out: offers Sign in and no Delegator link", async () => {
    session = null;
    await mount();
    expect(text()).toContain("Sign in");
    expect(host!.querySelector('a[href="/delegator"]')).toBeNull();
  });
  it("signed in but the profile request failed: no Sign in button, an account menu, and the Delegator link", async () => {
    session = { token: "t", subject: "u" };
    profile = { kind: "error" };
    await mount();
    expect(host!.querySelector('a[href="/sign-in"]')).toBeNull();
    expect(host!.querySelector(".account-trigger")).not.toBeNull();
    expect(host!.querySelector('a[href="/delegator"]')).not.toBeNull();
  });
  it("signed in with a profile: shows the username", async () => {
    session = { token: "t", subject: "u" };
    profile = { kind: "ok", data: { profile: { username: "chait" } } };
    await mount();
    expect(text()).toContain("chait");
    expect(host!.querySelector('a[href="/sign-in"]')).toBeNull();
  });
  it("signing out brings Sign in back and removes the Delegator link", async () => {
    session = { token: "t", subject: "u" };
    profile = { kind: "error" };
    await mount();
    await act(async () => { notify!(null); });
    expect(host!.querySelector('a[href="/delegator"]')).toBeNull();
    expect(text()).toContain("Sign in");
  });
});
