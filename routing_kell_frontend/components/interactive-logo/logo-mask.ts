import type { LogoGeometry } from "./types";

// The real, unmodified keळ mark (public/kel-mark.png, 640x640 with alpha) is the only
// geometry source here -- no vector asset exists in the repo, so contour and interior
// points are both derived from its alpha channel instead of SVG path sampling.
const SOURCE_SRC = "/kel-mark.png";
const WORK_SIZE = 400;
const ALPHA_OPAQUE = 160;
const ALPHA_EDGE = 90;

// Deterministic tiny hash (not Math.random) so the sampled point cloud is stable across
// reloads and between server/client -- "deterministic sampling rather than fully random."
function hash2(a: number, b: number): number {
  let h = (a * 374761393 + b * 668265263) | 0;
  h = (h ^ (h >>> 13)) * 1274126177;
  h = h ^ (h >>> 16);
  return ((h >>> 0) % 10000) / 10000;
}

function classify(r: number, g: number, b: number): 0 | 1 {
  return r > 190 && g > 140 && b < 150 ? 1 : 0;
}

async function loadImage(): Promise<HTMLImageElement> {
  const img = new Image();
  img.src = SOURCE_SRC;
  try {
    await img.decode();
  } catch {
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("failed to load kel-mark.png"));
    });
  }
  return img;
}

let imageDataPromise: Promise<ImageData> | null = null;
function loadImageData(): Promise<ImageData> {
  if (!imageDataPromise) {
    imageDataPromise = loadImage().then((img) => {
      const canvas = document.createElement("canvas");
      canvas.width = WORK_SIZE;
      canvas.height = WORK_SIZE;
      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(img, 0, 0, WORK_SIZE, WORK_SIZE);
      return ctx.getImageData(0, 0, WORK_SIZE, WORK_SIZE);
    });
  }
  return imageDataPromise;
}

function thin<T>(items: T[], target: number): T[] {
  if (target <= 0) return [];
  if (items.length <= target) return items;
  const stride = items.length / target;
  const out: T[] = [];
  for (let t = 0; t < target; t++) out.push(items[Math.floor(t * stride)]);
  return out;
}

type Pt = { x: number; y: number };
type TaggedPt = Pt & { isContour: 0 | 1 };

/**
 * Greedily keeps points that are at least `minDist` from every point already kept,
 * checking a full 3x3 neighbourhood of grid cells (not just "one point per cell", which
 * lets two points from adjacent cells land right next to each other). Earlier entries win,
 * so callers order `points` by priority. Without this, tightly curved regions -- a
 * stroke's rounded end cap, where the boundary loops back on itself within a small area
 * and interior fill points end up surrounded by boundary points on every side instead of
 * two straight rails -- end up with silently elevated local density: same per-point
 * neighbour cap, but so many points in so little area that the links pack into a solid
 * mesh instead of the sparse look everywhere else.
 */
function declump<T extends Pt>(points: T[], minDist: number): T[] {
  const cellSize = Math.max(1, minDist);
  const minDist2 = minDist * minDist;
  const cellOf = (v: number) => Math.floor(v / cellSize);
  const buckets = new Map<string, T[]>();
  const kept: T[] = [];
  for (const p of points) {
    const cx = cellOf(p.x);
    const cy = cellOf(p.y);
    let tooClose = false;
    for (let ox = -1; ox <= 1 && !tooClose; ox++) {
      for (let oy = -1; oy <= 1 && !tooClose; oy++) {
        const bucket = buckets.get(`${cx + ox}:${cy + oy}`);
        if (!bucket) continue;
        for (const q of bucket) {
          const dx = p.x - q.x;
          const dy = p.y - q.y;
          if (dx * dx + dy * dy < minDist2) {
            tooClose = true;
            break;
          }
        }
      }
    }
    if (tooClose) continue;
    kept.push(p);
    const key = `${cx}:${cy}`;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = [];
      buckets.set(key, bucket);
    }
    bucket.push(p);
  }
  return kept;
}

async function build(pointBudget: number): Promise<LogoGeometry> {
  const data = await loadImageData();
  const { width, height } = data;
  const px = data.data;
  const alphaAt = (x: number, y: number) => px[(y * width + x) * 4 + 3];

  // Interior: a deterministically jittered grid, kept only where the mark is opaque.
  const interior: Pt[] = [];
  const cell = Math.max(3, Math.round(width / Math.sqrt(pointBudget * 2.2)));
  for (let gy = 0; gy < height; gy += cell) {
    for (let gx = 0; gx < width; gx += cell) {
      const x = Math.min(width - 1, Math.round(gx + hash2(gx, gy) * cell));
      const y = Math.min(height - 1, Math.round(gy + hash2(gy, gx + 1) * cell));
      if (alphaAt(x, y) >= ALPHA_OPAQUE) interior.push({ x, y });
    }
  }

  // Contour: opaque pixels next to a transparent neighbour -- this also picks up the
  // inner hole of the mark's loop, not just the outer silhouette.
  const boundary: Pt[] = [];
  const step = 2;
  for (let y = step; y < height - step; y += step) {
    for (let x = step; x < width - step; x += step) {
      if (alphaAt(x, y) < ALPHA_OPAQUE) continue;
      if (
        alphaAt(x - step, y) < ALPHA_EDGE ||
        alphaAt(x + step, y) < ALPHA_EDGE ||
        alphaAt(x, y - step) < ALPHA_EDGE ||
        alphaAt(x, y + step) < ALPHA_EDGE
      ) {
        boundary.push({ x, y });
      }
    }
  }
  // Thin the boundary to an even spread (one candidate per small cell) rather than a
  // random subset, so the silhouette reads consistently all the way around. This alone
  // still lets two points from adjacent cells land close together on a tight curve, so
  // it's a coarse first pass -- declump() below enforces the real minimum spacing.
  const contourTarget = Math.round(pointBudget * 0.32);
  const contourCell = Math.max(2, Math.round(width / Math.sqrt(Math.max(1, contourTarget) * 3)));
  const byCell = new Map<string, Pt>();
  for (const p of boundary) {
    const key = `${Math.floor(p.x / contourCell)}:${Math.floor(p.y / contourCell)}`;
    if (!byCell.has(key)) byCell.set(key, p);
  }
  const contourRaw = Array.from(byCell.values());
  const interiorTrimmed = thin(interior, Math.max(0, pointBudget - contourRaw.length));

  // Merge and enforce genuine minimum spacing, contour points winning ties -- this is
  // what actually keeps rounded stroke caps from silently over-densifying (see declump's
  // docstring). minDist tracks the same scale as the interior grid itself. Measured
  // against the real asset at the current 1000-point budget / larger box size
  // (InteractiveLogo.tsx): 0.95x keeps the worst-case mutual-neighbour count at 9, zero
  // points reaching 10+, even in the tightly curved cap regions (hook tip, arm end, swirl
  // tail) that meshed solid at smaller factors.
  const minDist = Math.max(3, cell * 0.95);
  const tagged: TaggedPt[] = [
    ...contourRaw.map((p) => ({ ...p, isContour: 1 as const })),
    ...interiorTrimmed.map((p) => ({ ...p, isContour: 0 as const })),
  ];
  const declumped = declump(tagged, minDist);

  const total = declumped.length;
  const nx = new Float32Array(total);
  const ny = new Float32Array(total);
  const isContour = new Uint8Array(total);
  const colorClass = new Uint8Array(total);

  let i = 0;
  for (const p of declumped) {
    nx[i] = p.x / width;
    ny[i] = p.y / height;
    isContour[i] = p.isContour;
    const o = (p.y * width + p.x) * 4;
    colorClass[i] = classify(px[o], px[o + 1], px[o + 2]);
    i++;
  }

  return { nx, ny, isContour, colorClass, count: total };
}

const geometryCache = new Map<number, Promise<LogoGeometry>>();

/**
 * Samples the real keळ mark into contour + interior points, in normalized [0,1] source
 * space. Runs once per (rounded) point budget; every mounted InteractiveLogo reuses the
 * cached result rather than re-decoding/re-scanning the image.
 */
export function getLogoGeometry(pointBudget: number): Promise<LogoGeometry> {
  const key = Math.max(50, Math.round(pointBudget / 50) * 50);
  let p = geometryCache.get(key);
  if (!p) {
    p = build(key);
    geometryCache.set(key, p);
  }
  return p;
}
