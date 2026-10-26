// .kell/areas.md: how the cheap models did in each area (file) of THIS repository, so dispatch stops paying the wait
// and the cheap models' spend where they keep losing. On a real repository a miss is not free: each turn waited the
// whole cheap-try limit (408 s) and the cheap models spent $0.24-0.36 per issue without delivering (Modal dry run on
// jsonargparse, 2026-10-15), and a series of issues often lands in the same module (5 of 10 jsonargparse issues).
//
//   AREA|<path>|wins=<n>|losses=<n>|at=<ISO time>
//
// One line per path, rewritten in place. An area is the first file lib/locate.mjs ranks for the task. Only outcomes
// that say something about the models count: a pass is a win; no pass within the limit, or every answer failing its
// checks, is a loss; a task that reads two ways, a usage limit or the user's own edit count as neither.
import fs from "node:fs";
import path from "node:path";

const FILE = "areas.md";
const HEAD = "# areas.md -- cheap-model outcomes per file of this repository, written by kell dispatch.";
const MAX_LINES = 400;

function parse(text) {
  const out = new Map();
  for (const l of String(text || "").split(/\r?\n/)) {
    const m = /^AREA\|([^|]+)\|wins=(\d+)\|losses=(\d+)\|at=([^|\s]*)/.exec(l);
    if (m) out.set(m[1], { wins: Number(m[2]), losses: Number(m[3]), at: m[4] });
  }
  return out;
}

export function readArea(root, file) {
  try { return parse(fs.readFileSync(path.join(root, ".kell", FILE), "utf8")).get(file) || null; } catch { return null; }
}

export function recordArea(root, file, won, { now = new Date() } = {}) {
  if (!root || !file || /[|\r\n]/.test(file)) return;
  const p = path.join(root, ".kell", FILE);
  let map = new Map();
  try { map = parse(fs.readFileSync(p, "utf8")); } catch { /* first record */ }
  const r = map.get(file) || { wins: 0, losses: 0 };
  map.delete(file);
  map.set(file, { wins: r.wins + (won ? 1 : 0), losses: r.losses + (won ? 0 : 1), at: now.toISOString() });
  const rows = [...map.entries()].slice(-MAX_LINES).map(([f, v]) => `AREA|${f}|wins=${v.wins}|losses=${v.losses}|at=${v.at}`);
  try { fs.writeFileSync(p, `${HEAD}\n${rows.join("\n")}\n`); } catch { /* best effort: no .kell here */ }
}
