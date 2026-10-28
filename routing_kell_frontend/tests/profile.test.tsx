// @vitest-environment jsdom
// The profile page: add or change a photo, edit username / about line / visibility, honest states. Pure form rules first.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AVATAR_MAX_BYTES, avatarProblem, profileChanges } from "@/lib/profile-form";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("profile form rules", () => {
  it("accepts PNG, JPEG and WebP up to 5 MB and says why otherwise", () => {
    expect(avatarProblem({ type: "image/png", size: 1000 })).toBeNull();
    expect(avatarProblem({ type: "image/webp", size: AVATAR_MAX_BYTES })).toBeNull();
    expect(avatarProblem({ type: "image/gif", size: 1000 })).toMatch(/PNG, JPEG or WebP/);
    expect(avatarProblem({ type: "image/png", size: AVATAR_MAX_BYTES + 1 })).toMatch(/over 5 MB/);
    expect(avatarProblem({ type: "image/png", size: 0 })).toMatch(/empty/);
  });
  it("sends only the fields that changed, and nothing when nothing did", () => {
    const saved = { username: "ada", tagline: "hello", visibility: "private" as const };
    expect(profileChanges(saved, { username: "ada", tagline: "hello", visibility: "private" })).toBeNull();
    expect(profileChanges(saved, { username: "ada ", tagline: " hello ", visibility: "private" })).toBeNull(); // whitespace only
    expect(profileChanges(saved, { username: "ada", tagline: "new line", visibility: "private" })).toEqual({ tagline: "new line" });
    expect(profileChanges(saved, { username: "grace", tagline: "hello", visibility: "public" })).toEqual({ username: "grace", visibility: "public" });
    expect(profileChanges({ username: "ada", tagline: "x", visibility: "private" }, { username: "ada", tagline: "", visibility: "private" })).toEqual({ tagline: "" }); // clearing is a change
    expect(profileChanges({ username: null, tagline: null, visibility: "private" }, { username: "", tagline: "", visibility: "private" })).toBeNull();
  });
});

let session: { token: string; subject: string } | null = { token: "t", subject: "u" };
const api = {
  getMyProfile: vi.fn(), updateMyProfile: vi.fn(), uploadAvatar: vi.fn(), removeAvatar: vi.fn(),
};
vi.mock("@/lib/session", () => ({ getSession: () => Promise.resolve(session), onSessionChange: () => () => {} }));
vi.mock("@/lib/kel-api", () => ({
  getMyProfile: (...a: unknown[]) => api.getMyProfile(...a), updateMyProfile: (...a: unknown[]) => api.updateMyProfile(...a),
  uploadAvatar: (...a: unknown[]) => api.uploadAvatar(...a), removeAvatar: (...a: unknown[]) => api.removeAvatar(...a),
}));
vi.mock("@/components/AnimatedHeading", () => ({ default: ({ children }: { children: React.ReactNode }) => <span>{children}</span> }));
vi.mock("@/components/Avatar", () => ({ default: ({ username }: { username: string }) => <span data-testid="avatar">{username}</span> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }));

import ProfilePage from "@/app/account/profile/page";

const profile = (over: Record<string, unknown> = {}) => ({
  kind: "ok" as const,
  data: { profile: { user_id: "u", visibility: "private", disclosed_at: null, tagline: "Building things", username: "ada", avatar_locator: null, onboarding_complete: true, t_created: "", t_updated: "", ...over }, counts: {}, display_name: "Ada", avatar_url: null, disclosure_required: false, onboarding_required: false, is_reviewer: false },
});

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
async function mount() {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(<ProfilePage />); });
  await flush(); await flush();
}
const byText = (t: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(t))!;
const fill = async (el: HTMLInputElement | HTMLTextAreaElement, v: string) => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); });
};
const choose = async (f: File) => {
  const input = host!.querySelector<HTMLInputElement>("#p-photo")!;
  Object.defineProperty(input, "files", { configurable: true, value: [f] });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  await flush();
};
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; session = { token: "t", subject: "u" }; Object.values(api).forEach((m) => m.mockReset()); });

describe("Profile page", () => {
  it("signed out: a sign-in notice that returns here, no form", async () => {
    session = null;
    await mount();
    expect(host!.textContent).toContain("Sign in to see your profile");
    expect(host!.querySelector('a[href="/sign-in?redirect=/account/profile"]')).not.toBeNull();
    expect(host!.querySelector("form")).toBeNull();
    expect(api.getMyProfile).not.toHaveBeenCalled();
  });

  it("shows the saved photo, username, about line and visibility", async () => {
    api.getMyProfile.mockResolvedValue(profile({ visibility: "public" }));
    await mount();
    expect((host!.querySelector("#p-username") as HTMLInputElement).value).toBe("ada");
    expect((host!.querySelector("#p-tagline") as HTMLTextAreaElement).value).toBe("Building things");
    expect((host!.querySelector('input[name="visibility"]:checked') as HTMLInputElement).parentElement!.textContent).toContain("Anyone");
    expect(host!.textContent).toContain("Add a photo");
    for (const el of host!.querySelectorAll<HTMLInputElement>("input, textarea")) expect(el.labels?.length || el.getAttribute("aria-label"), el.id).toBeTruthy();
  });

  it("Save is off until something changes, then sends only what changed", async () => {
    api.getMyProfile.mockResolvedValue(profile());
    api.updateMyProfile.mockResolvedValue(profile({ tagline: "New line" }));
    await mount();
    expect(byText("Save changes").disabled).toBe(true);
    await fill(host!.querySelector("#p-tagline") as HTMLTextAreaElement, "New line");
    expect(byText("Save changes").disabled).toBe(false);
    await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(api.updateMyProfile).toHaveBeenCalledWith({ tagline: "New line" });
    expect(host!.textContent).toContain("Saved.");
  });

  it("shows the server's reason when a save is refused", async () => {
    api.getMyProfile.mockResolvedValue(profile());
    api.updateMyProfile.mockResolvedValue({ kind: "error", message: "That username is taken." });
    await mount();
    await fill(host!.querySelector("#p-username") as HTMLInputElement, "taken");
    await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await flush();
    expect(host!.querySelector('[role="alert"]')!.textContent).toBe("That username is taken.");
  });

  it("a photo that is too big or the wrong type is refused before any upload", async () => {
    api.getMyProfile.mockResolvedValue(profile());
    await mount();
    await choose(new File([new Uint8Array(AVATAR_MAX_BYTES + 1)], "big.png", { type: "image/png" }));
    expect(host!.querySelector('[role="alert"]')!.textContent).toMatch(/over 5 MB/);
    await choose(new File(["x"], "anim.gif", { type: "image/gif" }));
    expect(host!.querySelector('[role="alert"]')!.textContent).toMatch(/PNG, JPEG or WebP/);
    expect(api.uploadAvatar).not.toHaveBeenCalled();
  });

  it("uploads a good photo, then offers Change photo and Remove; Remove clears it", async () => {
    api.getMyProfile.mockResolvedValue(profile());
    api.uploadAvatar.mockResolvedValue(profile({ avatar_locator: "avatars/u.png" }));
    api.removeAvatar.mockResolvedValue(profile());
    await mount();
    await choose(new File(["png-bytes"], "me.png", { type: "image/png" }));
    expect(api.uploadAvatar).toHaveBeenCalledTimes(1);
    expect(host!.textContent).toContain("Change photo");
    await act(async () => { byText("Remove").click(); });
    await flush();
    expect(api.removeAvatar).toHaveBeenCalledTimes(1);
    expect(host!.textContent).toContain("Add a photo");
  });

  it("when the profile server cannot be reached it says so and offers Try again", async () => {
    api.getMyProfile.mockResolvedValueOnce({ kind: "error", message: "Could not reach the backend." }).mockResolvedValue(profile());
    await mount();
    expect(host!.querySelector('[role="alert"]')!.textContent).toContain("Could not reach the backend.");
    await act(async () => { byText("Try again").click(); });
    await flush();
    expect(host!.querySelector("#p-username")).not.toBeNull();
  });
});
