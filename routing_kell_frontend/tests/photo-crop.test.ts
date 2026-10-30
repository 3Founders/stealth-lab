import { describe, expect, it } from "vitest";
import { MAX_ZOOM, START, VIEW, baseScale, clamp, sourceRect, zoomTo } from "@/lib/photo-crop";

describe("photo crop maths", () => {
  it("at the start the whole shorter side is shown, centred", () => {
    const r = sourceRect(START, 400, 200);
    expect(r.side).toBeCloseTo(200); expect(r.sx).toBeCloseTo(100); expect(r.sy).toBeCloseTo(0);
    expect(baseScale(400, 200)).toBeCloseTo(VIEW / 200);
  });

  it("zooming in shows a smaller part of the picture, and never beyond the limits", () => {
    expect(sourceRect(zoomTo(START, 2, 300, 300), 300, 300).side).toBeCloseTo(150);
    expect(zoomTo(START, 99, 300, 300).zoom).toBe(MAX_ZOOM);
    expect(zoomTo(START, 0.1, 300, 300).zoom).toBe(1);
    expect(zoomTo(START, Number.NaN, 300, 300).zoom).toBe(1);
  });

  it("the view stays full: dragging stops where the picture's edge meets the view's edge", () => {
    expect(clamp({ zoom: 1, x: 500, y: 500 }, 300, 300)).toEqual({ zoom: 1, x: 0, y: 0 });      // nothing to move at zoom 1 on a square
    const c = clamp({ zoom: 2, x: 9999, y: -9999 }, 300, 300);
    expect(c.x).toBeCloseTo(VIEW / 2); expect(c.y).toBeCloseTo(-VIEW / 2);
    const r = sourceRect({ zoom: 2, x: 9999, y: -9999 }, 300, 300);
    expect(r.sx).toBeCloseTo(0); expect(r.sy).toBeCloseTo(150);                             // pinned to the picture's top-left / bottom
    expect(r.sx + r.side).toBeLessThanOrEqual(300.0001); expect(r.sy + r.side).toBeLessThanOrEqual(300.0001);
  });

  it("zooming keeps the picture under the middle of the view", () => {
    const moved = zoomTo({ zoom: 2, x: 40, y: -20 }, 3, 400, 400);
    const before = sourceRect({ zoom: 2, x: 40, y: -20 }, 400, 400), after = sourceRect(moved, 400, 400);
    const mid = (r: { sx: number; sy: number; side: number }) => [r.sx + r.side / 2, r.sy + r.side / 2];
    expect(mid(after)[0]).toBeCloseTo(mid(before)[0], 0); expect(mid(after)[1]).toBeCloseTo(mid(before)[1], 0);
  });
});
