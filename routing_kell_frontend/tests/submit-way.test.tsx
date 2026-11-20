// @vitest-environment jsdom
// Submit a Way (components/submit/SubmitWay.tsx) against a fake API: the mode from the preview, submit, a rejected step fixed on its
// own, and a run that pauses at a person's step.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubmitWay from "@/components/submit/SubmitWay";
import { cleanStep, type Submission, type SubmissionsApi } from "@/lib/submissions-api";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLDivElement;
beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); window.localStorage.clear(); host = document.createElement("div"); document.body.appendChild(host); });
afterEach(() => { act(() => root?.unmount()); root = null; host.remove(); vi.useRealTimers(); });

const flush = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const byText = (t: string) => [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(t)) as HTMLButtonElement;
const type = (el: HTMLInputElement | HTMLTextAreaElement, v: string) => {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};

const submitted = (over: Partial<Submission> = {}): Submission => ({
  id: "sub1", goal_name: "ML research", description: "", mode: "human_ai", mode_label: "Human+AI", status: "needs_changes",
  parent_procedure_id: null,
  steps: [
    { order: 1, kind: "human", title: "Copy the links", description: "Search arxiv and copy 5 links", prompt: "", locator: null,
      status: "ai_approved", reason: "", reviewer: "deepseek", revision: 1, goal_match: { decision: "same:jev", goal: "Find papers" } },
    { order: 2, kind: "automated", title: "Summarise", description: "", prompt: "summarise", locator: { type: "url", value: "https://arxiv.org/list" },
      status: "rejected", reason: "point it at the links from step 1", reviewer: "deepseek", revision: 1 },
  ],
  ...over,
});

function fakeApi(): SubmissionsApi & { calls: Record<string, unknown[][]> } {
  const calls: Record<string, unknown[][]> = {};
  const rec = <T,>(name: string, out: (...a: unknown[]) => T) => (...a: unknown[]) => { (calls[name] ||= []).push(a); return Promise.resolve(out(...a)); };
  return {
    calls,
    preview: rec("preview", () => ({ goal: "ML research", mode: "human_ai", mode_label: "Human+AI", message: "This will be submitted as Human+AI.", steps: [] })),
    refine: rec("refine", () => ({
      goal: "Research diffusion transformers in PyTorch repositories", description: "Find and summarise recent papers.",
      mode: "specialize", mode_label: "Specialized", mode_note: "narrower: one concrete case, named in the goal",
      steps: [
        { kind: "human", title: "Find papers", description: "Search arxiv for diffusion transformers and copy 5 links" },
        { kind: "automated", title: "Summarise", prompt: "Summarise each link in two lines" },
      ],
    })),
    submit: rec("submit", () => submitted()),
    list: rec("list", () => []),
    get: rec("get", () => submitted()),
    fix: rec("fix", () => submitted({ status: "ai_approved", steps: submitted().steps.map((s) => ({ ...s, status: "ai_approved" as const, reason: "" })) })),
    removeStep: rec("removeStep", () => submitted({ status: "reviewing" })),
    suggestions: rec("suggestions", () => ({ 2: [{ name: "Summarise papers", goal: "ML research", why: "does this step" }] })),
    run: rec("run", () => ({ id: "run1", status: "waiting_human", next_step: 1, error: null, outputs: [],
      waiting_on: { kind: "step", order: 1, title: "Copy the links", description: "Search arxiv and copy 5 links" } })),
    getRun: rec("getRun", () => ({})),
    done: rec("done", () => ({ id: "run1", status: "done", next_step: 3, error: null, waiting_on: null,
      outputs: [{ order: 1, title: "Copy the links", actor: "human", output: "5 links" }, { order: 2, title: "Summarise", actor: "ai", output: "two-line summaries" }] })),
  } as unknown as SubmissionsApi & { calls: Record<string, unknown[][]> };
}

describe("Submit a Way", () => {
  it("shows the mode from the steps, submits only filled steps, then a rejected step is fixed on its own and the Way runs", async () => {
    const api = fakeApi();
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} />));
    type(host.querySelector('input[placeholder^="e.g. Summarise"]') as HTMLInputElement, "ML research");
    type(host.querySelector('textarea[aria-label="What the person does in step 1"]') as HTMLTextAreaElement, "Search arxiv and copy 5 links");
    type(host.querySelector('textarea[aria-label="Prompt for step 2"]') as HTMLTextAreaElement, "summarise");
    act(() => byText("+ An automated step").click());                                  // an empty third step is not sent
    await flush(600);
    expect(host.textContent).toContain("This will be submitted as Human+AI.");
    await act(async () => { (host.querySelector("form") as HTMLFormElement).requestSubmit(); });
    await flush();
    const sent = api.calls.submit[0][0] as { steps: unknown[] };
    expect(sent.steps).toHaveLength(2);
    expect(host.textContent).toContain("point it at the links from step 1");
    expect(host.textContent).toContain("Joins kell's existing goal “Find papers”.");
    const link = host.querySelector('.sw-loc a') as HTMLAnchorElement;                  // the link on its own line, clickable
    expect(link.href).toBe("https://arxiv.org/list");
    expect(link.closest("p")!.textContent).toContain("Link:");
    expect(byText("Change this step")).toBeTruthy();                                     // an approved step can be changed too

    act(() => byText("Fix this step").click());
    type(host.querySelector('input[aria-label="Link or tool for step 2"]') as HTMLInputElement, "");
    await act(async () => { byText("Send the fix").click(); });
    await flush();
    expect(api.calls.fix[0].slice(0, 2)).toEqual(["sub1", 2]);
    expect(cleanStep(api.calls.fix[0][2] as never)).toEqual({ kind: "automated", title: "Summarise", prompt: "summarise" });   // cleared link: not sent

    await act(async () => { byText("Run").click(); });
    await flush();
    expect(host.textContent).toContain("Your turn, step 1: Copy the links");
    type(host.querySelector('textarea[aria-label="What you did or found"]') as HTMLTextAreaElement, "5 links");
    await act(async () => { byText("Done, continue").click(); });
    await flush();
    expect(api.calls.done[0]).toEqual(["run1", "5 links"]);
    expect(host.textContent).toContain("two-line summaries");
  });

  it("lists the person's open runs wherever they started, and cancels one", async () => {
    const api = fakeApi() as SubmissionsApi & { calls: Record<string, unknown[][]> };
    let open = [
      { run_id: "r1", status: "waiting_session", mode: "session", way_id: "w", way_name: "Research manuscript", input: "LLM judges",
        step: 6, of: 16, step_title: "Index the literature", started_at: null, updated_at: null },
      { run_id: "r2", status: "waiting_human", mode: "headless", way_id: "w", way_name: "Research manuscript", input: "",
        step: 3, of: 16, step_title: "Go or no-go", started_at: null, updated_at: null },
    ];
    Object.assign(api, {
      runs: vi.fn(() => Promise.resolve(open)),
      cancel: vi.fn((id: string) => { open = open.filter((r) => r.run_id !== id); return Promise.resolve({ id, status: "cancelled" }); }),
    });
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} />));
    act(() => byText("My runs").click());
    await flush();
    expect(host.textContent).toContain("Step 6 of 16: Index the literature");
    expect(host.textContent).toContain("Waiting for the AI");
    expect(host.textContent).toContain("Step 3 of 16: Go or no-go");
    const cancel = host.querySelector('button[aria-label="Cancel the run of Research manuscript"]') as HTMLButtonElement;
    act(() => cancel.click());
    await flush();
    expect((api as unknown as { cancel: ReturnType<typeof vi.fn> }).cancel).toHaveBeenCalledWith("r1");
    expect(host.textContent).not.toContain("Index the literature");
    expect(host.textContent).toContain("Go or no-go");
  });

  it("swaps two steps with the up and down buttons, keeping what was typed", () => {
    const api = fakeApi();
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} />));
    type(host.querySelector('textarea[aria-label="What the person does in step 1"]') as HTMLTextAreaElement, "first");
    type(host.querySelector('textarea[aria-label="Prompt for step 2"]') as HTMLTextAreaElement, "second");
    act(() => (host.querySelector('button[aria-label="Move step 2 up"]') as HTMLButtonElement).click());
    expect((host.querySelector('textarea[aria-label="Prompt for step 1"]') as HTMLTextAreaElement).value).toBe("second");
    expect((host.querySelector('textarea[aria-label="What the person does in step 2"]') as HTMLTextAreaElement).value).toBe("first");
    act(() => (host.querySelector('button[aria-label="Move step 1 down"]') as HTMLButtonElement).click());
    expect((host.querySelector('textarea[aria-label="What the person does in step 1"]') as HTMLTextAreaElement).value).toBe("first");
  });

  it("cleans a step for the server", () => {
    expect(cleanStep({ kind: "human", description: "  do it ", prompt: "dropped", locator: "x" })).toEqual({ kind: "human", description: "do it" });
    expect(cleanStep({ kind: "automated", title: "", prompt: "p", locator: "  https://a.b " })).toEqual({ kind: "automated", prompt: "p", locator: "https://a.b" });
  });

  it("keeps what was typed when the step's tag changes between a person and automated", async () => {
    // A regression guard: the two kinds write to different fields (description / prompt), so switching the tag used to show
    // an empty box as if the words had been deleted. They must travel with the step, and only one field may carry them,
    // because the server refuses a human step that still has a prompt.
    const api = fakeApi();
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} />));
    // Step 1 only: the form starts with a person step and an automated one, so both the box and the tag must come from the
    // same card or the test switches one step while reading another (and then proves nothing).
    const step1 = () => host.querySelectorAll("li.sw-step")[0] as HTMLLIElement;
    const box = () => step1().querySelector("textarea") as HTMLTextAreaElement;
    const tag = (who: "A person" | "Automated") =>
      [...step1().querySelectorAll('[role="radiogroup"] button')].find((b) => b.textContent?.trim() === who) as HTMLButtonElement;

    type(box(), "Search arxiv and copy the top 5 links");
    act(() => tag("Automated").click());                          // a person -> automated
    expect(box().value).toBe("Search arxiv and copy the top 5 links");

    type(box(), "summarise each link in two lines");
    expect(box().value).toBe("summarise each link in two lines");   // typed into the prompt, as kell's step
    act(() => tag("A person").click());                              // automated -> a person
    expect(box().value).toBe("summarise each link in two lines");
  });

  it("rewrites the goal and the steps, then submits both from the proposal", async () => {
    const api = fakeApi();
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} />));
    type(host.querySelector('input[placeholder^="e.g. Summarise"]') as HTMLInputElement, "ML research");
    type(host.querySelector('textarea[aria-label="What the person does in step 1"]') as HTMLTextAreaElement, "Search arxiv and copy 5 links");
    await flush(600);

    act(() => byText("Specialize").click());
    await flush();
    expect(host.textContent).toContain("Rewrite this Way");          // the rewrite has a page of its own
    expect(host.textContent).toContain("ML research");               // the draft is shown there
    act(() => byText("Rewrite").click());
    await flush();
    const draft = api.calls.refine[0][0] as { goal: string; steps: unknown[] };
    expect(draft.goal).toBe("ML research");
    expect(draft.steps).toHaveLength(1);
    expect(api.calls.refine[0].slice(1)).toEqual(["specialize", ""]);
    expect(host.textContent).toContain("Specialized: narrower");
    expect(host.textContent).toContain("Research diffusion transformers in PyTorch repositories");

    act(() => byText("Use this and submit").click());
    await flush();
    const sent = api.calls.submit[0][0] as { goal: string; steps: { title: string }[] };
    expect(sent.goal).toBe("Research diffusion transformers in PyTorch repositories");
    expect(sent.steps.map((s) => s.title)).toEqual(["Find papers", "Summarise"]);
  });

  it("rewrites a submitted Way from My submissions and files it as a new one", async () => {
    const api = fakeApi();
    root = createRoot(host);
    act(() => root!.render(<SubmitWay api={api} initial="sub1" />));
    await flush();
    expect(host.textContent).toContain("ML research");                 // the submission view, not the form

    act(() => byText("Specialize").click());
    await flush();
    expect(host.textContent).toContain("Rewrite this Way");
    act(() => byText("Rewrite").click());
    await flush();
    const draft = api.calls.refine[0][0] as { goal: string; steps: unknown[] };
    expect(draft.goal).toBe("ML research");                            // the submission's goal, with its steps
    expect(draft.steps).toHaveLength(2);
    expect(api.calls.refine[0].slice(1)).toEqual(["specialize", ""]);

    act(() => byText("Submit as a new Way").click());
    await flush();
    const sent = api.calls.submit[0][0] as { goal: string; steps: { title: string }[] };
    expect(sent.goal).toContain("PyTorch");
    expect(sent.steps.map((s) => s.title)).toEqual(["Find papers", "Summarise"]);
  });
});
