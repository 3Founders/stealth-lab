// @vitest-environment jsdom
// The referral-code section above /pricing's plans: it states the code's exact effect before it is used, asks the server
// (never decides on its own), remembers an applied code across a sign-in round trip, and hands the code to the page.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import ReferralCode from "@/components/billing/ReferralCode";
import type { CodeCheck } from "@/lib/billing";
import { planById } from "@/lib/plans";
import { rememberCode } from "@/lib/referral";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
let applied: ReturnType<typeof vi.fn>;
let cleared: ReturnType<typeof vi.fn>;

async function mount(check: (code: string) => Promise<CodeCheck> = async () => ({ kind: "valid" })) {
  applied = vi.fn(); cleared = vi.fn();
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => {
    root = createRoot(host!);
    root.render(<ReferralCode api={{ checkCode: vi.fn(check) as never }} pilot={planById("pilot")} onApplied={applied} onCleared={cleared} />);
  });
}
beforeEach(() => { localStorage.clear(); });
afterEach(() => { act(() => root?.unmount()); host?.remove(); root = null; localStorage.clear(); });

const text = () => host!.textContent ?? "";
const button = (label: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(label)) as HTMLButtonElement | undefined;
async function type(v: string) {
  const el = host!.querySelector("input#referral-code") as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => { host!.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await act(async () => {});
}

describe("the referral-code section", () => {
  it("asks for a code, with no pre-use paragraph, and keeps Apply off until something is typed", async () => {
    await mount();
    expect(text()).toContain("Have a code?");
    expect(text()).not.toContain("A code unlocks one plan for one person");
    expect(text()).not.toContain("Without a code the plans below");
    expect(host!.querySelector("input#referral-code")).not.toBeNull();
    expect(button("Apply")!.disabled).toBe(true);                          // nothing to check until something is typed
  });

  it("a valid code is applied, remembered and handed to the page (the field keeps the case as typed)", async () => {
    const check = vi.fn(async (_code: string) => ({ kind: "valid" } as CodeCheck));
    await mount(check);
    await type("Firsttrial");
    await submit();
    expect(check).toHaveBeenCalledWith("Firsttrial");                      // sent as typed; the server matches ignoring case
    expect(applied).toHaveBeenCalledWith("FIRSTTRIAL");
    expect(text()).toContain("Code FIRSTTRIAL applied.");
    expect(text()).toContain("the one plan it unlocks: Pilot, ₹649 for 30 days");
    expect(localStorage.getItem("kel.referral.code")).toBe("FIRSTTRIAL");
    expect(host!.querySelector("input")).toBeNull();                       // the field gives way to the applied state
  });

  it("an unrecognised code shows the server's sentence and applies nothing", async () => {
    await mount(async () => ({ kind: "error", message: "we do not recognise that code; check it and try again" }));
    await type("NOPE");
    await submit();
    expect(text()).toContain("we do not recognise that code; check it and try again");
    expect(applied).not.toHaveBeenCalled();
    expect(localStorage.getItem("kel.referral.code")).toBeNull();
  });

  it("while billing is off, it says the code cannot be checked yet", async () => {
    await mount(async () => ({ kind: "off" }));
    await type("FRIEND");
    await submit();
    expect(text()).toContain("We can't check codes yet: buying plans is not switched on. Nothing is charged.");
    expect(applied).not.toHaveBeenCalled();
  });

  it("a code applied before is applied again on the next visit (the sign-in round trip)", async () => {
    rememberCode("friend");
    await mount();
    await act(async () => {});
    expect(applied).toHaveBeenCalledWith("FRIEND");
    expect(text()).toContain("Code FRIEND applied.");
    expect(host!.querySelector("input")).toBeNull();
  });

  it("removing the code forgets it and tells the page", async () => {
    rememberCode("FRIEND");
    await mount();
    await act(async () => {});
    await act(async () => { button("Remove code")!.click(); });
    expect(cleared).toHaveBeenCalled();
    expect(localStorage.getItem("kel.referral.code")).toBeNull();
    expect(host!.querySelector("input#referral-code")).not.toBeNull();
  });

  it("a whitespace-only code is not sent", async () => {
    const check = vi.fn(async (_code: string) => ({ kind: "valid" } as CodeCheck));
    await mount(check);
    await type("   ");
    await submit();
    expect(check).not.toHaveBeenCalled();
  });
});
