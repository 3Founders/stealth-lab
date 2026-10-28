// What this repository's own history says about the code a cheap model is about to change.
//
// The library (lib/library.mjs) is keyed by GOAL: a lookup matches "the same task done before". Two different bugs in the same
// function never match, so on a run of unrelated issues every lookup came back no_match (cache-full-2, 2026-10-27). What the
// cheap models lacked was the AREA's history: jsonargparse #560 is fixed the way #559 was, a day earlier, in the same
// function (copy the kwargs dict instead of mutating it), and the cheap models instead patched the symptom with a guard.
//
// This reads `git log -p` for the files locate found, keeps the hunks whose enclosing function is one of the functions the
// located lines are in (git's own function headers, with its built-in drivers for Python and others), and renders the newest
// few, bug fixes first, inside a character budget. Pure git, no model, nothing leaves the machine.
//
// Honest scope limits: a hunk header names the function git finds above the change, which for a module-level change or an
// unsupported language is the nearest line that looks like a header -- matching on it is a heuristic. History is whatever this
// clone has: a shallow clone, or a repository with no history, gives nothing (and costs nothing).
// STEALTHLAB_DISPATCH_AREA_HISTORY=on adds it to the cheap models' task (off by default until measured).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

export const AREA_HISTORY_MAX = 6000;
const MAX_COMMITS = 300;        // per file: older history rarely shows the code as it is now
const HUNK_LINES_MAX = 60;      // a larger hunk is a rewrite or a moved block, not a pattern to follow
const ENTRIES_MAX = 4;
const PER_FUNCTION = 2;
const FIX = /\b(fix|fixes|fixed|bug|incorrect|wrong|fail|failing|broken|regression|error)\b/i;

export const areaHistoryEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_AREA_HISTORY || "off").toLowerCase() === "on";

// git's built-in function-header drivers by extension (gitattributes(5) "diff" drivers)
const DRIVERS = { ".py": "python", ".rb": "ruby", ".go": "golang", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".cs": "csharp",
  ".php": "php", ".c": "cpp", ".h": "cpp", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".js": "javascript", ".mjs": "javascript",
  ".jsx": "javascript", ".ts": "javascript", ".tsx": "javascript" };

const DEF = /^(\s*)(?:async\s+def|def|class|function|func|fn|(?:export\s+)?(?:async\s+)?function)\s+([A-Za-z_$][\w$]*)/;

// The functions (or classes) the lines start..end are in: the innermost definition above `start`, its enclosing ones, and any
// defined inside the range. Indentation decides nesting, which fits Python and most formatted code.
export function enclosingNames(text, start, end) {
  const lines = String(text).split(/\r?\n/);
  const names = new Set();
  let indent = Infinity;
  for (let i = Math.min(lines.length, Math.max(1, start)) - 1; i >= 0 && indent > 0; i--) {
    const m = DEF.exec(lines[i]);
    if (m && m[1].length < indent) { names.add(m[2]); indent = m[1].length; }
  }
  for (let i = Math.max(0, start - 1); i < Math.min(lines.length, end); i++) {
    const m = DEF.exec(lines[i]);
    if (m) names.add(m[2]);
  }
  return [...names];
}

function git(root, args, attrs) {
  const pre = attrs ? ["-c", `core.attributesFile=${attrs}`] : [];
  return execFileSync("git", ["-C", root, ...pre, ...args], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"],
    timeout: 20000 });
}

// -> [{sha, date, subject, file, header, hunk}] for one file, newest first
export function fileHunks(root, file, { maxCommits = MAX_COMMITS } = {}) {
  const driver = DRIVERS[path.extname(file).toLowerCase()];
  let attrs = null;
  if (driver) {
    attrs = path.join(os.tmpdir(), `kell-area-attrs-${process.pid}`);
    try { fs.writeFileSync(attrs, `*${path.extname(file)} diff=${driver}\n`); } catch { attrs = null; }
  }
  let out = "";
  try {
    out = git(root, ["log", "-p", "--full-history", "--no-merges", "--no-color", "--no-ext-diff", "-U2", `-n${maxCommits}`, "--date=short",
                     "--format=%x1eCOMMIT %H%x1f%ad%x1f%s", "--", file], attrs);
  } catch { return []; }
  const hunks = [];
  for (const block of out.split("\x1e").filter(Boolean)) {
    const nl = block.indexOf("\n");
    const head = block.slice(0, nl).replace(/^COMMIT /, "").split("\x1f");
    if (head.length < 3) continue;
    const [sha, date, subject] = head;
    const body = block.slice(nl + 1);
    const parts = body.split(/^(?=@@ )/m).slice(1);
    for (const p of parts) {
      const m = /^@@ [^@]+ @@ ?(.*)$/m.exec(p);
      const lines = p.replace(/\n+$/, "").split("\n");
      if (lines.length - 1 > HUNK_LINES_MAX) continue;
      // a diff of the whole file is not a pattern (a file added, or the tree replaced at once)
      if (!/^[-+]/m.test(lines.slice(1).join("\n")) || !lines.slice(1).some((l) => l.startsWith(" "))) continue;
      // git's header names the definition above the hunk's FIRST line, which with context lines can be the function before
      // the changed one: follow definitions inside the hunk up to its first changed line
      let header = (m && m[1]) || "";
      for (const l of lines.slice(1)) {
        if (/^[-+]/.test(l)) break;
        if (DEF.test(l.slice(1))) header = l.slice(1).trim();
      }
      hunks.push({ sha, date, subject, file, header, hunk: lines.join("\n") });
    }
  }
  return hunks;
}

// The cheap agent's `history` tool (lib/agent/step_agent.mjs): past changes to one function of a file, or to the file when no
// function is named. locate's guess of the place is often wrong (4 of 6 failed jsonargparse issues, cache-full-2), so the agent
// asks once it has found the code itself.
export function historyFor(root, file, fn, { budget = AREA_HISTORY_MAX, maxCommits = MAX_COMMITS } = {}) {
  const f = String(file || "").replace(/\\/g, "/").replace(/^\.\//, "");
  if (!f) return "";
  return render(root, new Map([[f, fn ? new Set([String(fn)]) : null]]), { budget, maxCommits });
}

// spots: [{file, start, end}] (lib/locate.mjs). -> the history text, "" when there is none.
export function areaHistory(root, spots, { budget = AREA_HISTORY_MAX, maxCommits = MAX_COMMITS } = {}) {
  if (!root || !Array.isArray(spots) || !spots.length) return "";
  const wanted = new Map();                       // file -> Set of function names
  for (const s of spots) {
    if (!s || !s.file) continue;
    let text = "";
    try { text = fs.readFileSync(path.join(root, s.file), "utf8"); } catch { continue; }
    const names = enclosingNames(text, Number(s.start) || 1, Number(s.end) || Number(s.start) || 1);
    if (!names.length) continue;
    if (!wanted.has(s.file)) wanted.set(s.file, new Set());
    for (const n of names) wanted.get(s.file).add(n);
  }
  return render(root, wanted, { budget, maxCommits });
}

// wanted: file -> Set of function names, or null for any change to the file
function render(root, wanted, { budget, maxCommits }) {
  const picked = [];
  for (const [file, names] of wanted) {
    const perFn = new Map();
    const seenCommit = new Set();
    for (const h of fileHunks(root, file, { maxCommits })) {
      const fn = names ? [...names].find((n) => new RegExp(`(^|[^\\w$])${n.replace(/[$]/g, "\\$")}($|[^\\w$])`).test(h.header))
        : (h.header.match(/(?:def|class|function|func|fn)\s+([A-Za-z_$][\w$]*)/) || [null, "(module)"])[1];
      if (!fn || seenCommit.has(h.sha)) continue;
      if ((perFn.get(fn) || 0) >= PER_FUNCTION * 3) continue;
      perFn.set(fn, (perFn.get(fn) || 0) + 1);
      seenCommit.add(h.sha);
      picked.push({ ...h, fn, fix: FIX.test(h.subject) });
    }
  }
  if (!picked.length) return "";
  // bug fixes first, then the newest; at most PER_FUNCTION per function and ENTRIES_MAX in all
  picked.sort((a, b) => (b.fix - a.fix) || String(b.date).localeCompare(String(a.date)));
  const count = new Map();
  const chosen = [];
  const bodies = new Set();                       // the same change twice (a merge brought it in again, a cherry-pick)
  for (const h of picked) {
    if (chosen.length >= ENTRIES_MAX) break;
    const k = `${h.file}::${h.fn}`;
    if ((count.get(k) || 0) >= PER_FUNCTION) continue;
    const body = h.hunk.split("\n").filter((l) => /^[-+]/.test(l)).join("\n");
    if (bodies.has(body)) continue;
    bodies.add(body);
    count.set(k, (count.get(k) || 0) + 1);
    chosen.push(h);
  }
  const head = "Earlier changes to the same functions in this repository's history (its own commits, newest bug fixes first). " +
    "They show how this code is usually changed -- the helpers it uses and where fixes go; use what applies:";
  let out = head;
  for (const h of chosen) {
    const entry = `\n\n${h.date} ${h.subject} (${h.file}, in ${h.fn})\n\`\`\`diff\n${h.hunk}\n\`\`\``;
    if (out.length + entry.length > budget) continue;
    out += entry;
  }
  return out === head ? "" : out;
}
