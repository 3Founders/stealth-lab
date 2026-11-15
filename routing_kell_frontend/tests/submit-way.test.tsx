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
    submit: rec("submit", () => submitted()),
    list: rec("list", () => []),
    get: rec("get", () => submitted()),
    fix: rec("fix", () => submitted({ status: "ai_approved", steps: submitted().steps.map((s) => ({ ...s, status: "ai_approved" as const, reason: "" })) })),
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

  it("cleans a step for the server", () => {
    expect(cleanStep({ kind: "human", description: "  do it ", prompt: "dropped", locator: "x" })).toEqual({ kind: "human", description: "do it" });
    expect(cleanStep({ kind: "automated", title: "", prompt: "p", locator: "  https://a.b " })).toEqual({ kind: "automated", prompt: "p", locator: "https://a.b" });
  });
});
