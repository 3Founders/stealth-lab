// @vitest-environment jsdom
// An HTML artifact in a message: nothing runs until the person clicks Render, and then only inside a sandbox.
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom; the test only needs createRoot at runtime
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import MessageText from "@/components/chat/MessageText";

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
async function mount(el: React.ReactElement) {
  host = document.createElement("div"); document.body.appendChild(host);
  await act(async () => { root = createRoot(host!); root.render(el); });
}
afterEach(() => { act(() => root?.unmount()); host?.remove(); });
const text = () => host!.textContent ?? "";
const click = async (el: Element | null) => { await act(async () => { (el as HTMLElement).click(); }); };
const btn = (label: string) => [...host!.querySelectorAll("button")].find((b) => b.textContent?.includes(label)) ?? null;

const PAGE = "<!doctype html><html><body><h1>Deck</h1><script>window.__ran = true</script></body></html>";
const MESSAGE = `The deck is ready:\n\`\`\`html title="Launch deck"\n${PAGE}\n\`\`\`\nAsk me to change a slide.`;

describe("an HTML artifact in a message", () => {
  it("shows a card with its title, and no frame until Render is clicked", async () => {
    await mount(<MessageText text={MESSAGE} />);
    expect(text()).toContain("Launch deck");
    expect(text()).toContain("The deck is ready");
    expect(text()).toContain("Ask me to change a slide");
    expect(host!.querySelector("iframe")).toBeNull();
  });

  it("renders in a sandbox that cannot read this page, and says so", async () => {
    await mount(<MessageText text={MESSAGE} />);
    await click(btn("Render"));
    const frame = host!.querySelector("iframe")!;
    expect(frame).not.toBeNull();
    const sandbox = frame.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-scripts");
    expect(sandbox).not.toContain("allow-same-origin");          // no access to the app's storage or DOM
    expect(frame.getAttribute("srcdoc")).toContain("<h1>Deck</h1>");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(text()).toContain("Rendered in a sandbox");
    await click(btn("Hide"));
    expect(host!.querySelector("iframe")).toBeNull();
  });

  it("shows the exact source that was posted", async () => {
    await mount(<MessageText text={MESSAGE} />);
    await click(btn("Source"));
    expect(host!.querySelector(".dg-artifact-src")!.textContent).toContain("<script>window.__ran = true</script>");
    expect(host!.querySelector("iframe")).toBeNull();
  });
});
