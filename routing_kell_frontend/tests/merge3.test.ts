// @vitest-environment node
import { describe, expect, it } from "vitest";
import { mapCaret, merge3 } from "@/lib/chat/merge3";

const BASE = "# Plan\n\nIntro text.\n\nStep one.\n\nStep two.\n";

describe("merge3 (the same merge the server does)", () => {
  it("changes in different lines both apply", () => {
    const r = merge3(BASE, BASE.replace("Step one.", "Step one, by me."), BASE.replace("Step two.", "Step two, by them."));
    expect(r).toEqual({ text: "# Plan\n\nIntro text.\n\nStep one, by me.\n\nStep two, by them.\n", clash: false });
  });
  it("the same line changed twice keeps yours and says so", () => {
    const r = merge3(BASE, BASE.replace("Step one.", "Mine."), BASE.replace("Step one.", "Theirs."));
    expect(r.clash).toBe(true);
    expect(r.text).toContain("Mine.");
    expect(r.text).not.toContain("Theirs.");
  });
  it("adding at the end does not lose a change above", () => {
    const r = merge3(BASE, BASE + "\nAdded.\n", BASE.replace("Step two.", "Step two, theirs."));
    expect(r).toEqual({ text: "# Plan\n\nIntro text.\n\nStep one.\n\nStep two, theirs.\n\nAdded.\n", clash: false });
  });
  it("an unchanged side takes the other", () => {
    expect(merge3(BASE, BASE, "x")).toEqual({ text: "x", clash: false });
    expect(merge3(BASE, "y", BASE)).toEqual({ text: "y", clash: false });
    expect(merge3(BASE, "z", "z")).toEqual({ text: "z", clash: false });
  });
  it("deleting a line and editing another both apply", () => {
    const r = merge3(BASE, BASE.replace("Intro text.\n\n", ""), BASE.replace("Step two.", "Step two!"));
    expect(r.text).toBe("# Plan\n\nStep one.\n\nStep two!\n");
  });
  it("an enormous document falls back to yours instead of freezing the page", () => {
    const big = Array.from({ length: 2500 }, (_, i) => `line ${i}\n`).join("");
    expect(merge3(big, big + "mine\n", big + "theirs\n")).toEqual({ text: big + "mine\n", clash: true });
  });
});

describe("mapCaret", () => {
  it("stays put before someone's edit and moves with the text after it", () => {
    expect(mapCaret("hello world", "hello brave world", 3)).toBe(3);
    expect(mapCaret("hello world", "hello brave world", 11)).toBe(17);
    expect(mapCaret("abc", "abc", 2)).toBe(2);
  });
});
