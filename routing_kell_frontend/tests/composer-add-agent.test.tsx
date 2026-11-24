// @vitest-environment jsdom
// "+ Add agent" in the middle pane, not only the sidebar: the person is already looking at the message box, and the
// composer's placeholder has always advertised /add-agent. It must be absent in a Way's room (no handler passed), where
// the conversation is not what the person came for.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import Composer from "@/components/chat/Composer";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; });

function mount(node: React.ReactNode) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  return act(async () => { root!.render(<>{node}</>); });
}

const addButton = () => host!.querySelector<HTMLButtonElement>(".dg-addagent");

describe("+ Add agent in the message box", () => {
  it("is drawn in an ordinary chat and opens the dialog when pressed", async () => {
    let opened = 0;
    await mount(<Composer handles={["@kell"]} agentHandles={["@kell"]} onSend={() => {}} enterToSend onAddAgent={() => { opened += 1; }} />);
    const button = addButton();
    expect(button).not.toBeNull();
    expect(button!.textContent).toBe("+ Add agent");
    await act(async () => { button!.click(); });
    expect(opened).toBe(1);
  });

  it("is absent when no handler is given, as in a Way's room", async () => {
    await mount(<Composer handles={["@kell"]} agentHandles={["@kell"]} onSend={() => {}} enterToSend />);
    expect(addButton()).toBeNull();
  });

  it("sits in the meta row beside the expand control, not over the text box", async () => {
    await mount(<Composer handles={["@kell"]} agentHandles={["@kell"]} onSend={() => {}} enterToSend onAddAgent={() => {}} />);
    expect(addButton()!.parentElement?.className).toContain("dg-composer-meta");
  });
});
