// p50 / p95 / max (and n) of each routing part, from routing_timing.jsonl files (lib/routing_timing.mjs).
//   node scripts/summarize_routing_timing.mjs <file.jsonl>... [--by label,kind]
import fs from "node:fs";

const files = process.argv.slice(2).filter((a, i, all) => !a.startsWith("--") && all[i - 1] !== "--by");
const byIdx = process.argv.indexOf("--by");
const by = (byIdx > 0 ? process.argv[byIdx + 1] : "label,kind").split(",");
const PARTS = ["triage_ms", "find_ways_ms", "plan_ms", "gate_ms", "brief_ms", "locate_ms", "setup_ms", "routing_ms", "process_ms"];

const rows = files.flatMap((f) => fs.readFileSync(f, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)));
const pct = (xs, p) => { const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1)]; };
const groups = new Map();
for (const r of rows) {
  const k = by.map((f) => r[f] ?? "-").join(" / ");
  if (!groups.has(k)) groups.set(k, []);
  groups.get(k).push(r);
}
for (const [k, rs] of groups) {
  const outcomes = {};
  for (const r of rs) { const o = r.outcome || (r.dispatched ? "dispatched" : "-"); outcomes[o] = (outcomes[o] || 0) + 1; }
  console.log(`\n## ${k}  (n=${rs.length}; ${Object.entries(outcomes).map(([o, c]) => `${o}: ${c}`).join(", ")})`);
  console.log("| part | n | p50 | p95 | max |\n|---|---|---|---|---|");
  for (const p of PARTS) {
    const xs = rs.map((r) => r[p]).filter((x) => Number.isFinite(x));
    if (xs.length) console.log(`| ${p} | ${xs.length} | ${pct(xs, 50)} | ${pct(xs, 95)} | ${Math.max(...xs)} |`);
  }
  const noVerdict = rs.filter((r) => "triage" in r && r.triage === null).length;
  if (noVerdict) console.log(`(triage gave no verdict on ${noVerdict} of ${rs.length})`);
}
