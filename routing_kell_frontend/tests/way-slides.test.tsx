// @vitest-environment jsdom
// One step per slide (owner, 2026-11-26: "for each step, there should be slide"). The step surface used to be a
// single vertical list; it is a deck now. These pin the behaviour that matters: one step is shown at a time, the
// arrows and the rail move between steps, and the slide opens on the step the run is actually on.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/lib/session", () => ({ getAccessToken: async () => "test-token" }));

import WayRunPanel from "@/components/chat/WayRunPanel";

const STEPS = [
  { order: 1, title: "Scope the talk", price_low_paise: 0, price_high_paise: 0, is_human: true },
  { order: 2, title: "Draft the argument", price_low_paise: 0, price_high_paise: 0 },
  { order: 3, title: "Build the slides", price_low_paise: 0, price_high_paise: 0 },
];

const RUN = {
  state: "done",
  run: { run_id: "run-1", status: "done" },
  inputs: [],
  submission_id: "way-1",
  purchase: "none",
  can_run: true,
};

const FULL = {
  outputs: [
    { order: 1, title: "Scope the talk", output: "The brief is set." },
    { order: 2, title: "Draft the argument", output: "The spine of the argument." },
    {
      order: 3, title: "Build the slides", output: "The deck was built.",
      files: [{ name: "talk_deck.md", size_bytes: 2048, media_type: "text/markdown", text: true, url: "/f/1" }],
    },
  ],
  artifacts: [{ name: "talk_deck.md", size_bytes: 2048, media_type: "text/markdown", text: true, url: "/f/1" }],
  artifacts_refused: [],
};

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; vi.unstubAllGlobals(); });

async function mount(run: unknown = RUN, full: unknown = FULL) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    // Order matters: "/v1/goals/runs/<id>" also contains "/run", so the room's own "/run" is matched exactly.
    const body = u.includes("/steps/cost") ? { steps: STEPS }
      : u.endsWith("/run") ? run : full;
    return { ok: true, json: async () => body } as unknown as Response;
  }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => {
    root!.render(<WayRunPanel roomId="room-1" wayId="way-1" title="A talk" />);
  });
  // Let the three requests settle.
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

const current = () => host!.querySelector(".dg-way-slide[data-current]");
const slideTitle = () => current()?.querySelector(".dg-way-slide-title")?.textContent;
const tabs = () => [...host!.querySelectorAll(".dg-way-tab")] as HTMLButtonElement[];
/** Which step the track is translated to, as a percentage offset. */
const trackOffset = () => (host!.querySelector(".dg-way-track") as HTMLElement | null)?.style.transform;

describe("a Way shows one step per slide", () => {
  it("puts the steps in a horizontal track with one step in view", async () => {
    await mount();
    // Every step is laid out side by side (that is what lets the track slide rather than jump), and exactly one is
    // the current, visible one.
    const all = [...host!.querySelectorAll(".dg-way-slide")];
    expect(all).toHaveLength(3);
    expect(host!.querySelectorAll(".dg-way-slide[data-current]")).toHaveLength(1);
    expect(host!.querySelector(".dg-way-track-frame")).not.toBeNull();
    expect(trackOffset()).toContain("translate3d");
  });

  it("has the steps along the bottom, one tab each, named", async () => {
    await mount();
    expect(tabs()).toHaveLength(3);
    expect(tabs().map((t) => t.querySelector(".dg-way-tab-t")?.textContent)).toEqual(
      ["Scope the talk", "Draft the argument", "Build the slides"],
    );
    // The strip comes after the track in the DOM, so it sits along the bottom.
    const deck = host!.querySelector(".dg-way-deck")!;
    const kids = [...deck.children].map((e) => e.className);
    expect(kids[0]).toContain("dg-way-track-frame");
    expect(kids[1]).toContain("dg-way-strip");
  });

  it("opens on the last step that finished", async () => {
    await mount();
    expect(slideTitle()).toBe("Build the slides");
    expect(current()!.querySelector(".dg-way-slide-count")!.textContent).toBe("3 / 3");
  });

  it("shows that step's own text and its own files on the slide", async () => {
    await mount();
    expect(current()!.querySelector(".dg-way-slide-out")!.textContent).toContain("The deck was built.");
    const names = [...current()!.querySelectorAll(".dg-way-slide-files .dg-way-fname")].map((e) => e.textContent);
    expect(names).toEqual(["talk_deck.md"]);
  });

  it("the arrows move one step at a time and stop at the ends", async () => {
    await mount();
    const [prev, next] = [...host!.querySelectorAll(".dg-way-arrow")] as HTMLButtonElement[];
    expect(next.disabled).toBe(true);
    expect(trackOffset()).toContain("-200%");                    // on step 3 of 3
    await act(async () => { prev.click(); });
    expect(slideTitle()).toBe("Draft the argument");
    expect(trackOffset()).toContain("-100%");
    await act(async () => { prev.click(); });
    expect(slideTitle()).toBe("Scope the talk");
    expect(trackOffset()).toContain("-0%");
    expect(prev.disabled).toBe(true);
  });

  it("the bottom strip jumps straight to a step", async () => {
    await mount();
    await act(async () => { tabs()[0].click(); });
    expect(slideTitle()).toBe("Scope the talk");
    expect(tabs()[0].getAttribute("aria-current")).toBe("step");
  });

  it("opens on a step that has real work on it, not on a one-line sign-off", async () => {
    // Step 16 of the academic-talk run is "Rehearsed once. Approved." (158 chars) while step 15 holds the gate report.
    // Landing on the sign-off made the deck look empty (owner, 2026-11-26: "I'm not able to see previous output").
    const long = "x".repeat(2000);
    await mount(RUN, {
      ...FULL,
      outputs: [
        { order: 1, title: "Scope the talk", output: long },
        { order: 2, title: "Draft the argument", output: long },
        { order: 3, title: "Build the slides", output: "Rehearsed once. Approved." },
      ],
    });
    expect(slideTitle()).toBe("Draft the argument");
    expect(current()!.querySelector(".dg-way-slide-count")!.textContent).toBe("2 / 3");
  });

  it("still opens on the last step when nothing is more than a line", async () => {
    await mount(RUN, {
      ...FULL,
      outputs: [
        { order: 1, title: "Scope the talk", output: "ok" },
        { order: 2, title: "Draft the argument", output: "ok" },
        { order: 3, title: "Build the slides", output: "ok" },
      ],
    });
    expect(slideTitle()).toBe("Build the slides");
  });

  it("opens on the step a paused run is waiting for", async () => {
    await mount(
      { ...RUN, state: "waiting_human", run: { run_id: "run-1", status: "waiting_human" },
        waiting: { order: 2, title: "Draft the argument" } },
      { ...FULL, outputs: [FULL.outputs[0]] },
    );
    expect(slideTitle()).toBe("Draft the argument");
    expect(current()!.getAttribute("data-state")).toBe("review");
  });

  it("never says 'no output yet' for a step the run DID write", async () => {
    // The owner's report: "I'm not able to see previous output, it is just saying you've got an output". Every step
    // here has text, so every step must show it -- not a placeholder.
    await mount();
    for (let i = 0; i < 3; i += 1) {
      const text = host!.querySelector(".dg-way-slide-out")!.textContent || "";
      expect(text).not.toContain("No output from this step yet");
      expect(text.trim().length).toBeGreaterThan(0);
      const [, next] = [...host!.querySelectorAll(".dg-way-arrow")] as HTMLButtonElement[];
      if (next.disabled) break;
      await act(async () => { next.click(); });
    }
  });

  it("says the run's text could not be read when that call fails, instead of pretending nothing was written", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.includes("/goals/runs/")) return { ok: false, status: 403, json: async () => ({ error: "not yours" }) } as unknown as Response;
      const body = u.includes("/steps/cost") ? { steps: STEPS } : u.endsWith("/run") ? RUN : FULL;
      return { ok: true, json: async () => body } as unknown as Response;
    }));
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    await act(async () => { root!.render(<WayRunPanel roomId="room-1" wayId="way-1" title="A talk" />); });
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    const text = host!.querySelector(".dg-way-slide-out")!.textContent || "";
    expect(text).toContain("could not be read");
    expect(text).toContain("not yours");
    expect(text).not.toContain("No output from this step yet");
  });
});
