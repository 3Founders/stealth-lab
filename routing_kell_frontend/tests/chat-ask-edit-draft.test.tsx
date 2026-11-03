// @vitest-environment jsdom
// "Ask kell to edit" puts one short line in the message box: it must read as plain words, never as a document and its fence.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
import Composer from "@/components/chat/Composer";
import { editRequest } from "@/components/chat/DocPanel";

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; });

describe("the draft from Ask kell to edit", () => {
  it("is a short line naming the canvas, shown in the message box as it is", async () => {
    const draft = editRequest("Crumb Bakery");
    expect(draft).toBe('@kell Edit "Crumb Bakery": ');
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    await act(async () => { root!.render(<Composer draft={{ text: draft, n: 1 }} handles={["@kell"]} agentHandles={["@kell"]} onSend={() => {}} enterToSend />); });
    const box = host.querySelector("textarea") as HTMLTextAreaElement;
    expect(box.value).toBe(draft);
    expect(box.value).not.toContain("canvas title=");
    expect(box.value.split("\n").length).toBe(1);
  });
});
