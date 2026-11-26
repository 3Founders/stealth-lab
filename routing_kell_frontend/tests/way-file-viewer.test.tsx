// @vitest-environment jsdom
// A step's files open in place, whatever they are: an image, a PDF, an HTML page, a table, formatted JSON or plain
// text. The frame is fixed and nothing pans or zooms (owner, 2026-11-26: "unmovable and unpannable, we should just
// have a viewer for each step's outputs if they are texts or literally any format").
import React, { act } from "react";
// @ts-ignore this app does not install @types/react-dom
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("@/lib/session", () => ({ getAccessToken: async () => "test-token" }));

import WayRunPanel from "@/components/chat/WayRunPanel";

const STEPS = [{ order: 1, title: "Build the deck", price_low_paise: 0, price_high_paise: 0 }];
const RUN = { state: "done", run: { run_id: "run-1", status: "done" }, inputs: [], submission_id: "way-1", purchase: "none", can_run: true };

let host: HTMLElement | null = null;
let root: { render(n: React.ReactNode): void; unmount(): void } | null = null;
afterEach(() => { act(() => root?.unmount()); host?.remove(); host = null; root = null; vi.unstubAllGlobals(); });

/** Mount with one step that produced the given files, then open the first one. */
async function openFile(file: Record<string, unknown>, body = "CONTENT") {
  const out = { outputs: [{ order: 1, title: "Build the deck", output: "Built.", files: [file] }], artifacts: [file], artifacts_refused: [] };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.endsWith("/run")) return { ok: true, json: async () => RUN } as unknown as Response;
    if (u.includes("/steps/cost")) return { ok: true, json: async () => ({ steps: STEPS }) } as unknown as Response;
    if (u.includes("mode=view")) return { ok: true, text: async () => body, json: async () => ({}) } as unknown as Response;
    return { ok: true, json: async () => out } as unknown as Response;
  }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => { root!.render(<WayRunPanel roomId="room-1" wayId="way-1" title="A talk" />); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
  // The Files section is the canonical place files are opened from.
  const view = [...host!.querySelectorAll("button")].find((b) => b.textContent === "View") as HTMLButtonElement;
  await act(async () => { view.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
}

const base = { name: "x.md", size_bytes: 10, text: true, url: "/v1/goals/runs/run-1/artifacts/x.md" };

describe("a step's file is shown in place", () => {
  it("an image is drawn, not dumped as bytes", async () => {
    await openFile({ ...base, name: "chart.png", media_type: "image/png", text: false });
    const img = host!.querySelector("img.dg-way-viewimg") as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute("src")).toContain("mode=view");
  });

  it("a PDF uses the browser's own viewer inside the fixed frame", async () => {
    await openFile({ ...base, name: "talk.pdf", media_type: "application/pdf", text: false });
    const embed = host!.querySelector("embed.dg-way-viewpdf");
    expect(embed).not.toBeNull();
    expect(embed!.getAttribute("type")).toBe("application/pdf");
  });

  it("an HTML page runs only inside the same sandbox the room uses", async () => {
    await openFile({ ...base, name: "deck.html", media_type: "text/html", text: true });
    const frame = host!.querySelector("iframe.dg-way-viewframe") as HTMLIFrameElement;
    expect(frame).not.toBeNull();
    // No allow-same-origin: it cannot read this page, the room or the account.
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("a CSV becomes a table with its header row", async () => {
    await openFile({ ...base, name: "rows.csv", media_type: "text/csv" }, "name,score\nAda,9\nBob,7\n");
    const heads = [...host!.querySelectorAll(".dg-way-viewtable th")].map((e) => e.textContent);
    expect(heads).toEqual(["name", "score"]);
    const cells = [...host!.querySelectorAll(".dg-way-viewtable td")].map((e) => e.textContent);
    expect(cells).toEqual(["Ada", "9", "Bob", "7"]);
  });

  it("quoted commas and doubled quotes survive the table", async () => {
    await openFile({ ...base, name: "rows.csv", media_type: "text/csv" }, 'a,b\n"x, y","he said ""hi"""\n');
    const cells = [...host!.querySelectorAll(".dg-way-viewtable td")].map((e) => e.textContent);
    expect(cells).toEqual(["x, y", 'he said "hi"']);
  });

  it("JSON is pretty-printed", async () => {
    await openFile({ ...base, name: "d.json", media_type: "application/json" }, '{"a":1,"b":[2,3]}');
    const text = host!.querySelector(".dg-way-viewsrc")!.textContent || "";
    expect(text).toContain('"a": 1');
    expect(text).toContain("\n");           // laid out, not one line
  });

  it("plain text is shown as it is", async () => {
    await openFile({ ...base, name: "notes.txt", media_type: "text/plain" }, "line one\nline two");
    expect(host!.querySelector(".dg-way-viewsrc")!.textContent).toContain("line two");
  });

  it("a file that cannot be shown says to download it, instead of pretending", async () => {
    await openFile({ ...base, name: "archive.zip", media_type: "application/zip", text: false });
    expect(host!.textContent).toContain("cannot be shown in the page");
  });

  it("a failed read names the reason", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = String(url);
      if (u.endsWith("/run")) return { ok: true, json: async () => RUN } as unknown as Response;
      if (u.includes("/steps/cost")) return { ok: true, json: async () => ({ steps: STEPS }) } as unknown as Response;
      if (u.includes("mode=view")) return { ok: false, status: 403, text: async () => "no" } as unknown as Response;
      return { ok: true, json: async () => ({ outputs: [{ order: 1, title: "x", output: "y", files: [base] }], artifacts: [base], artifacts_refused: [] }) } as unknown as Response;
    }));
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
    await act(async () => { root!.render(<WayRunPanel roomId="room-1" wayId="way-1" title="A talk" />); });
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    const view = [...host!.querySelectorAll("button")].find((b) => b.textContent === "View") as HTMLButtonElement;
    await act(async () => { view.click(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
    expect(host!.querySelector(".dg-way-viewerr")!.textContent).toContain("could not be read");
  });
});
