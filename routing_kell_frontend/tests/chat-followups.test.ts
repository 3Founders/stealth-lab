import { describe, expect, it } from "vitest";
import { splitNext, withoutNext } from "@/lib/chat/followups";

describe("follow-up suggestions", () => {
  it("takes the last line out and returns up to three questions", () => {
    const out = splitNext("Here is the plan.\n\n[[next: How much will it cost? | Can I do it later? | Who needs to approve? | A fourth]]");
    expect(out.text).toBe("Here is the plan.");
    expect(out.next).toEqual(["How much will it cost?", "Can I do it later?", "Who needs to approve?"]);
  });
  it("leaves ordinary text, and a token that is not at the end, alone", () => {
    expect(splitNext("no suggestions here")).toEqual({ text: "no suggestions here", next: [] });
    const mid = "see [[next: x]] and then more words";
    expect(splitNext(mid)).toEqual({ text: mid, next: [] });
  });
  it("skips empty and over-long suggestions", () => {
    expect(splitNext(`ok [[next:  | ${"x".repeat(130)} | real one ]]`).next).toEqual(["real one"]);
  });
  it("hides a suggestion line that is still arriving", () => {
    expect(withoutNext("Answer text\n\n[[ne")).toBe("Answer text");
    expect(withoutNext("Answer text\n\n[[next: How m")).toBe("Answer text");
    expect(withoutNext("Answer text\n\n[[next: a | b]]")).toBe("Answer text");
    expect(withoutNext("An array looks like [[1, 2]] in this notation")).toBe("An array looks like [[1, 2]] in this notation");
  });
});
