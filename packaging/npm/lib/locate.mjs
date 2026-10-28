// Where in THIS repository a task is: the files and the functions in them that the test writer and the cheap models
// should look at first. On a repository-sized project the fix is usually a few lines in one function of one large
// module (jsonargparse, 10 real issues: 3-30 lines, one source file each), and the hard part is finding it. The test
// writer used to see only a few small top-level files and the cheap models a 2,000-character task, so both worked blind.
//
// Sources, all local and model-free (fast, and nothing leaves the machine):
//   1. the code itself: `git grep` for the identifiers, file names and quoted messages the task mentions; a term found
//      in few files counts more than one found everywhere, and a definition (def/class/function) more than a use;
//   2. .kell/library.md through index/terms.idx: problems already solved here whose terms the task shares -- the
//      files their diffs touched (a repo's own past fixes are the strongest hint where the next one goes);
//   3. .kell/claims.md and claims/*.md: facts whose statement names a task term, at their cited source= file.
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
const SKIP_PATH = /(^|\/)(docs?|sphinx|examples?|vendor|node_modules|dist|build)\/|\.(md|rst|txt|lock|json|ya?ml|toml|cfg|ini|csv)$|(^|\/)(CHANGELOG|CHANGES|HISTORY|README)/i;
const SOURCE_EXT = /\.(py|pyx|pyi|m?[jt]sx?|cjs|go|rs|java|kt|kts|scala|rb|php|cs|c|cc|cpp|cxx|h|hpp|swift|m|mm|lua|r|jl|ex|exs|erl|clj|dart|sql|vue|svelte)$/i;
export const isSource = (f) => SOURCE_EXT.test(f) && !TEST_PATH.test(f) && !SKIP_PATH.test(f);
const DEF_LINE = /^\s*(?:async\s+def|def|class|function|export\s+(?:async\s+)?function|export\s+class|func|fn|pub\s+fn)\s+([A-Za-z_][A-Za-z0-9_]*)/;
const MAX_TERMS = 20;
// module names that say nothing about what a task is about
const GENERIC_STEMS = new Set(("utils util helpers helper common core base main init setup types typing errors error exceptions " +
  "config settings constants compat models model views tests version index server client options parser")
  .split(" "));
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
  // the other spellings code uses for a name the task writes in snake_case: `regexp_extract` is RegexpExtract (the class)
  // and REGEXP_EXTRACT (the SQL name) in sqlglot -- searched as written, it matched nothing in the source and the
  // "likely place" fell back to the files' first lines (Modal, sqlglot-4051)
  const base = [...out.entries()].slice(0, MAX_TERMS);
  const more = [];
  for (const [term, kind] of base) {
    if (kind !== "ident" || !/^[a-z0-9]+(_[a-z0-9]+)+$/.test(term)) continue;
    const camel = term.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join("");
    for (const v of [camel, term.toUpperCase()]) if (!out.has(v)) { out.set(v, "ident"); more.push([v, "ident"]); }
  }
  return [...base, ...more.slice(0, 10)].map(([term, kind]) => ({ term, kind }));
}

// Phrases of the error output and messages a task quotes ("Does not validate against any of the Union subtypes"): code
// writes them as literal text, so the line that builds the message is where the failure is reported -- often the function
// to fix. jsonargparse-508 and -559 quoted that message from _typehints.py inside a code block, and the search, which only
// read "double-quoted" messages, ranked _core.py first (cache-full-2, 2026-10-27). Runs of 4+ plain words, from code blocks
// and from lines that read like an error; the first 5 words of each (a format string's {value} rarely comes that early).
const ERRORISH = /\b(error|exception|warning|traceback|failed|fails|invalid|cannot|unable|expected|expects|unexpected|not supported|unsupported|must|missing)\b/i;
export function messagePhrases(text, max = 10) {
  const t = String(text || "");
  const spans = [...t.matchAll(/```[\w+-]*([\s\S]*?)```/g)].map((m) => m[1]);     // a task flattened to one line keeps its fences
  for (const l of t.replace(/```[\s\S]*?```/g, "\n").split(/\r?\n/)) if (ERRORISH.test(l)) spans.push(l);
  const out = new Set();
  for (const s of spans) {
    for (const chunk of s.split(/[\n:"'`()<>[\]{}=|,;!?]|\s-\s|\.(?:\s|$)/)) {
      let run = [];
      const flush = () => {
        if (run.length >= 4) {
          const p = run.slice(0, 5).join(" ");
          const code = run.slice(0, 5).some((w) => /^(import|from|def|class|return|lambda|async|await|const|let|var|function)$/.test(w));
          if (p.length >= 16 && !code && run.filter((w) => /^[a-z]/.test(w)).length >= 2) out.add(p);
        }
        run = [];
      };
      for (const w of chunk.trim().split(/\s+/)) { if (/^[A-Za-z][a-z']*$/.test(w)) run.push(w); else flush(); }
      flush();
      if (out.size >= max) return [...out];
    }
  }
  return [...out];
}

// The repository files a task's traceback runs through, innermost last: "File "/home/u/.../pkg/_core.py", line 1191, in
// instantiate_classes" -> {file: "pkg/_core.py", fn} by the longest path suffix that is a tracked file.
export function tracebackFrames(text, tracked) {
  const out = [];
  for (const m of String(text || "").matchAll(/File "([^"\n]+?\.\w{1,5})", line \d+, in ([A-Za-z_<][\w>]*)/g)) {
    const parts = m[1].replace(/\\/g, "/").split("/");
    for (let k = 0; k < parts.length; k++) {
      const f = parts.slice(k).join("/");
      if (tracked.has(f)) { out.push({ file: f, fn: m[2] }); break; }
    }
  }
  return out;
}

// How often this repository's bug-fix commits touched each file: files that were fixed often are where fixes go (the
// defect-prediction result: past fix density predicts the next fix). -> Map(file -> share of the most-fixed file's count)
const FIX_SUBJECT = /\b(fix(e[sd]|ing)?|bug|regression|crash(es)?|broken|wrong|incorrect)\b|(^|\s)#\d+|\(#\d+\)/i;
// root -> {at, res}: one git log per repository per 10 minutes, not per call (dispatch asks twice per task, the search and
// the narrowing; each git process costs ~200 ms on Windows, before the first cheap model starts). The shares barely move
// from one commit to the next.
const densityCache = new Map();
const DENSITY_TTL_MS = 10 * 60 * 1000;
export function fixDensity(root, { commits = 3000, now = Date.now() } = {}) {
  const key = `${root}|${commits}`;
  const hit = densityCache.get(key);
  if (hit && now - hit.at < DENSITY_TTL_MS) return hit.res;
  const res = fixDensityOf(root, commits);
  densityCache.set(key, { at: now, res });
  return res;
}

function fixDensityOf(root, commits) {
  const out = git(root, ["log", "--no-merges", `-n${commits}`, "--format=%x1e%s", "--name-only"], 30000);
  const counts = new Map();
  for (const block of out.split("\x1e")) {
    const [subject, ...files] = block.split(/\r?\n/);
    if (!FIX_SUBJECT.test(subject || "")) continue;
    for (const f of files) if (f && isSource(f)) counts.set(f, (counts.get(f) || 0) + 1);
  }
  const top = Math.max(0, ...counts.values());
  return new Map([...counts].map(([f, c]) => [f, top ? c / top : 0]));
}

function git(root, args, timeout = 15000) {
  const r = spawnSync("git", ["-C", root, ...args], { encoding: "utf8", timeout, windowsHide: true, maxBuffer: 16 * 1024 * 1024 });
  return r.status === 0 || r.status === 1 ? String(r.stdout || "") : "";
}

// term -> Map(file -> [{line, def}])
// paths never searched: docs, generated and vendored trees, the .kell notes (a minified docs/search.js matched on
// one multi-megabyte line overflowed the output and the whole lookup came back empty -- sqlglot, 2026-10-16)
const GREP_EXCLUDES = [":(exclude).kell", ":(exclude)docs", ":(exclude)doc", ":(exclude)node_modules", ":(exclude)vendor",
  ":(exclude)dist", ":(exclude)build", ":(glob,exclude)**/*.min.js", ":(glob,exclude)**/*.map", ":(glob,exclude)**/*.html",
  ":(glob,exclude)**/*.lock", ":(glob,exclude)**/package-lock.json"];

function grepTerm(root, { term, kind }) {
  // -o: only the matched text, never the whole line (a line can be megabytes); definitions are read from the file after
  const args = ["grep", "-n", "-o", "-I", "--no-color", "-F"];
  if (kind === "message") args.push("-i");
  const out = git(root, [...args, "-e", term, "--", ".", ...GREP_EXCLUDES]);
  const hits = new Map();
  const cache = new Map();
  const lineOf = (file, n) => {
    if (!cache.has(file)) cache.set(file, readLines(path.join(root, file)) || []);
    return cache.get(file)[n - 1] || "";
  };
  for (const l of out.split(/\r?\n/)) {
    const m = /^(.+?):(\d+):(.*)$/.exec(l);
    if (!m) continue;
    const file = m[1].replace(/\\/g, "/");
    const def = kind === "ident" && DEF_LINE.exec(lineOf(file, Number(m[2])).slice(0, 400))?.[1] === term;
    if (!hits.has(file)) hits.set(file, []);
    hits.get(file).push({ line: Number(m[2]), def });
  }
  return hits;
}

function readLines(file) { try { return fs.readFileSync(file, "utf8").split(/\r?\n/); } catch { return null; } }

// Files the library's past fixes touched, for the task terms the back-of-book index lists (lower-case terms).
function libraryFiles(root, terms) {
  const sdir = path.join(root, ".kell");
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
  const sdir = path.join(root, ".kell");
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

const MODULE_EXT = [".py", "/__init__.py", ".ts", ".js", ".mjs", ".rb", ".go", ".rs", ".java", ".kt", ".scala", ".php"];

// The source files the dotted names in a text refer to: for each name of 2+ parts, the longest prefix that names a file
// (a.b.c -> a/b.py, a/b/__init__.py, a/b.ts, ...), also under src/ and lib/.
export function moduleFiles(root, text) {
  const out = new Set();
  for (const m of String(text || "").matchAll(/(?<![\w./-])([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)/g)) {
    const segs = m[1].split(".");
    if (/^(py|js|ts|md|txt|com|org|io|html|json|ya?ml)$/i.test(segs[segs.length - 1])) continue;   // a file name or a domain
    // an exception class's name (sqlglot.errors.ParseError in an error output) says where the class is defined, not where
    // the failure is: errors.py ranked first on 3 of 12 sqlglot issues (locate benchmark, 2026-10-28)
    if (/(Error|Exception|Warning)$/.test(segs[segs.length - 1])) continue;
    for (let k = segs.length; k >= 2; k--) {
      const base = segs.slice(0, k).join("/");
      const hit = ["", "src/", "lib/"].flatMap((pre) => MODULE_EXT.map((e) => `${pre}${base}${e}`))
        .find((f) => { try { return fs.statSync(path.join(root, f)).isFile(); } catch { return false; } });
      if (hit) { out.add(hit); break; }
    }
  }
  return [...out].filter(isSource);
}

export function locate({ prompt, root, env = process.env, maxFiles = 3 } = {}) {
  const empty = { spots: [], files: [], testFile: null, terms: [] };
  if (!locateEnabled(env) || !root) return empty;
  try {
    const terms = taskTerms(prompt);
    if (!terms.length) return empty;
    const score = new Map(), hitsOf = new Map(), testHits = new Map();
    const bump = (f, w) => score.set(f, (score.get(f) || 0) + w);
    // files with strong evidence: a path or module the task names, a message it quotes, a frame of its traceback
    const strongFiles = new Set();
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
      if (fs.existsSync(path.join(root, f))) { bump(f, 3); strongFiles.add(f); }
    }
    // the messages the task quotes, found where the code writes them (messagePhrases): a phrase in one file is a strong
    // pointer, and its line is a hit for the spot (the function that raises it)
    for (const phrase of messagePhrases(prompt)) {
      const hits = grepTerm(root, { term: phrase, kind: "message" });
      const src = [...hits.keys()].filter((f) => isSource(f));
      if (!src.length || src.length > 6) continue;
      const w = 3 / Math.log2(1 + src.length);
      for (const f of src) {
        bump(f, w); strongFiles.add(f);
        hitsOf.set(f, [...(hitsOf.get(f) || []), ...hits.get(f).map((h) => ({ ...h, def: true, w }))]);
      }
    }
    const tracked = git(root, ["ls-files"]).split(/\r?\n/).filter(Boolean);
    // the repository's own frames of a quoted traceback, the innermost counting most; the frame's function is a hit
    const frames = tracebackFrames(prompt, new Set(tracked)).filter((x) => isSource(x.file));
    frames.forEach((fr, i) => {
      const w = i === frames.length - 1 ? 3 : 1;
      bump(fr.file, w); strongFiles.add(fr.file);
      const lines = readLines(path.join(root, fr.file)) || [];
      const at = lines.findIndex((l) => DEF_LINE.exec(l)?.[1] === fr.fn);
      if (at >= 0) hitsOf.set(fr.file, [...(hitsOf.get(fr.file) || []), { line: at + 1, def: true, w }]);
    });
    // a name that IS a source file's name points at that file ("tsql" -> sqlglot/dialects/tsql.py)
    const stems = new Map();
    for (const f of tracked) {
      if (!f || isTest(f) || SKIP_PATH.test(f) || !/\.(py|m?[jt]sx?|go|rs|java|rb|kt|cs|php)$/.test(f)) continue;
      const s = path.posix.basename(f).replace(/\.[^.]+$/, "").replace(/^_+/, "").toLowerCase();
      if (!stems.has(s)) stems.set(s, []);
      stems.get(s).push(f);
    }
    const named = new Set();
    for (const t of terms.filter((x) => x.kind === "ident")) {
      const fs_ = stems.get(t.term.toLowerCase());
      if (fs_ && fs_.length <= 3) { named.add(t.term.toLowerCase()); for (const f of fs_) bump(f, 3 / fs_.length); }
    }
    // a name of the task's prose that a source file is named after ("Snowflake" -> dialects/snowflake.py, "Postgres" ->
    // postgres.py): the product or system the issue is about has its own module. Capitalised mid-sentence only: lower-case
    // words ("users", "generator", "status") pulled in same-named modules and cost 4 points of top-1 on 63 issues of 22
    // other repositories (locate benchmark, 2026-10-28). Weaker than a code name; generic names never count
    const proper = [...String(prompt).matchAll(/(?<![.!?:#\n]\s*)(?<=\s)[A-Z][A-Za-z]{4,}\b/g)].map((m) => m[0].toLowerCase());
    for (const w of new Set(proper)) {
      const fs_ = stems.get(w);
      if (!fs_ || fs_.length > 2 || named.has(w) || GENERIC_STEMS.has(w)) continue;
      for (const f of fs_) bump(f, 1.5 / fs_.length);
    }
    // a dotted module path the task names ("jsonargparse._util._parse_value_or_config", "pkg.mod.Class") is the file it
    // names: the longest prefix that is a module file. Scored as a path: an explicit name beats words found everywhere --
    // jsonargparse-518 named _util._parse_value_or_config and locate still picked _core.py (cache-full-2, 2026-10-27)
    for (const f of moduleFiles(root, prompt)) { bump(f, 4); strongFiles.add(f); }
    for (const [f, w] of libraryFiles(root, terms)) if (!isTest(f) && fs.existsSync(path.join(root, f))) bump(f, 2 * w);
    for (const [f, w] of claimFiles(root, terms)) if (!isTest(f) && !SKIP_PATH.test(f) && fs.existsSync(path.join(root, f))) bump(f, 0.5 * Math.min(2, w));
    // only source code can be "the place": config files, images, shell/cmd scripts and the like were picked on 5 of 47
    // real issues (.flake8, .pylintrc, icon.svg, *.cmd -- pipeline survey, 2026-10-16)
    // the files this repository's bug fixes touched most rank a little higher among the matches (never added on their own)
    const prior = Number(env.STEALTHLAB_LOCATE_FIX_PRIOR ?? 0.5);
    // (only when there is an order to change: one candidate needs no git log)
    if (prior > 0 && [...score.keys()].filter(isSource).length > 1) {
      for (const [f, d] of fixDensity(root)) if (score.has(f)) score.set(f, score.get(f) * (1 + prior * Math.sqrt(d)));
    }
    const ranked = [...score.entries()].filter(([f]) => isSource(f)).sort((a, b) => b[1] - a[1]);
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
    return { spots, files, strong: strongFiles.has(files[0]), strongFiles: files.filter((f) => strongFiles.has(f)), testFile: files.length ? testFileFor(root, files[0], testHits) : null, terms: terms.map((t) => t.term) };
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
