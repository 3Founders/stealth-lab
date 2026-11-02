/**
 * Three-way merge of text, line by line, for shared documents. `mine` is what you typed since `base`; `theirs` is what someone else saved since
 * `base`. Changes in different lines both apply; if both changed the same lines, yours win those lines. The server does the same merge when
 * two saves cross (backend/app/services/room_docs.py merge3), so the screen and the server agree.
 *
 * Honest limit: it works on lines. A paragraph written as one long line is one unit, so two people typing in the same paragraph at once is a
 * clash (yours wins that paragraph; the other version stays in history). Very long documents (over `MAX_LINES` lines) fall back to yours.
 */
export const MAX_LINES = 1800;

interface Hunk { a1: number; a2: number; repl: string[] }

const lines = (t: string): string[] => t.match(/[^\n]*\n|[^\n]+$/g) ?? [];

/** The changed stretches of `a` that turn it into `b`, by longest common subsequence of lines. */
function hunks(a: string[], b: string[]): Hunk[] {
  const n = a.length, m = b.length;
  const w = m + 1;
  const dp = new Uint16Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
  const out: Hunk[] = [];
  let i = 0, j = 0, open: Hunk | null = null;
  const flush = () => { if (open) { out.push(open); open = null; } };
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { flush(); i++; j++; continue; }
    open ??= { a1: i, a2: i, repl: [] };
    if (j < m && (i >= n || dp[i * w + j + 1] >= dp[(i + 1) * w + j])) { open.repl.push(b[j]); j++; }
    else { i++; open.a2 = i; }
  }
  flush();
  return out;
}

const touches = (x: Hunk, y: Hunk): boolean => {
  if (x.a1 === x.a2 && y.a1 === y.a2) return x.a1 === y.a1;
  if (x.a1 === x.a2) return y.a1 < x.a1 && x.a1 < y.a2;
  if (y.a1 === y.a2) return x.a1 < y.a1 && y.a1 < x.a2;
  return x.a1 < y.a2 && y.a1 < x.a2;
};

/** The one stretch of `a` that, replaced by `text`, turns it into `b` (the common start and end are kept). */
function span(a: string, b: string): { s: number; e: number; text: string } {
  const n = Math.min(a.length, b.length);
  let p = 0;
  while (p < n && a[p] === b[p]) p++;
  let q = 0;
  while (q < n - p && a[a.length - 1 - q] === b[b.length - 1 - q]) q++;
  return { s: p, e: a.length - q, text: b.slice(p, b.length - q) };
}

/**
 * Character-level merge, like two people typing in different places of one paragraph: each side's change is one replaced stretch of `base`;
 * when the stretches do not overlap, both apply exactly (two insertions at one point: theirs first, then yours). Null when they overlap.
 * The server does the same (backend room_docs.merge_chars), so the screen and the saved text agree.
 */
export function mergeChars(base: string, mine: string, theirs: string): string | null {
  const m = span(base, mine), t = span(base, theirs);
  const mi = m.s === m.e, ti = t.s === t.e;
  if (mi && ti) { /* two insertions: both fit */ }
  else if (mi) { if (t.s < m.s && m.s < t.e) return null; }
  else if (ti) { if (m.s < t.s && t.s < m.e) return null; }
  else if (m.s < t.e && t.s < m.e) return null;
  const edits = [{ ...m, r: 0 }, { ...t, r: 1 }].sort((x, y) => y.s - x.s || x.r - y.r);
  let out = base;
  for (const e of edits) out = out.slice(0, e.s) + e.text + out.slice(e.e);
  return out;
}

export function merge3(base: string, mine: string, theirs: string): { text: string; clash: boolean } {
  if (mine === theirs) return { text: mine, clash: false };
  if (base === theirs) return { text: mine, clash: false };
  if (base === mine) return { text: theirs, clash: false };
  const exact = mergeChars(base, mine, theirs);
  if (exact !== null) return { text: exact, clash: false };
  const b = lines(base), m = lines(mine), t = lines(theirs);
  if (b.length > MAX_LINES || m.length > MAX_LINES || t.length > MAX_LINES) return { text: mine, clash: true };
  const hm = hunks(b, m), ht = hunks(b, t);
  let clash = false;
  const keep: Hunk[] = [];
  for (const h of ht) { if (hm.some((x) => touches(x, h))) clash = true; else keep.push(h); }
  const out = [...b];
  for (const h of [...hm, ...keep].sort((x, y) => y.a1 - x.a1 || y.a2 - x.a2)) out.splice(h.a1, h.a2 - h.a1, ...h.repl);
  return { text: out.join(""), clash };
}

/** Where a caret at `caret` in `before` belongs in `after`, when `after` is `before` with someone else's edit: it moves with the text around it. */
export function mapCaret(before: string, after: string, caret: number): number {
  let p = 0;
  const max = Math.min(before.length, after.length);
  while (p < max && before[p] === after[p]) p++;
  if (caret <= p) return caret;
  let s = 0;
  while (s < max - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  if (caret >= before.length - s) return Math.max(0, caret + (after.length - before.length));
  return Math.min(after.length, p);
}
