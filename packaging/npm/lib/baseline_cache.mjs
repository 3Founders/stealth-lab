// The unchanged code's check result, kept per state of the checkout, so it is run once and not on every dispatch.
//
// Why: the baseline (which tests already fail before any change) is the project's whole suite. On kell-try it took
// 2.5-3.75 min and ran four times in one evening on the same commit with the same result (2026-10-29), and an answer
// that finished early waited for it. The key is everything the result depends on: the commit, the uncommitted changes
// (their diff and the untracked files' contents), the check command and the Python in use. Any of them different: a new
// run. STEALTHLAB_BASELINE_CACHE=off always runs it.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { configDir } from "./config.mjs";

const OUT_MAX = 2_000_000;
const KEEP = 40;

export const baselineCacheEnabled = (env = process.env) => (env.STEALTHLAB_BASELINE_CACHE || "on").toLowerCase() !== "off";

function git(root, args) {
  const r = spawnSync("git", ["-C", root, ...args], { encoding: "utf8", timeout: 15000, windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
  return r.status === 0 ? r.stdout : null;
}

// -> the cache key for this checkout and check, or null (not a git checkout: no key, so no cache)
export function baselineKey(root, check, env = process.env) {
  const head = git(root, ["rev-parse", "HEAD"]);
  const diff = git(root, ["diff", "HEAD", "--binary"]);
  const untracked = git(root, ["ls-files", "--others", "--exclude-standard", "-z"]);
  if (head === null || diff === null || untracked === null) return null;
  const h = crypto.createHash("sha256").update(head).update("\0").update(diff).update("\0").update(String(check)).update("\0")
    .update(String(env.VIRTUAL_ENV || "")).update("\0");
  for (const f of untracked.split("\0").filter(Boolean).sort()) {
    h.update(f).update("\0");
    try { h.update(fs.readFileSync(path.join(root, f))); } catch { h.update("?"); }
  }
  return h.digest("hex").slice(0, 32);
}

const dirOf = (env) => path.join(configDir(env), "baseline_cache");

// -> {code, out} of an earlier run in the same state, or null
export function readBaseline(key, env = process.env) {
  if (!key || !baselineCacheEnabled(env)) return null;
  try {
    const o = JSON.parse(fs.readFileSync(path.join(dirOf(env), `${key}.json`), "utf8"));
    return typeof o?.out === "string" ? { code: o.code ?? null, out: o.out } : null;
  } catch { return null; }
}

// a run that finished (exit code known) is kept; one that timed out or was stopped is not
export function writeBaseline(key, { code, out }, env = process.env) {
  if (!key || !baselineCacheEnabled(env) || code === null || code === undefined) return false;
  try {
    const dir = dirOf(env);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, `${key}.json`), JSON.stringify({ at: new Date().toISOString(), code, out: String(out || "").slice(-OUT_MAX) }));
    const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json") && f !== "suite_secs.json")
      .map((f) => ({ f, t: fs.statSync(path.join(dir, f)).mtimeMs })).sort((a, b) => b.t - a.t);
    for (const { f } of files.slice(KEEP)) fs.rmSync(path.join(dir, f), { force: true });
    return true;
  } catch { return false; }
}

// How long this project's check takes, per repository and check (whatever state the checkout is in): the time kept for
// checking the last answer is sized from it (lib/dispatch.mjs). A fixed 300 s was less than kell-try's 210 s suite plus
// the review and a second answer checking at the same time.
const suiteFile = (env) => path.join(configDir(env), "baseline_cache", "suite_secs.json");
const suiteKey = (root, check) => crypto.createHash("sha256").update(path.resolve(String(root))).update("\0").update(String(check)).digest("hex").slice(0, 24);

export function suiteSecs(root, check, env = process.env) {
  try { const v = JSON.parse(fs.readFileSync(suiteFile(env), "utf8"))[suiteKey(root, check)]; return Number.isFinite(v) ? v : null; } catch { return null; }
}

export function recordSuiteSecs(root, check, secs, env = process.env) {
  if (!Number.isFinite(secs) || secs <= 0) return false;
  try {
    let all = {};
    try { all = JSON.parse(fs.readFileSync(suiteFile(env), "utf8")) || {}; } catch { /* first one */ }
    all[suiteKey(root, check)] = Math.round(secs);
    fs.mkdirSync(path.dirname(suiteFile(env)), { recursive: true });
    fs.writeFileSync(suiteFile(env), JSON.stringify(all));
    return true;
  } catch { return false; }
}
