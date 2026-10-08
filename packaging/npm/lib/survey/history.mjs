// Library bootstrap from git history (docs/plan_2026-10_priors_library_survey.md §4.4, §4.6): this
// repository's own past fixes, with their real diffs, as `library.md` entries (`outcome=historical`).
// The experiment's winning setting -- same-repo past fixes with diffs -- built locally, no server.
//
// Bounded by design: the 2 years before HEAD's commit date, at most 5,000 commits, one `git log --name-only` pass, the
// fix-commit filter applied BEFORE any diff is read, then at most `maxEntries` diffs. Files that look
// like secrets, lockfiles, generated or vendored code never enter a solution; a hunk with a secret-looking
// value is dropped and the entry says `redacted=1`.
//
// Grammar (plan §3.2, shared with Workstream B):
//   GOAL|L-<id>|<title>|unit=<unit_path or .>|g=-|outcome=historical|verified_at=<date>|route=-|commit=<sha>
//   PROC|L-<id>.p1|<name>|p=-|solution=solutions/L-<id>.diff|touches=path#sha=<7>,...
//   STEP|L-<id>.p1:1|action|<do>|check=<cmd or tests:...>
const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
import fs from "node:fs";
import path from "node:path";
import { git } from "./files.mjs";
import { GENERATED_FILE_RE } from "./units.mjs";
import { hasSecret } from "./validate.mjs";
import { writeAtomic } from "./claims.mjs";

const FIX_RE = /\b(fix(e[sd]|ing)?|bug(fix)?|hotfix|regression|crash(es|ed)?|errors?|fault|broken|incorrect(ly)?|wrong|leak|race|deadlock|panic|exception|resolves?\s+#\d+|closes?\s+#\d+|fixes\s+#\d+)\b/i;
const SKIP_RE = /\b(typo|readme|changelog|bump|release|version\s+\d|merge\s+(branch|pull)|chore\(deps|dependabot|renovate|deps:\s|format(ting)?|prettier|lint(ing)?\s+fix|whitespace|docs?:|ci:|wip)\b/i;
const CODE_RE = /\.(py|pyi|ts|tsx|mts|cts|js|jsx|mjs|cjs|go|rs|java|kt|kts|scala|cs|fs|vb|c|h|cc|cpp|cxx|hpp|hh|m|mm|swift|rb|php|ex|exs|erl|dart|lua|r|jl|hs|ml|clj|sh|bash|vue|svelte|sql|zig|sol|groovy|pl|cu)$/i;
const TEST_RE = /(^|\/)(tests?|__tests__|specs?|testdata)\/|(^|\/)test_[^/]+\.py$|_test\.(py|go|exs?|rs)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs|swift)$|_spec\.rb$/;
const SECRET_FILE_RE = /(^|\/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|ed25519|ecdsa)|credentials(\.json)?|secrets?\.(ya?ml|json|toml)|.*\.neon_shards\.env.*)$/i;

/**
 * Is this commit a fix? A conventional-commit subject decides by its type (`fix:`/`fix(x):` yes; `feat:`,
 * `perf:`, `refactor:` ... no, however the body is worded). Otherwise the SUBJECT must read like a fix; the
 * body counts only for an explicit issue reference ("Fixes #12"). Bodies of features mention errors all the time.
 */
export function isFixCommit(subject, body = "") {
  const s = String(subject || "").trim();
  if (SKIP_RE.test(s)) return false;
  const cc = s.match(/^([a-z]+)(\([^)]*\))?!?:\s/i);
  if (cc) return /^(fix|bugfix|hotfix)$/i.test(cc[1]);
  if (FIX_RE.test(s)) return true;
  return /\b(fix(es|ed)?|close[sd]?|resolve[sd]?)\s+#\d+/i.test(String(body).split("\n").slice(0, 6).join("\n"));
}

export const LIBRARY_MAX_BYTES = 64 * 1024;
// `since` unset means `sinceYears` before HEAD's own commit date, not before today: "2.years.ago" mined nothing in an
// older checkout or a repository idle for two years. An explicit `since` (any git date) is passed through as given.
export const HISTORY_DEFAULTS = { since: null, sinceYears: 2, maxCommits: 5000, maxEntries: 150, maxFilesPerFix: 20, maxDiffBytes: 64 * 1024 };

/** ISO date `years` before HEAD's committer date, or null when HEAD has no readable date. */
export function sinceBeforeHead(root, years = HISTORY_DEFAULTS.sinceYears) {
  const iso = (git(root, ["log", "-1", "--format=%cI", "HEAD"]) || "").trim();
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return null;
  d.setUTCFullYear(d.getUTCFullYear() - years);
  return d.toISOString();
}

/**
 * Fix commits since `since` (or since `fromHead` when incremental), scored and capped.
 * @returns {{candidates: object[], scanned: number, head: string|null}}
 */
export function findFixCommits(root, opts = {}) {
  const o = { ...HISTORY_DEFAULTS, ...opts };
  const head = (git(root, ["rev-parse", "HEAD"]) || "").trim() || null;
  if (!head) return { candidates: [], scanned: 0, head: null };
  const range = o.fromHead && o.fromHead !== head ? [`${o.fromHead}..HEAD`] : o.fromHead === head ? null : ["HEAD"];
  if (!range) return { candidates: [], scanned: 0, head };
  const since = o.since || sinceBeforeHead(root, o.sinceYears);
  const out = git(root, ["log", ...range, ...(since ? [`--since=${since}`] : []), `--max-count=${o.maxCommits}`, "--no-merges", "--no-renames",
    "--format=%x1e%H%x1f%cs%x1f%an%x1f%s%x1f%b%x1d", "--name-only", "--", "."]);
  if (out == null) return { candidates: [], scanned: 0, head };
  const candidates = [];
  let scanned = 0;
  for (const rec of out.split("\x1e")) {
    if (!rec.trim()) continue;
    scanned++;
    const [meta, names = ""] = rec.split("\x1d");
    const [sha, date, author, subject, body = ""] = meta.split("\x1f");
    if (!sha || /\[bot\]|dependabot|renovate/i.test(author)) continue;
    const msg = `${subject}\n${body.split("\n").slice(0, 6).join("\n")}`;
    if (!isFixCommit(subject, body)) continue;
    const files = names.split("\n").map((s) => s.trim()).filter(Boolean);
    const code = files.filter((f) => CODE_RE.test(f) && !TEST_RE.test(f) && !GENERATED_FILE_RE.test(f) && !SECRET_FILE_RE.test(f) && !o.excluded?.(f));
    const tests = files.filter((f) => TEST_RE.test(f) && !o.excluded?.(f));
    if (!code.length || code.length > o.maxFilesPerFix) continue;
    candidates.push({ sha, date, subject: subject.trim(), code, tests, score: (tests.length ? 2 : 0) + (/#\d+/.test(msg) ? 1 : 0) });
  }
  // Prefer fixes that came with tests (a verified fix) and then the most recent.
  candidates.sort((a, b) => b.score - a.score || cmpStr(b.date, a.date));
  return { candidates: candidates.slice(0, o.maxEntries), scanned, head };
}

/**
 * Raw diffs of many commits in a few `git show` processes (50 commits each) instead of one per commit --
 * 150 processes cost ~50 s on Windows. Returns sha -> the commit's full patch text.
 */
export function showMany(root, shas, batch = 50) {
  const out = new Map();
  for (let i = 0; i < shas.length; i += batch) {
    const chunk = shas.slice(i, i + batch);
    const raw = git(root, ["show", "--format=%x1e%H", "--no-color", "--no-ext-diff", "--unified=3", "--no-renames", ...chunk]);
    if (raw == null) continue;
    for (const rec of raw.split("\x1e")) {
      const nl = rec.indexOf("\n");
      if (nl < 0) continue;
      out.set(rec.slice(0, nl).trim(), rec.slice(nl + 1));
    }
  }
  return out;
}

/** The diff of one fix, restricted to its code and test files, secrets dropped. */
export function fixDiff(root, c, maxBytes = HISTORY_DEFAULTS.maxDiffBytes, patch = null) {
  const files = new Set([...c.code, ...c.tests]);
  const raw = patch ?? git(root, ["show", "--format=", "--no-color", "--no-ext-diff", "--unified=3", c.sha, "--", ...files]);
  if (raw == null) return null;
  // Keep only the fix's own code and test files (a batched patch holds every file the commit touched).
  const parts = raw.split(/^(?=diff --git )/m).filter((p) => {
    const m = p.match(/^diff --git a\/(.+?) b\/(.+)$/m);
    return m && (files.has(m[2]) || files.has(m[1]));
  });
  let redacted = false;
  const kept = [];
  for (const p of parts) {
    if (/^Binary files /m.test(p)) continue;
    if (hasSecret(p)) { redacted = true; continue; }
    kept.push(p);
  }
  let text = kept.join("");
  let truncated = false;
  if (Buffer.byteLength(text) > maxBytes) {
    text = Buffer.from(text).subarray(0, maxBytes).toString("utf8").replace(/[^\n]*$/, "");
    truncated = true;
  }
  return { text, redacted, truncated };
}

/** Blob shas of many `<commit>:<path>` specs in ONE git process (cat-file --batch-check). */
function blobsAt(root, specs) {
  const out = new Map();
  if (!specs.length) return out;
  const res = git(root, ["cat-file", "--batch-check=%(objectname) %(objecttype)"], { input: specs.join("\n") + "\n" });
  const lines = (res || "").split(/\r?\n/);
  specs.forEach((s, i) => {
    const m = (lines[i] || "").match(/^([0-9a-f]{40}) blob$/);
    if (m) out.set(s, m[1].slice(0, 7));
  });
  return out;
}

// A literal `|` in a field is written as `¦` (U+00A6), as in claims.mjs.
const clean = (s) => String(s ?? "").replace(/[\r\n]+/g, " ").replace(/\|/g, "¦").replace(/\s+/g, " ").trim();

function checkFor(c, unitPath) {
  const t = c.tests;
  if (!t.length) return "-";
  if (t.every((f) => f.endsWith(".py"))) return `pytest ${t.slice(0, 4).join(" ")}`;
  if (t.every((f) => f.endsWith("_test.go"))) return `go test ./${path.posix.dirname(t[0])}/...`;
  if (t.every((f) => f.endsWith(".rs"))) return "cargo test";
  void unitPath;
  return `tests:${t.slice(0, 4).join(",")}`;
}

/** Parse library.md into blocks keyed by GOAL id (lines kept verbatim). */
export function readLibrary(file) {
  let text = "";
  try { text = fs.readFileSync(file, "utf8"); } catch { return { header: [], blocks: new Map() }; }
  const header = [];
  const blocks = new Map();
  let cur = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("GOAL|")) { cur = line.split("|")[1]; blocks.set(cur, [line]); continue; }
    if (cur && /^(PROC|STEP)\|/.test(line) && line.split("|")[1].startsWith(cur)) { blocks.get(cur).push(line); continue; }
    if (!cur && line.trim()) header.push(line);
  }
  return { header, blocks };
}

/**
 * Mine and write. Returns {added, scanned, head, redacted, truncated}.
 * @param {string} root
 * @param {string} stealthDir
 * @param {{unitOf:(file:string)=>string, excluded:(file:string)=>boolean, fromHead?:string, maxEntries?:number}} opts
 */
export function mineHistory(root, stealthDir, opts) {
  const { candidates, scanned, head } = findFixCommits(root, opts);
  const libFile = path.join(stealthDir, "library.md");
  const lib = readLibrary(libFile);
  let added = 0;
  let redacted = 0;
  let truncated = 0;
  const archiveFile = path.join(stealthDir, "library", "archive-historical.md");
  const archive = readLibrary(archiveFile);
  const fresh = candidates.filter((c) => !lib.blocks.has(`L-${c.sha.slice(0, 7)}`) && !archive.blocks.has(`L-${c.sha.slice(0, 7)}`));
  const blobs = blobsAt(root, fresh.flatMap((c) => c.code.slice(0, 12).map((f) => `${c.sha}:${f}`)));
  const blobAt = (_root, sha, f) => blobs.get(`${sha}:${f}`) || null;
  const patches = showMany(root, fresh.map((c) => c.sha));
  for (const c of fresh) {
    const id = `L-${c.sha.slice(0, 7)}`;
    const diff = fixDiff(root, c, HISTORY_DEFAULTS.maxDiffBytes, patches.get(c.sha) ?? null);
    if (!diff || !diff.text.trim()) continue;
    if (diff.redacted) redacted++;
    if (diff.truncated) truncated++;
    const units = c.code.map(opts.unitOf);
    const counts = new Map();
    for (const u of units) counts.set(u, (counts.get(u) || 0) + 1);
    const unit = [...counts.entries()].sort((a, b) => b[1] - a[1] || cmpStr(a[0], b[0]))[0][0];
    const touches = c.code.slice(0, 12).map((f) => `${f}#sha=${blobAt(root, c.sha, f) || "-"}`).join(",");
    const flags = [diff.redacted ? "redacted=1" : null, diff.truncated ? "diff=truncated" : null].filter(Boolean);
    const title = clean(c.subject).slice(0, 160);
    lib.blocks.set(id, [
      ["GOAL", id, title, `unit=${unit}`, "g=-", "outcome=historical", `verified_at=${c.date}`, "route=-", `commit=${c.sha}`].join("|"),
      ["PROC", `${id}.p1`, title, "p=-", `solution=solutions/${id}.diff`, `touches=${touches}`, ...flags].join("|"),
      ["STEP", `${id}.p1:1`, "action", title, `check=${clean(checkFor(c, unit))}`].join("|"),
    ]);
    writeAtomic(path.join(stealthDir, "library", "solutions", `${id}.diff`), diff.text);
    added++;
  }
  if (added || !fs.existsSync(libFile)) {
    const header = lib.header.length ? lib.header : [
      "# library.md -- this repository's solved problems. One block per entry, sorted by id; append-only.",
      "# GOAL|<id>|<title>|unit=|g=|outcome=|verified_at=|route=   PROC|<id>.p1|<name>|p=|solution=|touches=path#sha=   STEP|<id>.p1:<n>|<kind>|<do>|check=",
      "# outcome=historical entries were mined from git history by `stealthlab-mcp survey` (fix commits; diffs in library/solutions/).",
    ];
    // library.md stays within its 64 KB budget (plan §3.1): every non-historical entry (written by the agent after
    // a verified task) stays; historical entries fill the rest newest first; older ones go to the archive.
    const all = new Map([...archive.blocks, ...lib.blocks]);
    const isHistorical = (b) => /\|outcome=historical\|/.test(b[0]);
    const dateOf = (b) => (b[0].match(/\|verified_at=([^|]*)/) || [])[1] || "";
    const sizeOf = (b) => Buffer.byteLength(b.join("\n")) + 2;
    let budget = LIBRARY_MAX_BYTES - Buffer.byteLength(header.join("\n")) - 4;
    const keep = new Map();
    const rest = new Map();
    for (const [id, b] of all) if (!isHistorical(b)) { keep.set(id, b); budget -= sizeOf(b); }
    const hist = [...all.entries()].filter(([, b]) => isHistorical(b)).sort((a, b) => cmpStr(dateOf(b[1]), dateOf(a[1])) || cmpStr(a[0], b[0]));
    for (const [id, b] of hist) {
      if (budget - sizeOf(b) >= 0) { keep.set(id, b); budget -= sizeOf(b); } else rest.set(id, b);
    }
    const render = (m) => [...m.keys()].sort().map((k) => m.get(k).join("\n")).join("\n\n");
    const body = render(keep);
    writeAtomic(libFile, header.join("\n") + "\n\n" + body + (body ? "\n" : ""));
    if (rest.size || fs.existsSync(archiveFile)) {
      writeAtomic(archiveFile, "# archive-historical.md -- older past fixes moved out of library.md to keep it within 64 KB. Same grammar; grep it the same way.\n\n" + render(rest) + (rest.size ? "\n" : ""));
    }
  }
  return { added, scanned, head, redacted, truncated, candidates: candidates.length };
}
