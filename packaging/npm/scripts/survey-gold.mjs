#!/usr/bin/env node
// Gold-set evaluation of `stealthlab-mcp survey` (docs/plan_2026-10_priors_library_survey.md §4.5).
//
//   node scripts/survey-gold.mjs <cache-dir> [--only id,id] [--local-root <path>]
//
// Clones each repository in survey-gold-repos.json (--depth 1) into <cache-dir> (reused if present), runs
// the scanner, and scores it against survey-gold-truth.json, which was written by hand from each
// repository's own configuration and docs:
//   * units:     the exact set of unit paths a correct scanner finds            -> unit precision / recall
//   * key facts: run / test / build commands and runtime versions, each a list of acceptable regexes over
//                the scanner's fact statements                                    -> key-fact recall
// Writes <cache-dir>/gold-report.json and <cache-dir>/gold-facts-sample.jsonl (a seeded random sample of
// scanner facts with the cited line, for hand-checking precision).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runSurvey } from "../lib/survey/survey.mjs";
import { claimsOf, kvGet, readPage } from "../lib/survey/claims.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
// --set heldout reads survey-heldout-truth.json; repos come from the truth keys (cloned into <cache-dir>/<id>).
const set = args.includes("--set") ? args[args.indexOf("--set") + 1] : "gold";
const truth = JSON.parse(fs.readFileSync(path.join(HERE, `survey-${set}-truth.json`), "utf8"));
const repos = set === "gold" ? JSON.parse(fs.readFileSync(path.join(HERE, "survey-gold-repos.json"), "utf8")).repos
  : Object.keys(truth).filter((k) => !k.startsWith("_")).map((id) => ({ id, url: "cached", shape: "held-out" }));
const cache = path.resolve(args[0] || "gold-cache");
const only = args.includes("--only") ? new Set(args[args.indexOf("--only") + 1].split(",")) : null;
const localRoot = args.includes("--local-root") ? args[args.indexOf("--local-root") + 1] : null;
fs.mkdirSync(cache, { recursive: true });

function allClaims(dir) {
  const sdir = path.join(dir, ".stealth");
  const out = claimsOf(readPage(path.join(sdir, "claims.md")));
  try { for (const f of fs.readdirSync(path.join(sdir, "claims"))) out.push(...claimsOf(readPage(path.join(sdir, "claims", f)))); } catch { /* single page */ }
  return out;
}

/** Truth units from the spec, expanded independently of the scanner (git's pathspec matcher, plain regexes). */
function truthUnits(dir, t) {
  if (t.units) return new Set(t.units);
  const out = new Set(["."]);
  const excl = t.exclude ? new RegExp(t.exclude) : null;
  if (t.manifests) {
    const ls = execFileSync("git", ["ls-files", "-z", "--", ...t.manifests.map((p) => `:(glob)${p}`)], { cwd: dir, encoding: "utf8", maxBuffer: 1 << 28 });
    for (const f of ls.split("\0").filter(Boolean)) {
      const d = path.posix.dirname(f);
      if (!excl || !excl.test(d)) out.add(d === "" ? "." : d);
    }
  }
  const lsGlob = (globs) => !globs.length ? [] : execFileSync("git", ["ls-files", "-z", "--", ...globs.map((p) => `:(glob)${p}`)], { cwd: dir, encoding: "utf8", maxBuffer: 1 << 28 }).split("\0").filter(Boolean);
  for (const f of lsGlob(t.extraManifests || [])) out.add(path.posix.dirname(f));
  for (const f of lsGlob(t.xcodeprojParents || [])) out.add(path.posix.dirname(path.posix.dirname(f)));
  for (const u of t.extraUnits || []) out.add(u);
  if (t.excludeMarkers) {
    for (const u of [...out]) {
      const pj = path.join(dir, u, "package.json");
      if (u === "." || !fs.existsSync(pj)) continue;
      try {
        const keys = Object.keys(JSON.parse(fs.readFileSync(pj, "utf8"))).filter((k) => !["type", "private", "main", "module", "types", "exports", "sideEffects"].includes(k));
        if (!keys.length) out.delete(u);
      } catch { /* unparseable: keep */ }
    }
  }
  if (t.gradleIncludes) {
    const s = fs.readFileSync(path.join(dir, t.gradleIncludes), "utf8").replace(/\/\/.*$/gm, "");
    for (const m of s.matchAll(/include\(\s*"([^"]+)"\s*\)/g)) out.add(m[1].replace(/^:/, "").replace(/:/g, "/"));
    for (const m of s.matchAll(/includeBuild\(\s*"([^"]+)"\s*\)/g)) out.add(m[1].replace(/^\.\//, ""));
  }
  if (t.pubspecWorkspace) {
    const lines = fs.readFileSync(path.join(dir, t.pubspecWorkspace), "utf8").split(/\r?\n/);
    let on = false;
    for (const l of lines) {
      if (/^workspace:\s*$/.test(l)) { on = true; continue; }
      if (on && /^\S/.test(l)) break;
      const m = on && l.match(/^\s*-\s*(\S+)/);
      if (m) out.add(m[1]);
    }
  }
  return out;
}

// Seeded PRNG so the precision sample is reproducible.
let seed = args.includes("--seed") ? Number(args[args.indexOf("--seed") + 1]) : 20261007;
const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);

const report = [];
const sample = [];
for (const r of repos) {
  if (only && !only.has(r.id)) continue;
  const dir = r.url === "local" ? (localRoot || null) : path.join(cache, r.id);
  if (!dir) continue;
  if (r.url !== "local" && r.url !== "cached" && !fs.existsSync(path.join(dir, ".git"))) {
    execFileSync("git", ["clone", "-q", "--depth", "1", r.url, dir], { stdio: "inherit" });
  }
  fs.rmSync(path.join(dir, ".stealth"), { recursive: true, force: true });
  const t0 = Date.now();
  const s = runSurvey(dir, { history: false });
  const ms = Date.now() - t0;
  const claims = allClaims(dir);
  const statements = claims.map((c) => c.statement);
  const t = truth[r.id] || {};
  const found = new Set(s.units.map((u) => u.path));
  const want = truthUnits(dir, t);
  const unitTp = [...found].filter((u) => want.has(u)).length;
  const keys = (t.keys || []).map((k) => ({ ...k, hit: statements.find((st) => k.any.some((re) => new RegExp(re, "i").test(st))) || null }));
  report.push({
    id: r.id, shape: r.shape, ms, files: s.listing.files, units_found: [...found].sort(), units_truth: [...want].sort(),
    unit_precision: found.size ? unitTp / found.size : 1, unit_recall: want.size ? unitTp / want.size : 1,
    missing_units: [...want].filter((u) => !found.has(u)), extra_units: [...found].filter((u) => !want.has(u)),
    keys_total: keys.length, keys_found: keys.filter((k) => k.hit).length, keys_missed: keys.filter((k) => !k.hit).map((k) => k.name),
    facts: claims.length, rejected: s.rejected.length, identity: s.identity.strength,
  });
  // Precision sample: up to 12 cited facts per repo with their cited line.
  const cited = claims.filter((c) => !kvGet(c, "source").startsWith("search:"));
  for (const c of cited.map((c) => [rand(), c]).sort((a, b) => a[0] - b[0]).slice(0, 12).map((x) => x[1])) {
    const [, p, line] = kvGet(c, "source").match(/^(.+):(\d+)#/);
    const lines = fs.readFileSync(path.join(dir, p), "utf8").split(/\r?\n/);
    sample.push({ repo: r.id, id: c.id, topic: c.topic, scope: c.scope, statement: c.statement, source: `${p}:${line}`,
      context: lines.slice(Math.max(0, line - 3), Number(line) + 2).join("\n") });
  }
  const last = report[report.length - 1];
  console.error(`${r.id.padEnd(16)} units ${unitTp}/${want.size} (+${last.extra_units.length})  keys ${last.keys_found}/${last.keys_total}  facts ${claims.length}  ${ms} ms`);
}
const sum = (f) => report.reduce((a, x) => a + f(x), 0);
const totals = {
  repos: report.length,
  unit_precision: sum((x) => x.unit_precision) / report.length,
  unit_recall: sum((x) => x.unit_recall) / report.length,
  key_fact_recall: sum((x) => x.keys_found) / Math.max(1, sum((x) => x.keys_total)),
  facts: sum((x) => x.facts), rejected: sum((x) => x.rejected),
};
fs.writeFileSync(path.join(cache, "gold-report.json"), JSON.stringify({ totals, report }, null, 2));
fs.writeFileSync(path.join(cache, "gold-facts-sample.jsonl"), sample.map((x) => JSON.stringify(x)).join("\n") + "\n");
console.log(JSON.stringify(totals, null, 2));
