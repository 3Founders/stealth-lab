import { describe, expect, it } from "vitest";
import { drain, enqueue, inRoom, queueKey, removeAt } from "@/lib/chat/queue";

describe("the send queue", () => {
  const k = queueKey("#a", "@kell");
  it("keeps lines in order and hands them over as one message", () => {
    let q = enqueue({}, k, "first");
    q = enqueue(q, k, "second");
    expect(drain(q, k)).toEqual({ text: "first\n\nsecond", rest: {} });
  });
  it("draining an empty queue gives nothing and changes nothing", () => {
    const q = enqueue({}, queueKey("#a", "@scout"), "x");
    expect(drain(q, k)).toEqual({ text: null, rest: q });
  });
  it("a line can be removed, and an emptied queue disappears", () => {
    let q = enqueue(enqueue({}, k, "a"), k, "b");
    q = removeAt(q, k, 0);
    expect(inRoom(q, "#a")).toEqual([{ key: k, who: "@kell", index: 0, text: "b" }]);
    expect(removeAt(q, k, 0)).toEqual({});
  });
  it("lists only the room being looked at", () => {
    const q = enqueue(enqueue({}, k, "here"), queueKey("#b", "@kell"), "there");
    expect(inRoom(q, "#a").map((x) => x.text)).toEqual(["here"]);
  });
});
