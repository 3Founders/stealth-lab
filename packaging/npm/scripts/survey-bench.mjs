#!/usr/bin/env node
// Performance gates for `stealthlab-mcp survey` (docs/plan_2026-10_priors_library_survey.md §4.6):
//   scanner < 10 s at 100k files, < 60 s at 1M files, < 500 MB memory.
//
//   node scripts/survey-bench.mjs synth <files> [packages]   build a synthetic pnpm monorepo and time it
//   node scripts/survey-bench.mjs repo <path>                 time an existing checkout
//
// The synthetic repo has `packages` real packages on disk (package.json, README, src/index.ts, a test) and
// the rest of its files as git index entries marked skip-worktree -- the shape of a huge monorepo under a
// sparse checkout, and the way to get 1M index entries without writing 1M files. Every run is a fresh
// process (cold module load) and a cold first survey; a second, incremental survey is timed too.
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BIN = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "bin", "stealthlab-mcp.mjs");
const gitc = (cwd, args, input) => execFileSync("git", ["-c", "core.autocrlf=false", ...args], { cwd, input, maxBuffer: 1 << 30, encoding: "utf8",
  env: { ...process.env, GIT_AUTHOR_NAME: "b", GIT_AUTHOR_EMAIL: "b@x", GIT_COMMITTER_NAME: "b", GIT_COMMITTER_EMAIL: "b@x" } });

function synth(nFiles, nPackages) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `sl-bench-${nFiles}-`));
  gitc(dir, ["init", "-q"]);
  const w = (rel, s) => { const p = path.join(dir, rel); fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, s); };
  w("package.json", JSON.stringify({ name: "bench", private: true, scripts: { test: "turbo run test", build: "turbo run build" } }, null, 2));
  w("pnpm-workspace.yaml", "packages:\n  - 'packages/*'\n");
  w("pnpm-lock.yaml", "lockfileVersion: '9.0'\n");
  w(".nvmrc", "20.11.0\n");
  w(".github/workflows/ci.yml", "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/setup-node@v4\n        with:\n          node-version: 20\n      - run: pnpm install\n      - run: pnpm test\n");
  for (let i = 0; i < nPackages; i++) {
    const vite = i % 3 === 2;
    w(`packages/p${i}/package.json`, JSON.stringify({ name: `@bench/p${i}`, version: "1.0.0", description: `Bench package ${i}`,
      scripts: { build: vite ? "vite build" : "tsup src/index.ts", test: "vitest run" }, dependencies: { [`d${i}`]: "1" },
      devDependencies: vite ? { vite: "^5", vitest: "^1" } : { tsup: "^8", vitest: "^1", typescript: "^5" } }, null, 2));
    w(`packages/p${i}/README.md`, `# p${i}\n\nPackage ${i}.\n`);
    w(`packages/p${i}/src/index.ts`, "export {};\n");
    w(`packages/p${i}/test/index.test.ts`, "test('x', () => {});\n");
  }
  gitc(dir, ["add", "-A"]);
  gitc(dir, ["commit", "-qm", "real files"]);
  const onDisk = nPackages * 4 + 5;
  const rest = Math.max(0, nFiles - onDisk);
  if (rest) {
    const blob = gitc(dir, ["hash-object", "-w", "--stdin"], "export const x = 1;\n").trim();
    const paths = [];
    for (let k = 0; k < rest; k++) {
      const pkg = k % nPackages;
      paths.push(`packages/p${pkg}/src/gen/m${Math.floor(k / nPackages) % 50}/f${k}.ts`);
    }
    gitc(dir, ["update-index", "--add", "--index-info"], paths.map((p) => `100644 ${blob}\t${p}`).join("\n") + "\n");
    gitc(dir, ["update-index", "--skip-worktree", "--stdin"], paths.join("\n") + "\n");
    // Left staged on purpose: the scanner reads the index, and committing 1M entries only costs setup time.
  }
  return dir;
}

function timeSurvey(dir, extra = []) {
  const t0 = process.hrtime.bigint();
  const r = spawnSync(process.execPath, [BIN, "survey", dir, "--json", "--no-history", ...extra], { encoding: "utf8", maxBuffer: 1 << 28 });
  const wall = Number(process.hrtime.bigint() - t0) / 1e6;
  if (r.status !== 0) throw new Error(r.stderr);
  const j = JSON.parse(r.stdout);
  return { wall_ms: Math.round(wall), timing: j.timing, rss_mb: j.memory_mb, files: j.listing.files, units: j.units.length,
    templates: new Set(j.units.map((u) => u.template).filter(Boolean)).size, lazy: j.lazy, est_tokens: j.estTokens,
    worklist: j.worklist.length, facts_added: j.merge.added, rejected: j.rejected.length };
}

const [mode, a, b] = process.argv.slice(2);
let dir;
if (mode === "synth") dir = synth(Number(a), Number(b || 500));
else if (mode === "repo") dir = path.resolve(a);
else { console.error("usage: survey-bench.mjs synth <files> [packages] | repo <path>"); process.exit(2); }
fs.rmSync(path.join(dir, ".stealth"), { recursive: true, force: true });
const cold = timeSurvey(dir);
const warm = timeSurvey(dir);
const out = { mode, dir, cold, incremental: warm };
console.log(JSON.stringify(out, null, 2));
if (mode === "synth" && !process.env.KEEP) fs.rmSync(dir, { recursive: true, force: true });
