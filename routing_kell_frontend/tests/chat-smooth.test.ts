import { describe, expect, it } from "vitest";
import { MAX_FRAME_MS, MIN_CPS, revealStep, visiblePrefix } from "@/lib/chat/smooth";

describe("smooth streaming", () => {
  it("moves at a calm pace when little is waiting, and never passes what was received", () => {
    const next = revealStep(0, 3, 16);
    expect(next).toBeGreaterThan(0);
    expect(next).toBeLessThanOrEqual(3);
    expect(revealStep(5, 5, 16)).toBe(5);
    expect(revealStep(9, 5, 16)).toBe(5);
  });

  it("goes faster the more is waiting, so it does not fall behind", () => {
    const small = revealStep(0, 20, 16) - 0;
    const big = revealStep(0, 2000, 16) - 0;
    expect(big).toBeGreaterThan(small * 5);
  });

  it("a pause in the stream does not make the text jump when it resumes, and a stalled tab reveals at most one capped frame", () => {
    const capped = revealStep(0, 100000, 60_000);
    expect(capped).toBeLessThanOrEqual(100000 * 6 * (MAX_FRAME_MS / 1000) + 1);
    expect(capped).toBeLessThan(100000);
    expect(MIN_CPS).toBeGreaterThan(0);
  });

  it("drains a backlog in well under a second", () => {
    let pos = 0;
    for (let ms = 0; ms < 1000 && pos < 600; ms += 16) pos = revealStep(pos, 600, 16);
    expect(pos).toBe(600);
  });

  it("never cuts an emoji in half", () => {
    const t = "ab😀cd";
    expect(visiblePrefix(t, 3)).toBe("ab");        // index 3 is between the two halves of 😀
    expect(visiblePrefix(t, 4)).toBe("ab😀");
    expect(visiblePrefix(t, 99)).toBe(t);
  });
});
