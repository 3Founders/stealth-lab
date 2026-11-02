import { describe, expect, it } from "vitest";
import { MAX_VERSIONS, addVersion, captureFrom, extractCanvasBlocks, fileNameFor, latest, newCanvas, restoreVersion, reviseRequest, splitCanvas } from "@/lib/chat/canvas";

const FENCE = "```";
const msg = `Here you go.\n\n${FENCE}canvas title="Launch plan" kind=document\n# Plan\n\n- one\n- two\n${FENCE}\n\nTell me what to change.`;

describe("canvas blocks", () => {
  it("finds a titled block, its kind, and where it sits in the message", () => {
    const [b] = extractCanvasBlocks(msg);
    expect(b.title).toBe("Launch plan");
    expect(b.kind).toBe("document");
    expect(b.text).toBe("# Plan\n\n- one\n- two");
    expect(msg.slice(b.start, b.end).startsWith(`${FENCE}canvas`)).toBe(true);
  });
  it("defaults the title and the kind, ignores an unknown kind, and reads ~~~ fences that contain code fences inside", () => {
    expect(extractCanvasBlocks(`${FENCE}canvas\nhello\n${FENCE}`)[0]).toMatchObject({ title: "Untitled", kind: "document", text: "hello" });
    expect(extractCanvasBlocks(`${FENCE}canvas title=x kind=weird\nhi\n${FENCE}`)[0].kind).toBe("document");
    const inner = extractCanvasBlocks(`~~~canvas title="Doc"\ntext\n${FENCE}js\ncode()\n${FENCE}\nmore\n~~~`)[0];
    expect(inner.text).toBe(`text\n${FENCE}js\ncode()\n${FENCE}\nmore`);
  });
  it("ordinary code fences and text without a block are not canvases", () => {
    expect(extractCanvasBlocks(`${FENCE}js\nx\n${FENCE}`)).toEqual([]);
    expect(splitCanvas("just words")).toEqual([{ t: "text", text: "just words" }]);
  });
  it("splits a message around its block", () => {
    const parts = splitCanvas(msg);
    expect(parts.map((p) => p.t)).toEqual(["text", "canvas", "text"]);
    expect((parts[0] as { text: string }).text.trim()).toBe("Here you go.");
    expect((parts[2] as { text: string }).text.trim()).toBe("Tell me what to change.");
  });
});

describe("canvas versions", () => {
  const base = newCanvas("c1", "#a", "Plan", "document", "v1", "@kell", 1000, "m1");
  it("adds a version only when the text changed and the message is new", () => {
    expect(addVersion(base, "v1", "@kell", 2000, "m2")).toBe(base);
    expect(addVersion(base, "v2", "@kell", 2000, "m1")).toBe(base);
    const next = addVersion(base, "v2", "@kell", 2000, "m2");
    expect(next.versions.map((v) => v.n)).toEqual([1, 2]);
    expect(latest(next).text).toBe("v2");
  });
  it("restoring an old version makes it the newest and keeps the rest", () => {
    let c = addVersion(base, "v2", "you", 2000);
    c = restoreVersion(c, 1, 3000);
    expect(c.versions.map((v) => v.text)).toEqual(["v1", "v2", "v1"]);
    expect(restoreVersion(c, 99)).toBe(c);
  });
  it("keeps at most the newest versions", () => {
    let c = base;
    for (let i = 2; i < MAX_VERSIONS + 20; i++) c = addVersion(c, `v${i}`, "you", i);
    expect(c.versions.length).toBe(MAX_VERSIONS);
    expect(latest(c).text).toBe(`v${MAX_VERSIONS + 19}`);
  });
});

describe("capturing from an agent's message", () => {
  const ids = () => { let n = 0; return () => `id${++n}`; };
  it("makes a canvas for a new title and a version for a title the room already has (any case), and ignores a repeat", () => {
    const m = { id: "m1", room: "#a", by: "@kell", text: msg, at: 1 };
    const first = captureFrom([], m, ids());
    expect(first.length).toBe(1);
    expect(captureFrom(first, m, ids())).toEqual([]);
    const revised = captureFrom(first, { ...m, id: "m2", text: msg.replace("- two", "- two\n- three").replace("Launch plan", "launch PLAN") }, ids());
    expect(revised.length).toBe(1);
    expect(revised[0].id).toBe(first[0].id);
    expect(latest(revised[0]).text).toContain("three");
  });
  it("another room with the same title is a different canvas", () => {
    const m = { id: "m1", room: "#a", by: "@kell", text: msg, at: 1 };
    const a = captureFrom([], m, ids());
    const b = captureFrom(a, { ...m, id: "m3", room: "#b" }, ids());
    expect(b.length).toBe(1);
    expect(b[0].room).toBe("#b");
  });
});

describe("canvas helpers", () => {
  it("asks an agent to revise with the current text, and names a download", () => {
    const c = newCanvas("c", "#a", "Launch plan", "document", `text with ${FENCE}fence${FENCE}`, "@kell");
    const r = reviseRequest(c);
    expect(r.startsWith('@kell Revise the canvas "Launch plan"')).toBe(true);
    expect(r).toContain("~~~canvas");
    expect(fileNameFor(c)).toBe("launch-plan.md");
    expect(fileNameFor(newCanvas("c", "#a", "Page!", "html", "x", "@kell"))).toBe("page.html");
  });
});
