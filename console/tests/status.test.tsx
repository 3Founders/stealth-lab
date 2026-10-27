// @vitest-environment jsdom
// The status lights: each of the MCP and the console helper shows when it is closed, without the other having to stop.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import StatusBar, { mcpState } from "@/components/StatusBar";
import type { HelperClient } from "@/lib/helper";
import type { ReadinessItem } from "@/lib/types";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); root = null; host = null; });

const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });
async function until(test: () => boolean, ms = 3000) {
  const t0 = Date.now();
  while (!test() && Date.now() - t0 < ms) await wait(15);
  if (!test()) throw new Error(`timed out; the page shows: ${host?.textContent}`);
}
const light = (name: "helper" | "mcp") => host!.querySelector(`[data-light="${name}"]`)!.textContent ?? "";

function fakeClient() {
  const s = { helper: true, mcp: true };
  const items = (): ReadinessItem[] => [
    { name: "server", ok: s.mcp, detail: s.mcp ? "answers" : "unreachable (connection refused)", fix: s.mcp ? undefined : "start it" },
    { name: "token", ok: true, detail: "saved" },
  ];
  const client = {
    health: async () => s.helper,
    readiness: async () => { if (!s.helper) throw new Error("down"); return items(); },
  } as unknown as HelperClient;
  return { s, client };
}
async function mount(client: HelperClient) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root!.render(<StatusBar client={client} helperEveryMs={20} mcpEveryMs={20} />); });
}

describe("mcpState", () => {
  it("reads the server line of the readiness list", () => {
    expect(mcpState(null).state).toBe("unknown");
    expect(mcpState([{ name: "token", ok: true, detail: "" }]).state).toBe("unknown");
    expect(mcpState([{ name: "server", ok: true, detail: "answers" }]).state).toBe("running");
    expect(mcpState([{ name: "server", ok: false, detail: "unreachable (x)" }])).toEqual({ state: "closed", detail: "unreachable (x)" });
  });
});

describe("StatusBar", () => {
  it("shows both running, then the MCP closing while the console keeps running, then coming back", async () => {
    const { s, client } = fakeClient();
    await mount(client);
    await until(() => /MCP running/.test(light("mcp")));
    expect(light("helper")).toMatch(/Console helper running/);

    s.mcp = false; // the MCP is closed; nothing else changes
    await until(() => /MCP closed/.test(light("mcp")));
    expect(light("helper")).toMatch(/Console helper running/);
    expect(host!.textContent).toMatch(/This console is still running/);
    expect(host!.textContent).toMatch(/connection refused/);

    s.mcp = true;
    await until(() => /MCP running/.test(light("mcp")));
    expect(host!.textContent).not.toMatch(/This console is still running/);
  });

  it("shows the console helper stopping, with how to start it, and recovering", async () => {
    const { s, client } = fakeClient();
    await mount(client);
    await until(() => /MCP running/.test(light("mcp")));

    s.helper = false; // the helper stops; the MCP may well be fine, but the page can no longer ask
    await until(() => /Console helper stopped/.test(light("helper")));
    expect(light("mcp")).toMatch(/MCP unknown/);
    expect(host!.textContent).toMatch(/helper has stopped/);
    expect(host!.querySelectorAll("button.copy").length).toBe(2);

    s.helper = true;
    await until(() => /Console helper running/.test(light("helper")));
    await until(() => /MCP running/.test(light("mcp")));
    expect(host!.textContent).not.toMatch(/helper has stopped/);
  });

  it("announces changes politely and is labelled", async () => {
    const { client } = fakeClient();
    await mount(client);
    const bar = host!.querySelector(".statusbar")!;
    expect(bar.getAttribute("role")).toBe("status");
    expect(bar.getAttribute("aria-live")).toBe("polite");
    expect(bar.getAttribute("aria-label")).toBeTruthy();
  });
});
