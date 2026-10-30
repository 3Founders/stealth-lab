// @vitest-environment jsdom
// Joining a room by invite link must survive sign-in and onboarding, which can drop the ?code= from the address.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgetInvite, recallInvite, rememberInvite } from "@/lib/chat/pending-invite";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let query = "";
const joinSpy = vi.fn(async (_code: string) => ({ title: "#team", already_member: false }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(query) }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("@/lib/session", () => ({ getAccessToken: async () => "tok", getSession: async () => ({ subject: "u1" }), onSessionChange: () => () => {} }));
vi.mock("@/lib/chat/rooms-api", () => ({ roomsApi: () => ({ join: joinSpy }) }));

import JoinRoomPage from "@/app/room/join/page";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function render() {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(<JoinRoomPage />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
}
beforeEach(() => { window.localStorage.clear(); joinSpy.mockClear(); });
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

describe("invite code memory", () => {
  it("is kept, expires after a day, and can be forgotten", () => {
    rememberInvite("abc.def", 1000);
    expect(recallInvite(1000 + 3600_000)).toBe("abc.def");
    expect(recallInvite(1000 + 25 * 3600_000)).toBe("");
    forgetInvite();
    expect(recallInvite(1001)).toBe("");
  });
});

describe("/room/join", () => {
  it("joins with the code in the address, then forgets it", async () => {
    query = "code=abc.def";
    await render();
    expect(window.localStorage.getItem("kel.pending.invite")).toContain("abc.def");
    await act(async () => { (host!.querySelector("button") as HTMLButtonElement).click(); });
    expect(joinSpy).toHaveBeenCalledWith("abc.def");
    expect(host!.textContent).toContain("You've joined #team");
    expect(recallInvite()).toBe("");
  });

  it("still joins when sign-in or onboarding dropped the code from the address", async () => {
    rememberInvite("abc.def");
    query = "";
    await render();
    expect(host!.textContent).not.toContain("missing its invite code");
    await act(async () => { (host!.querySelector("button") as HTMLButtonElement).click(); });
    expect(joinSpy).toHaveBeenCalledWith("abc.def");
  });

  it("says the link is missing its code only when there is truly none", async () => {
    query = "";
    await render();
    expect(host!.textContent).toContain("missing its invite code");
  });
});
