import { describe, expect, it } from "vitest";
import { captureFrom, extractCanvasBlocks, splitCanvas } from "@/lib/chat/canvas";

describe("a card for a canvas the server already holds", () => {
  const pointer = 'Made it.\n\n```canvas title="Big deck" kind=html\n\n```';
  it("is still a canvas card in the message", () => {
    const parts = splitCanvas(pointer);
    expect(parts.some((p) => p.t === "canvas" && p.block.title === "Big deck" && p.block.text === "")).toBe(true);
  });
  it("never creates an empty copy or blanks an existing canvas", () => {
    expect(captureFrom([], { id: "m1", room: "r", by: "@kell", text: pointer, at: 1 }, () => "id1")).toEqual([]);
    const have = captureFrom([], { id: "m0", room: "r", by: "@kell", text: '```canvas title="Big deck" kind=html\n<p>x</p>\n```', at: 1 }, () => "id0");
    expect(have).toHaveLength(1);
    expect(captureFrom(have, { id: "m2", room: "r", by: "@kell", text: pointer, at: 2 }, () => "id2")).toEqual([]);
  });
  it("is found so the panel can be opened at its title", () => {
    expect(extractCanvasBlocks(pointer).find((b) => !b.text.trim())?.title).toBe("Big deck");
  });
});
