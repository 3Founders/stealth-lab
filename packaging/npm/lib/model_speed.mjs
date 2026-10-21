// How long each cheap MODEL takes to win in THIS repo, kept in .stealth/routing.md, and what dispatch does with it.
//
// In the owner's run logs one model (minimax-m2-7) accounted for 20 of the 27 runs still going at 100 s, while gemma and gpt-oss win in about
// 32-36 s at the median. A time limit per kind of task (the L2 idea) cut wins of the slow model and the fast ones alike; what differs is the
// MODEL. So:
//   * every verified cheap answer records the seconds it took (from the start of ITS run) in a `WIN` line for its model, the last 20:
//         WIN|<model>|<scaffold>|n=<wins ever>|last=<date>|p90s=<seconds>|wins=<s,s,...>
//     The line type is this machine's own: it is not sent to the server (only ROUTE and OBS lines are) and the server's parser skips it.
//   * a run is CAPPED at clamp(3 x p90 of its model's wins, 60 s, today's limit); with fewer than 5 wins for the model, today's limit stands.
//     A capped run hands in its change (lib/exec/runtime.mjs finishRun), which is checked and judged like a finished one's.
//   * the rungs START fastest model first (median win time), so the fast models get the parallel slots. A model without enough wins is placed
//     as if it had the median of the known ones: it neither jumps the queue nor starves behind it.
// Switches: STEALTHLAB_DISPATCH_MODEL_CAP=off (the cap), STEALTHLAB_DISPATCH_FASTEST_FIRST=off (the order), STEALTHLAB_DISPATCH_CAP_MULT (3; 2 cut a gemma win on the owner's run records),
// STEALTHLAB_DISPATCH_CAP_FLOOR_S (60).
import fs from "node:fs";
import path from "node:path";
import { ROUTING_HEADER, stealthDir } from "./library.mjs";

export const WINS_MAX = 20;
export const MIN_WINS = 5;
const SEP = "|";
const COMMENT = "# WIN|<model>|<scaffold>|n=<wins>|last=<date>|p90s=<s>|wins=<s,s,...>  (seconds a verified cheap answer took; local only, the last 20)\n";

// on unless "off". Its basis is this repo's own measurements: a model's wins here; it stays off for a model until it has
// 5 of them. A model's failed tries on a task it cannot do ran the whole limit and cost more than the session's model
// doing the task (ct-series-v7 sqlglot: minimax $0.71-0.90 for 600 s, against Opus $0.49-0.53 for the turn), while its
// wins took 79-123 s; a run past 3 x its p90 win time hands in what it has instead of running on.
export const modelCapEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_MODEL_CAP || "on").toLowerCase() !== "off";
export const fastestFirstEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_FASTEST_FIRST || "on").toLowerCase() !== "off";
export const capMult = (env = process.env) => Math.max(1, Number(env.STEALTHLAB_DISPATCH_CAP_MULT) || 3);
// the cap's floor in seconds (60); STEALTHLAB_DISPATCH_CAP_FLOOR_S lowers or raises it (tests use a fraction of a second)
export const capFloorS = (env = process.env) => (env.STEALTHLAB_DISPATCH_CAP_FLOOR_S !== undefined && Number.isFinite(Number(env.STEALTHLAB_DISPATCH_CAP_FLOOR_S)) ? Number(env.STEALTHLAB_DISPATCH_CAP_FLOOR_S) : 60);

const round1 = (x) => Math.round(Number(x) * 10) / 10;
const file = (root) => path.join(stealthDir(root), "routing.md");
const esc = (s) => String(s).replace(/\|/g, "%7C").replace(/\n/g, "%0A");
const unesc = (s) => String(s).replace(/%([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)));

// nearest-rank 90th percentile and median, one decimal
export function p90(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return round1(s[Math.max(0, Math.ceil(0.9 * s.length) - 1)]);
}
export function median(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return round1(s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2);
}

const parseNums = (text) => String(text ?? "").split(",").filter((x) => x.trim() !== "" && x.trim() !== "-").map(Number).filter((x) => Number.isFinite(x) && x >= 0).slice(-WINS_MAX);

// text of routing.md -> Map "model|scaffold" -> { model, scaffold, n, wins, last }
export function parseWinLines(text) {
  const out = new Map();
  for (const raw of String(text || "").split("\n")) {
    const f = raw.replace(/\r+$/, "").split(SEP);
    if (f[0] !== "WIN" || f.length < 4) continue;
    const kv = Object.fromEntries(f.slice(3).map((x) => { const i = x.indexOf("="); return i > 0 ? [x.slice(0, i), x.slice(i + 1)] : [x, ""]; }));
    const wins = parseNums(kv.wins);
    if (!wins.length) continue;
    const model = unesc(f[1]);
    const scaffold = unesc(f[2]);
    out.set(`${model}${SEP}${scaffold}`, { model, scaffold, n: Math.max(wins.length, Number(kv.n) || 0), wins, last: kv.last && kv.last !== "-" ? kv.last : null });
  }
  return out;
}

export function renderWinLine({ model, scaffold, n, wins, last }) {
  const w = wins.slice(-WINS_MAX);
  return ["WIN", esc(model), esc(scaffold), `n=${n}`, `last=${last || "-"}`, `p90s=${p90(w)}`, `wins=${w.join(",")}`].join(SEP);
}

// One verified answer's time joins its model's last 20. Never throws: the record is best-effort.
export function recordModelWin(root, model, scaffold, seconds, { now = new Date() } = {}) {
  try {
    if (!root || !model || !Number.isFinite(seconds) || seconds < 0) return false;
    const f = file(root);
    fs.mkdirSync(path.dirname(f), { recursive: true });
    let text = "";
    try { text = fs.readFileSync(f, "utf8"); } catch { text = ROUTING_HEADER; }
    const key = `${model}${SEP}${scaffold}`;
    const cur = parseWinLines(text).get(key) || { model, scaffold, n: 0, wins: [], last: null };
    const line = renderWinLine({ model, scaffold, n: cur.n + 1, wins: [...cur.wins, round1(seconds)], last: now.toISOString().slice(0, 10) });
    let lines = text.split("\n").filter((l, i, a) => l || i < a.length - 1);
    const at = lines.findIndex((l) => { const g = l.split(SEP); return g[0] === "WIN" && unesc(g[1] || "") === model && unesc(g[2] || "") === scaffold; });
    if (at >= 0) lines[at] = line; else lines.push(line);
    if (!lines.includes(COMMENT.trimEnd())) {
      const firstData = lines.findIndex((l) => !l.startsWith("#"));
      lines.splice(firstData < 0 ? lines.length : firstData, 0, COMMENT.trimEnd());
    }
    fs.writeFileSync(f, lines.join("\n") + "\n");
    return true;
  } catch {
    return false;
  }
}

// -> { model, scaffold, n, wins, last } | null
export function modelWins(root, model, scaffold) {
  try { return parseWinLines(fs.readFileSync(file(root), "utf8")).get(`${model}${SEP}${scaffold}`) || null; } catch { return null; }
}

// clamp(mult x p90, floor, base); null (keep `baseS`) with fewer than minWins wins
export function modelCapS(wins, { baseS, floorS = 60, minWins = MIN_WINS, mult = 2 }) {
  if (!Array.isArray(wins) || wins.length < minWins) return null;
  return Math.min(baseS, Math.max(floorS, mult * p90(wins)));
}

// unit ("model|scaffold") -> median win seconds, for units with enough wins
export function modelSpeeds(root, { minWins = MIN_WINS } = {}) {
  const out = new Map();
  try {
    for (const w of parseWinLines(fs.readFileSync(file(root), "utf8")).values()) if (w.wins.length >= minWins) out.set(`${w.model}${SEP}${w.scaffold}`, median(w.wins));
  } catch { /* no routing.md */ }
  return out;
}

// rungs [{unit, model, executor}] fastest first. Known models by median win time; an unknown one is placed as the median of the known
// medians (stable: ties keep their order). Nothing known: the order is unchanged.
export function orderFastestFirst(rungs, speeds) {
  const known = rungs.map((r) => speeds.get(r.unit)).filter((x) => x !== undefined);
  if (!known.length) return rungs;
  const neutral = median(known);
  return rungs.map((r, i) => ({ r, i, s: speeds.has(r.unit) ? speeds.get(r.unit) : neutral }))
    .sort((a, b) => a.s - b.s || a.i - b.i).map((x) => x.r);
}
