// Where in THIS repository a task is: the files and the functions in them that the test writer and the cheap models
// should look at first. On a repository-sized project the fix is usually a few lines in one function of one large
// module (jsonargparse, 10 real issues: 3-30 lines, one source file each), and the hard part is finding it. The test
// writer used to see only a few small top-level files and the cheap models a 2,000-character task, so both worked blind.
//
// Sources, all local and model-free (fast, and nothing leaves the machine):
//   1. the code itself: `git grep` for the identifiers, file names and quoted messages the task mentions; a term found
//      in few files counts more than one found everywhere, and a definition (def/class/function) more than a use;
//   2. .stealth/library.md through index/terms.idx: problems already solved here whose terms the task shares -- the
//      files their diffs touched (a repo's own past fixes are the strongest hint where the next one goes);
//   3. .stealth/claims.md and claims/*.md: facts whose statement names a task term, at their cited source= file.
// -> {spots: [{file, start, end, symbol}], files, testFile, terms} (spots empty when nothing was found).
// STEALTHLAB_DISPATCH_LOCATE=off turns it off.
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { diffPaths } from "./library.mjs";

export const locateEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_LOCATE || "on").toLowerCase() !== "off";

const STOP = new Set(("the and for with from this that into when then than what which while should would could have has " +
  "not none true false self return import class def function const let var async await print value values error errors " +
  "file files test tests type types data list dict string str int bool object method methods name names default " +
  "issue issues expected actual example examples python using used use like also only just make makes other some " +
  "result results argument arguments option options parser config").split(" "));
const TEST_PATH = /(^|\/)(tests?|testing|[\w.-]*_tests?)(\/)|(^|\/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$/i;
const SKIP_PATH = /(^|\/)(docs?|examples?|vendor|node_modules|dist|build)\/|\.(md|rst|txt|lock|json|ya?ml|toml|cfg|ini|csv)$|(^|\/)(CHANGELOG|CHANGES|HISTORY|README)/i;
const DEF_LINE = /^\s*(?:async\s+def|def|class|function|export\s+(?:async\s+)?function|export\s+class|func|fn|pub\s+fn)\s+([A-Za-z_][A-Za-z0-9_]*)/;
const MAX_TERMS = 20;
const MAX_FILES_PER_TERM = 40;          // a term in more files than this is too common to say where the task is
const SPOT_LINES = 150;

// The terms of a task worth searching for: identifiers that look like code, file names, and quoted messages.
export function taskTerms(text) {
  const t = String(text || "");
  const out = new Map();                // term -> kind (ident | path | message)
  const add = (term, kind) => { if (term && !out.has(term) && out.size < MAX_TERMS * 2) out.set(term, kind); };
  for (const m of t.matchAll(/(?:^|[\s`'"(])((?:[\w.-]+\/)+[\w.-]+\.[A-Za-z]{1,5}|[\w-]+\.(?:py|js|mjs|ts|tsx|go|rs|java|rb))(?=$|[\s`'".,;:)!?])/g)) add(m[1], "path");
  for (const m of t.matchAll(/`([^`\n]{2,80})`/g)) {
    for (const id of m[1].match(/[A-Za-z_][A-Za-z0-9_]{2,}/g) || []) if (!STOP.has(id.toLowerCase())) add(id, "ident");
  }
  for (const m of t.matchAll(/["“]([^"”\n]{12,90})["”]/g)) if (/[a-z]/i.test(m[1]) && /\s/.test(m[1])) add(m[1].trim(), "message");
  for (const m of t.matchAll(/\b[A-Za-z_][A-Za-z0-9_]{3,}\b/g)) {
    const w = m[0];
    const codeLike = w.includes("_") || /[a-z][A-Z]/.test(w) || /^[A-Z][a-z]+[A-Z]/.test(w) || /\(/.test(t[m.index + w.length] || "");
    if (codeLike && !STOP.has(w.toLowerCase())) add(w, "ident");
  }
  for (const m of t.matchAll(/\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b/g)) {
    const last = m[1].split(".").pop();
    if (last.length > 2 && !STOP.has(last.toLowerCase()) && !/^(py|js|ts|md|txt|com|org|io)$/i.test(last)) add(last, "ident");
  }
  return [...out.entries()].slice(0, MAX_TERMS).map(([term, kind]) => ({ term, kind }));
}

function git(root, args, timeout = 15000) {
  const r = spawnSync("git", ["-C", root, ...args], { encoding: "utf8", timeout, windowsHide: true, maxBuffer: 16 * 1024 * 1024 });
  return r.status === 0 || r.status === 1 ? String(r.stdout || "") : "";
}

// term -> Map(file -> [{line, def}])
function grepTerm(root, { term, kind }) {
  const args = ["grep", "-n", "-I", "--no-color", "-F"];
  if (kind === "message") args.push("-i");
  const out = git(root, [...args, "-e", term, "--", "."]);
  const hits = new Map();
  for (const l of out.split(/\r?\n/)) {
    const m = /^(.+?):(\d+):(.*)$/.exec(l);
    if (!m) continue;
    const file = m[1].replace(/\\/g, "/");
    const def = kind === "ident" && DEF_LINE.exec(m[3])?.[1] === term;
    if (!hits.has(file)) hits.set(file, []);
    hits.get(file).push({ line: Number(m[2]), def });
  }
  return hits;
}

function readLines(file) { try { return fs.readFileSync(file, "utf8").split(/\r?\n/); } catch { return null; } }

// Files the library's past fixes touched, for the task terms the back-of-book index lists (lower-case terms).
function libraryFiles(root, terms) {
  const sdir = path.join(root, ".stealth");
  const idx = readLines(path.join(sdir, "index", "terms.idx"));
  if (!idx) return new Map();
  const want = new Set(terms.flatMap(({ term }) => [term.toLowerCase(), path.posix.basename(term.toLowerCase())]));
  const ids = new Set();
  for (const l of idx) {
    const bar = l.indexOf("|");
    if (bar > 0 && want.has(l.slice(0, bar))) for (const id of l.slice(bar + 1).split(",")) if (/^L-[0-9a-f]+$/.test(id.trim())) ids.add(id.trim());
  }
  const files = new Map();
  for (const id of ids) {
    let diff = "";
    try { diff = fs.readFileSync(path.join(sdir, "library", "solutions", `${id}.diff`), "utf8"); } catch { continue; }
    for (const f of diffPaths(diff)) files.set(f, (files.get(f) || 0) + 1 / ids.size);
  }
  return files;
}

// Files that survey facts naming a task term cite (CLAIM lines: ...|<statement>|source=<path>:<line>...).
function claimFiles(root, terms) {
  const sdir = path.join(root, ".stealth");
  const pages = [path.join(sdir, "claims.md")];
  try { for (const f of fs.readdirSync(path.join(sdir, "claims"))) if (f.endsWith(".md")) pages.push(path.join(sdir, "claims", f)); } catch { /* one page */ }
  const lower = terms.filter((t) => t.kind !== "message").map((t) => t.term.toLowerCase());
  const files = new Map();
  for (const p of pages) {
    for (const l of readLines(p) || []) {
      if (!l.startsWith("CLAIM|")) continue;
      const src = /\|source=([^|:#\s]+)/.exec(l)?.[1];
      if (!src) continue;
      const low = l.toLowerCase();
      if (lower.some((t) => low.includes(t))) files.set(src, (files.get(src) || 0) + 1);
    }
  }
  return files;
}

// The function or class around a line (Python by indentation; other languages: a window) -> {start, end, symbol}
export function enclosingBlock(lines, line) {
  const i0 = Math.max(0, Math.min(lines.length - 1, line - 1));
  const indent = (s) => s.match(/^\s*/)[0].length;
  const py = lines.some((l) => /^\s*(async\s+)?def\s|^\s*class\s/.test(l));
  if (!py) return { start: Math.max(1, line - 25), end: Math.min(lines.length, line + 35), symbol: null };
  let head = -1;
  const own = indent(lines[i0]);
  for (let i = i0; i >= 0; i--) {
    const l = lines[i];
    if (!l.trim()) continue;
    if (/^\s*(async\s+def|def|class)\s/.test(l) && (i === i0 || indent(l) < own)) { head = i; break; }
    if (indent(l) === 0 && i !== i0 && !/^\s*[@#]/.test(l)) break;     // module level: no enclosing function
  }
  if (head < 0) return { start: Math.max(1, line - 20), end: Math.min(lines.length, line + 30), symbol: null };
  const hi = indent(lines[head]);
  let start = head;
  while (start > 0 && /^\s*@/.test(lines[start - 1])) start--;
  let end = head + 1;
  while (end < lines.length && (!lines[end].trim() || indent(lines[end]) > hi)) end++;
  while (end > head + 1 && !lines[end - 1].trim()) end--;
  const symbol = /^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)/.exec(lines[head])?.[1] || null;
  if (end - start > SPOT_LINES) {
    // a long function: keep its head and the part around the hit
    const from = Math.max(start, line - 60), to = Math.min(end, line + 60);
    return { start: from + 1, end: to, symbol };
  }
  return { start: start + 1, end, symbol };
}

const isTest = (f) => TEST_PATH.test(f);

// The existing test file that covers a module: test_<stem>.py, <stem>_test.py, ... (leading _ dropped), else the
// test file that mentions the task's terms most.
export function testFileFor(root, file, testHits = new Map()) {
  const all = git(root, ["ls-files"]).split(/\r?\n/).filter((f) => f && isTest(f));
  const stem = path.posix.basename(file).replace(/\.[^.]+$/, "").replace(/^_+/, "");
  const exact = all.filter((f) => {
    const b = path.posix.basename(f).replace(/\.[^.]+$/, "");
    return b === `test_${stem}` || b === `${stem}_test` || b === `${stem}.test` || b === `${stem}.spec`;
  });
  if (exact.length) return exact.sort((a, b) => a.length - b.length)[0];
  let best = null, n = 0;
  for (const [f, c] of testHits) if (c > n && all.includes(f)) { best = f; n = c; }
  return best;
}

export function locate({ prompt, root, env = process.env, maxFiles = 3 } = {}) {
  const empty = { spots: [], files: [], testFile: null, terms: [] };
  if (!locateEnabled(env) || !root) return empty;
  try {
    const terms = taskTerms(prompt);
    if (!terms.length) return empty;
    const score = new Map(), hitsOf = new Map(), testHits = new Map();
    const bump = (f, w) => score.set(f, (score.get(f) || 0) + w);
    for (const t of terms) {
      const hits = grepTerm(root, t);
      const files = [...hits.keys()].filter((f) => !SKIP_PATH.test(f));
      for (const f of files.filter(isTest)) testHits.set(f, (testHits.get(f) || 0) + 1);
      const src = files.filter((f) => !isTest(f));
      if (!src.length || src.length > MAX_FILES_PER_TERM) continue;
      const w = (t.kind === "path" ? 3 : t.kind === "message" ? 2 : 1) / Math.log2(2 + src.length);
      for (const f of src) {
        const hs = hits.get(f);
        bump(f, w * (hs.some((h) => h.def) ? 2.5 : 1));
        hitsOf.set(f, [...(hitsOf.get(f) || []), ...hs.map((h) => ({ ...h, w }))]);
      }
    }
    for (const t of terms.filter((x) => x.kind === "path")) {
      const f = t.term.replace(/^\.\//, "");
      if (fs.existsSync(path.join(root, f))) bump(f, 3);
    }
    for (const [f, w] of libraryFiles(root, terms)) if (!isTest(f) && fs.existsSync(path.join(root, f))) bump(f, 2 * w);
    for (const [f, w] of claimFiles(root, terms)) if (!isTest(f) && !SKIP_PATH.test(f) && fs.existsSync(path.join(root, f))) bump(f, 0.5 * Math.min(2, w));
    const ranked = [...score.entries()].sort((a, b) => b[1] - a[1]);
    if (!ranked.length) return { ...empty, terms: terms.map((t) => t.term) };
    const top = ranked[0][1];
    const files = ranked.filter(([, s]) => s >= top * 0.34).slice(0, maxFiles).map(([f]) => f);
    const spots = [];
    for (const f of files) {
      const lines = readLines(path.join(root, f));
      if (!lines) continue;
      // the best hit lines of this file: definitions first, then the rarest terms
      const hs = (hitsOf.get(f) || []).sort((a, b) => (b.def - a.def) || (b.w - a.w)).slice(0, 6);
      const blocks = [];
      for (const h of hs) {
        const b = enclosingBlock(lines, h.line);
        const over = blocks.find((x) => !(b.end < x.start || b.start > x.end));
        if (over) { over.start = Math.min(over.start, b.start); over.end = Math.max(over.end, b.end); over.hits++; }
        else blocks.push({ ...b, hits: 1 });
      }
      for (const b of blocks.sort((a, z) => z.hits - a.hits).slice(0, 2)) spots.push({ file: f, start: b.start, end: Math.min(b.end, b.start + 200), symbol: b.symbol });
      if (!blocks.length) spots.push({ file: f, start: 1, end: Math.min(lines.length, 80), symbol: null });
    }
    return { spots, files, testFile: files.length ? testFileFor(root, files[0], testHits) : null, terms: terms.map((t) => t.term) };
  } catch {
    return empty;
  }
}

// One line for the cheap model's task: where to look, as path:start-end (the executor's agent preloads those lines).
export function renderWhere(loc) {
  if (!loc?.spots?.length) return "";
  const s = loc.spots.slice(0, 3).map((p) => `${p.file}:${p.start}-${p.end}${p.symbol ? ` (${p.symbol})` : ""}`).join(", ");
  return `Likely place in this repository (found by searching it for the task's names; confirm before relying on it): ${s}.` +
    (loc.testFile ? ` Existing tests for it: ${loc.testFile}.` : "");
}

// The code itself for the test writer: the spots with line numbers, then the head and first test of the module's test file.
export function renderForWriter(loc, root, { max = 12000 } = {}) {
  if (!loc?.spots?.length) return "";
  const parts = [];
  let used = 0;
  for (const p of loc.spots) {
    const lines = readLines(path.join(root, p.file)) || [];
    const body = lines.slice(p.start - 1, p.end).map((l, i) => `${p.start + i}\t${l}`).join("\n");
    const piece = `--- ${p.file} lines ${p.start}-${p.end}${p.symbol ? ` (${p.symbol})` : ""} ---\n${body}`;
    if (used + piece.length > max) break;
    parts.push(piece);
    used += piece.length;
  }
  const sample = loc.testFile ? testSample(path.join(root, loc.testFile)) : "";
  if (sample && used + sample.length < max + 3000) parts.push(`--- ${loc.testFile} (the project's existing tests for this code: follow its imports and style) ---\n${sample}`);
  return parts.join("\n\n");
}

// The imports of a test file and its first test function: how this project's tests reach its code.
export function testSample(file) {
  const lines = readLines(file);
  if (!lines) return "";
  const firstDef = lines.findIndex((l) => /^(async\s+)?def\s|^class\s/.test(l));
  const head = lines.slice(0, firstDef < 0 ? 30 : Math.min(firstDef, 40));
  const t = lines.findIndex((l) => /^\s*(async\s+)?def\s+test_/.test(l));
  let body = [];
  if (t >= 0) {
    const ind = lines[t].match(/^\s*/)[0].length;
    let e = t + 1;
    while (e < lines.length && e < t + 40 && (!lines[e].trim() || lines[e].match(/^\s*/)[0].length > ind)) e++;
    body = lines.slice(t, e);
  }
  return [...head, ...(body.length ? ["...", ...body] : [])].join("\n").slice(0, 3000);
}
