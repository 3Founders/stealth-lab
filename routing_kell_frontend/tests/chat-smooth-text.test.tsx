// @vitest-environment jsdom
import React, { act } from "react";
import { describe, expect, it } from "vitest";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import SmoothText, { FADE_WINDOW, wordsOf } from "@/components/chat/SmoothText";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("SmoothText", () => {
  it("splits text into words that keep their trailing space", () => {
    expect(wordsOf("one two  three").join("")).toBe("one two  three");
    expect(wordsOf("")).toEqual([]);
  });
  it("shows the thinking indicator before any text, and fading words after", async () => {
    const host = document.createElement("div");
    const root = createRoot(host);
    await act(async () => { root.render(<SmoothText text="" />); });
    expect(host.querySelector(".dg-think")).not.toBeNull();
    expect(host.textContent).toContain("Thinking");
    await act(async () => { root.render(<SmoothText text="hello there" />); });
    // The reveal is paced by animation frames; wait (up to 3 s) for the first words rather than for a fixed time.
    for (let i = 0; i < 30 && !host.querySelector(".dg-tok"); i++) await act(async () => { await new Promise((r) => setTimeout(r, 100)); });
    expect(host.querySelector(".dg-think")).toBeNull();
    expect(host.querySelectorAll(".dg-tok").length).toBeGreaterThan(0);
    expect(host.querySelectorAll(".dg-tok").length).toBeLessThanOrEqual(FADE_WINDOW);
    await act(async () => { root.unmount(); });
  });
});
