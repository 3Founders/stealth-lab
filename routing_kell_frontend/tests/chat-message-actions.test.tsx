// @vitest-environment jsdom
// The copy/retry icon actions on a chat message: copy always offered, retry only when a re-run makes sense, the icons come from one family
// (components/chat/icons.tsx), and copy turns into a tick (or an alert when refused) in place, announced to screen readers, then reverts.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import MessageActions from "@/components/chat/MessageActions";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
async function mount(node: React.ReactNode) {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(node); });
  await flush();
}
const click = async (el: Element) => { await act(async () => { (el as HTMLElement).click(); }); await flush(); };
const byLabel = (t: string) => host!.querySelector(`button[aria-label="${t}"]`);
const stateOf = () => byLabel("Copy message")!.getAttribute("data-state");

const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; });

describe("message actions (copy / retry)", () => {
  it("copies the message text, and offers retry only when a re-run makes sense", async () => {
    const onCopy = vi.fn();
    const onRetry = vi.fn();
    await mount(<MessageActions copyText="hello there" onCopy={onCopy} />);
    expect(byLabel("Copy message")).not.toBeNull();
    expect(host!.querySelector(".dg-act svg")).not.toBeNull();       // an icon, not text
    await click(byLabel("Copy message")!);
    expect(onCopy).toHaveBeenCalledWith("hello there");
    expect(byLabel("Ask again")).toBeNull();                          // no retry without a re-run

    await mount(<MessageActions copyText="answer" retryText="the prompt" onCopy={onCopy} onRetry={onRetry} retryLabel="Ask @kell again" />);
    expect(byLabel("Ask @kell again")).not.toBeNull();
    await click(byLabel("Ask @kell again")!);
    expect(onRetry).toHaveBeenCalledWith("the prompt");               // retry re-runs the prompt, not the answer
  });

  it("offers Reply only when the room takes replies, and calls it", async () => {
    const onReply = vi.fn();
    await mount(<MessageActions copyText="hi" onCopy={() => true} onReply={onReply} />);
    (byLabel("Reply") as HTMLButtonElement).click();
    expect(onReply).toHaveBeenCalledTimes(1);
    await act(async () => { root!.render(<MessageActions copyText="hi" onCopy={() => true} />); });
    expect(byLabel("Reply")).toBeNull();
  });

  it("draws no retry button for an empty re-run text", async () => {
    const onRetry = vi.fn();
    await mount(<MessageActions copyText="x" retryText="" onCopy={vi.fn()} onRetry={onRetry} />);
    expect(byLabel("Ask again")).toBeNull();
    await click(byLabel("Copy message")!);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("turns into a tick, announces it, and reverts after a moment", async () => {
    await mount(<MessageActions copyText="x" onCopy={() => true} holdMs={60} />);
    expect(stateOf()).toBe("idle");
    await click(byLabel("Copy message")!);
    expect(stateOf()).toBe("copied");
    expect(host!.querySelector('[role="status"]')!.textContent).toBe("Copied");
    await wait(120);
    expect(stateOf()).toBe("idle");
    expect(host!.querySelector('[role="status"]')!.textContent).toBe("");
  });

  it("shows an alert, and says so, when the copy was refused (returned false or threw)", async () => {
    await mount(<MessageActions copyText="x" onCopy={() => false} holdMs={60} />);
    await click(byLabel("Copy message")!);
    expect(stateOf()).toBe("failed");
    expect(host!.querySelector('[role="status"]')!.textContent).toBe("Copying failed");
    await wait(120);
    expect(stateOf()).toBe("idle");

    await mount(<MessageActions copyText="y" onCopy={() => { throw new Error("blocked"); }} />);
    await click(byLabel("Copy message")!);
    expect(stateOf()).toBe("failed");
  });
});

describe("message actions (votes)", () => {
  it("draws the thumbs only with onVote, and pressing the chosen one again takes it back", async () => {
    const seen: number[] = [];
    await mount(<MessageActions copyText="x" onCopy={() => true} />);
    expect(byLabel("Good answer")).toBeNull();
    act(() => root?.unmount()); host?.remove();
    await mount(<MessageActions copyText="x" onCopy={() => true} vote={1} onVote={(v) => { seen.push(v); }} />);
    expect(byLabel("Good answer")!.getAttribute("aria-pressed")).toBe("true");
    await click(byLabel("Good answer")!);
    await click(byLabel("Bad answer")!);
    expect(seen).toEqual([0, -1]);
  });
});

describe("message actions (edit)", () => {
  it("offers Edit only with onEdit", async () => {
    let edited = 0;
    await mount(<MessageActions copyText="x" onCopy={() => true} />);
    expect(byLabel("Edit and send again")).toBeNull();
    act(() => root?.unmount()); host?.remove();
    await mount(<MessageActions copyText="x" onCopy={() => true} onEdit={() => { edited += 1; }} />);
    await click(byLabel("Edit and send again")!);
    expect(edited).toBe(1);
  });
});
