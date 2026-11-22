// @vitest-environment jsdom
// The first-open explanation of the three modes: it names all three with what each does, "Got it" closes it, Escape
// closes it, and the flag that makes it "first open" only turns on when it is dismissed.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ModesIntro from "@/components/chat/ModesIntro";
import { MODES_SEEN, markModesSeen, modesSeen } from "@/lib/chat/modes-intro";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLDivElement;
beforeEach(() => { window.localStorage.clear(); host = document.createElement("div"); document.body.appendChild(host); });
afterEach(() => { act(() => root?.unmount()); root = null; host.remove(); });

function render(onClose: () => void) {
  root = createRoot(host);
  act(() => root!.render(<ModesIntro onClose={onClose} />));
}

describe("the three modes, explained on first open", () => {
  it("names Direct, Auto and Ask with what each one does", () => {
    render(() => {});
    const text = host.textContent || "";
    expect(text).toContain("Direct");
    expect(text).toContain("Auto");
    expect(text).toContain("Ask");
    expect(text).toContain("straight to the model");
    expect(text).toContain("runs the best fit on its own");
    expect(text).toContain("shows them for you to pick");
    // A Way is explained, because the modes are about what kell does with one.
    expect(text).toContain("stored procedure");
  });

  it("closes from its own button", () => {
    const onClose = vi.fn();
    render(onClose);
    const done = [...host.querySelectorAll("button")].find((b) => b.textContent === "Got it") as HTMLButtonElement;
    act(() => done.click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(onClose);
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("the flag that makes it first-open only", () => {
  it("is unseen on a new browser, seen after it is dismissed, and survives a reload", () => {
    expect(modesSeen()).toBe(false);
    markModesSeen();
    expect(modesSeen()).toBe(true);
    expect(window.localStorage.getItem(MODES_SEEN)).toBe("1");
  });

  it("treats a blocked storage as seen, so a refusal never turns into a popup on every load", () => {
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(modesSeen()).toBe(true);
    get.mockRestore();
  });
});
