// File listing for `stealthlab-mcp survey`: what is in this repository, read locally, never uploaded.
//
// With git: one `git ls-files -s -t` pass gives every tracked path with its index blob sha (used for
// incremental re-survey) and its skip-worktree tag (sparse checkout: listed but not on disk), plus one
// `--others --exclude-standard` pass for untracked files git does not ignore. Gitlinks (mode 160000)
// are submodules. Without git (a tarball, a copied folder): a directory walk that honours .gitignore
// files and never follows directory symlinks.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const BIG = 1 << 30; // 1 GiB: `git ls-files` on a 1M-file repo is ~100 MB of output

export function git(cwd, args, { encoding = "utf8", allowFail = true, input } = {}) {
  const r = spawnSync("git", ["-c", "core.quotepath=off", ...args], {
    cwd, encoding, maxBuffer: BIG, windowsHide: true, input,
  });
  if (r.error || r.status !== 0) {
    if (allowFail) return null;
    throw new Error(`git ${args.join(" ")}: ${(r.stderr || r.error?.message || "").toString().trim()}`);
  }
  return r.stdout;
}

export function gitTopLevel(dir) {
  const out = git(dir, ["rev-parse", "--show-toplevel"]);
  return out ? path.resolve(out.trim()) : null;
}

export const toPosix = (p) => p.split(path.sep).join("/");

// Directories never worth listing in a walk even without a .gitignore (environments and caches).
const WALK_SKIP = new Set([".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache",
  ".pytest_cache", ".gradle", ".idea", ".stealth", ".next", ".nuxt", ".turbo", ".cache", "target", "bazel-out"]);

/**
 * List the repository's files.
 * @returns {{files: string[], blob: Map<string,string>, sparse: Set<string>, submodules: string[],
 *            source: "git"|"walk", gitRoot: string|null, prefix: string}}
 * `files` are posix paths relative to `root`, sorted. `blob` maps a tracked path to its index blob sha.
 */
export function listFiles(root) {
  root = path.resolve(root);
  const gitRoot = gitTopLevel(root);
  if (gitRoot) {
    const res = listGit(root, gitRoot);
    if (res) return res;
  }
  return { ...walk(root), blob: new Map(), sparse: new Set(), submodules: [], source: "walk", gitRoot: null, prefix: "" };
}

/**
 * Run git with stdout going to a temp file, then read it once. spawnSync's in-memory capture holds the
 * output twice while it concatenates chunks -- ~200 MB transient for `ls-files -s` on a 1M-file repo.
 */
function gitToBuffer(cwd, args) {
  const tmp = path.join(os.tmpdir(), `stealth-survey-${process.pid}-${Date.now()}.out`);
  const fd = fs.openSync(tmp, "w");
  try {
    const r = spawnSync("git", ["-c", "core.quotepath=off", ...args], { cwd, stdio: ["ignore", fd, "pipe"], windowsHide: true });
    fs.closeSync(fd);
    if (r.error || r.status !== 0) return null;
    return fs.readFileSync(tmp);
  } finally {
    try { fs.closeSync(fd); } catch { /* closed */ }
    try { fs.rmSync(tmp, { force: true }); } catch { /* best effort */ }
  }
}

function listGit(root, gitRoot) {
  const raw = gitToBuffer(root, ["ls-files", "-z", "-s", "-t", "--full-name", "."]);
  if (raw === null) return null;
  const prefix = toPosix(path.relative(gitRoot, root)); // "" when surveying the repo top level
  const strip = prefix ? prefix + "/" : "";
  const files = [];
  const sparse = new Set();
  const submodules = [];
  // "<tag> <mode> <sha> <stage>\t<path>\0". Memory matters at 1M files: no per-file sha map (cited files
  // are hashed from their content when needed) and no `seen` set (the index is sorted, so a merge
  // conflict's extra stages are adjacent duplicates).
  // Paths are decoded straight from the buffer, one small string each: decoding the whole output first
  // would hold a second copy of it (and every path slice would keep that copy alive).
  const raw2 = raw;
  let i = 0;
  let last = null;
  const GITLINK = Buffer.from("160000");
  while (i < raw2.length) {
    const end = raw2.indexOf(0, i);
    const stop = end === -1 ? raw2.length : end;
    const tab = raw2.indexOf(9, i);
    const recStart = i;
    i = stop + 1;
    if (tab < 0 || tab > stop) continue;
    let p = raw2.toString("utf8", tab + 1, stop);
    if (strip && p.startsWith(strip)) p = p.slice(strip.length);
    if (p === last) continue;
    last = p;
    if (p.startsWith(".stealth/")) continue; // our own output is not a repository fact
    if (raw2.compare(GITLINK, 0, 6, recStart + 2, recStart + 8) === 0) { submodules.push(p); continue; }
    files.push(p);
    if (raw2[recStart] === 83 /* "S": skip-worktree, i.e. sparse */) sparse.add(p);
  }
  const others = git(root, ["ls-files", "-z", "--others", "--exclude-standard", "."], { encoding: "buffer" });
  let added = 0;
  if (others) {
    for (const p of others.toString("utf8").split("\0")) {
      if (p && !p.startsWith(".stealth/")) { files.push(p); added++; }
    }
  }
  if (added) files.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return { files, blob: new Map(), sparse, submodules, source: "git", gitRoot, prefix };
}

// ---------------------------------------------------------------------------------------------
// .gitignore matching (for the walk fallback). Implements the documented rules: blank/# lines,
// `!` negation, trailing `/` = directories only, a `/` anywhere but the end anchors to the file's
// directory, `*`, `?`, `[...]`, `**/`, `/**`, `/**/`. Last matching pattern wins.
// ---------------------------------------------------------------------------------------------
export function compileGitignore(text, base = "") {
  const rules = [];
  for (let line of text.split(/\r?\n/)) {
    if (!line || line.startsWith("#")) continue;
    line = line.replace(/(?<!\\)\s+$/, "");
    if (!line) continue;
    let negate = false;
    if (line.startsWith("!")) { negate = true; line = line.slice(1); }
    else if (line.startsWith("\\!") || line.startsWith("\\#")) line = line.slice(1);
    let dirOnly = false;
    if (line.endsWith("/")) { dirOnly = true; line = line.slice(0, -1); }
    const anchored = line.includes("/");
    if (line.startsWith("/")) line = line.slice(1);
    rules.push({ re: gitGlobToRegExp(line, anchored), negate, dirOnly, base });
  }
  return rules;
}

export function gitGlobToRegExp(glob, anchored) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") {
        const atStart = i === 0 || glob[i - 1] === "/";
        const atEnd = i + 2 === glob.length || glob[i + 2] === "/";
        if (atStart && atEnd) {
          if (i + 2 === glob.length) { re += ".*"; i += 1; }
          else { re += "(?:.*/)?"; i += 2; }
          continue;
        }
      }
      re += "[^/]*";
    } else if (c === "?") re += "[^/]";
    else if (c === "[") {
      const close = glob.indexOf("]", i + 1);
      if (close < 0) { re += "\\["; continue; }
      let cls = glob.slice(i + 1, close).replace(/\\/g, "\\\\");
      if (cls.startsWith("!")) cls = "^" + cls.slice(1);
      re += `[${cls}]`;
      i = close;
    } else if (c === "\\" && i + 1 < glob.length) { re += "\\" + glob[++i]; }
    else re += /[.+^${}()|\]\\]/.test(c) ? "\\" + c : c;
  }
  return new RegExp(anchored ? `^${re}$` : `(?:^|/)${re}$`);
}

export function ignored(rules, relPath, isDir) {
  let out = false;
  for (const r of rules) {
    if (r.dirOnly && !isDir) continue;
    let p = relPath;
    if (r.base) {
      if (!p.startsWith(r.base + "/")) continue;
      p = p.slice(r.base.length + 1);
    }
    if (r.re.test(p)) out = !r.negate;
  }
  return out;
}

function walk(root) {
  const files = [];
  const stack = [["", []]];
  while (stack.length) {
    const [rel, inherited] = stack.pop();
    const abs = path.join(root, rel);
    let rules = inherited;
    try {
      const gi = fs.readFileSync(path.join(abs, ".gitignore"), "utf8");
      rules = inherited.concat(compileGitignore(gi, rel));
    } catch { /* no .gitignore here */ }
    let entries;
    try { entries = fs.readdirSync(abs, { withFileTypes: true }); } catch { continue; }
    for (const e of entries) {
      const childRel = rel ? `${rel}/${e.name}` : e.name;
      const isDir = e.isDirectory(); // a symlink to a directory is NOT followed (isDirectory() is false)
      if (isDir && WALK_SKIP.has(e.name)) continue;
      if (ignored(rules, childRel, isDir)) continue;
      if (isDir) stack.push([childRel, rules]);
      else files.push(childRel);
    }
  }
  files.sort();
  return { files };
}

// ---------------------------------------------------------------------------------------------
// Reading files: bounded, CRLF-tolerant, and safe on a case-insensitive file system.
// ---------------------------------------------------------------------------------------------
export function readText(root, rel, maxBytes = 512 * 1024) {
  try {
    const fd = fs.openSync(path.join(root, rel), "r");
    try {
      // Allocate what the file needs, not the cap: a cap-sized buffer per read is GBs of churn on big repos.
      const want = Math.min(fs.fstatSync(fd).size, maxBytes);
      const buf = Buffer.allocUnsafe(want);
      const n = want ? fs.readSync(fd, buf, 0, want, 0) : 0;
      const b = buf.subarray(0, n);
      if (b.includes(0)) return null; // binary
      const s = b.toString("utf8");
      return s.charCodeAt(0) === 0xfeff ? s.slice(1) : s; // a UTF-8 BOM breaks JSON.parse and shifts line 1
    } finally { fs.closeSync(fd); }
  } catch {
    return null;
  }
}

export function fileSize(root, rel) {
  try { return fs.statSync(path.join(root, rel)).size; } catch { return -1; }
}
