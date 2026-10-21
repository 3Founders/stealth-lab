export interface Edges {
  a: Int32Array;
  b: Int32Array;
  count: number;
}

/**
 * Sparse local network: each point links only to its few nearest neighbours within
 * maxRadius, found via a uniform spatial grid instead of an O(n^2) all-pairs scan.
 * Runs once at init/resize, never inside the animation loop.
 */
export function buildLocalEdges(
  x: Float32Array,
  y: Float32Array,
  count: number,
  maxRadius: number,
  maxPerPoint: number,
): Edges {
  const cellSize = maxRadius;
  const cellOf = (v: number) => Math.floor(v / cellSize);
  const buckets = new Map<string, number[]>();
  for (let i = 0; i < count; i++) {
    const key = `${cellOf(x[i])}:${cellOf(y[i])}`;
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = [];
      buckets.set(key, bucket);
    }
    bucket.push(i);
  }

  const seen = new Set<number>();
  const a: number[] = [];
  const b: number[] = [];
  const r2 = maxRadius * maxRadius;
  const candidates: { j: number; dist: number }[] = [];

  for (let i = 0; i < count; i++) {
    const cx = cellOf(x[i]);
    const cy = cellOf(y[i]);
    candidates.length = 0;
    for (let ox = -1; ox <= 1; ox++) {
      for (let oy = -1; oy <= 1; oy++) {
        const bucket = buckets.get(`${cx + ox}:${cy + oy}`);
        if (!bucket) continue;
        for (const j of bucket) {
          if (j === i) continue;
          const dx = x[i] - x[j];
          const dy = y[i] - y[j];
          const dist2 = dx * dx + dy * dy;
          if (dist2 <= r2) candidates.push({ j, dist: Math.sqrt(dist2) });
        }
      }
    }
    candidates.sort((p, q) => p.dist - q.dist);
    let linked = 0;
    for (const c of candidates) {
      if (linked >= maxPerPoint) break;
      const lo = Math.min(i, c.j);
      const hi = Math.max(i, c.j);
      const key = lo * count + hi;
      if (seen.has(key)) continue;
      seen.add(key);
      a.push(lo);
      b.push(hi);
      linked++;
    }
  }

  return { a: Int32Array.from(a), b: Int32Array.from(b), count: a.length };
}
