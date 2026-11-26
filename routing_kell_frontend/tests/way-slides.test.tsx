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

const slideTitle = () => host!.querySelector(".dg-way-slide-title")?.textContent;
const rail = () => [...host!.querySelectorAll(".dg-way-rail button")] as HTMLButtonElement[];

describe("a Way shows one step per slide", () => {
  it("shows a single step, not the whole list at once", async () => {
    await mount();
    const titles = [...host!.querySelectorAll(".dg-way-slide-title")].map((e) => e.textContent);
    expect(titles).toHaveLength(1);
    expect(host!.querySelector(".dg-way-deck")).not.toBeNull();
  });

  it("opens on the last step that finished", async () => {
    await mount();
    expect(slideTitle()).toBe("Build the slides");
    expect(host!.querySelector(".dg-way-slide-count")!.textContent).toBe("3 / 3");
  });

  it("shows that step's own text and its own files on the slide", async () => {
    await mount();
    expect(host!.querySelector(".dg-way-slide-out")!.textContent).toContain("The deck was built.");
    const names = [...host!.querySelectorAll(".dg-way-slide-files .dg-way-fname")].map((e) => e.textContent);
    expect(names).toEqual(["talk_deck.md"]);
  });

  it("the arrows move one step at a time and stop at the ends", async () => {
    await mount();
    const [prev, next] = [...host!.querySelectorAll(".dg-way-arrow")] as HTMLButtonElement[];
    expect(next.disabled).toBe(true);
    await act(async () => { prev.click(); });
    expect(slideTitle()).toBe("Draft the argument");
    await act(async () => { prev.click(); });
    expect(slideTitle()).toBe("Scope the talk");
    expect(prev.disabled).toBe(true);
  });

  it("the rail jumps straight to a step", async () => {
    await mount();
    expect(rail()).toHaveLength(3);
    await act(async () => { rail()[0].click(); });
    expect(slideTitle()).toBe("Scope the talk");
    expect(rail()[0].getAttribute("aria-current")).toBe("step");
  });

  it("opens on the step a paused run is waiting for", async () => {
    await mount(
      { ...RUN, state: "waiting_human", run: { run_id: "run-1", status: "waiting_human" },
        waiting: { order: 2, title: "Draft the argument" } },
      { ...FULL, outputs: [FULL.outputs[0]] },
    );
    expect(slideTitle()).toBe("Draft the argument");
    expect(host!.querySelector(".dg-way-slide")!.getAttribute("data-state")).toBe("review");
  });
});
