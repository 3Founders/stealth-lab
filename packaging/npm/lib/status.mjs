// `kell status`: a COMPUTED status for every `.kell/run.md` node and every `.kell/library.md` entry:
// verified | open | failed | contested | expired. Nobody types it. A run.md node marked `done` by hand is a claim,
// not evidence; the status comes from recorded check runs (`.kell/evidence.md`) and from what the target relies on.
//
// The same design as docs/verification_changes_prop.md section 4.2, local first: the server side has no evidence flowing
// into it yet, while dispatch, library.md and run.md already carry the checks. The evidence methods use the names of the
// server's verification ladder (backend/app/services/verification.py), so a line maps one to one onto a
// `verification_results` row later.
//
// Evidence line (append-only, same pipe grammar as the other .kell files; `check=` is last and takes the rest):
//   EVIDENCE|<target id>|deterministic_check|pass|fail|at=<iso>|commit=<HEAD sha or ->|tree=<tree id or ->|hashes=<path#sha=<hex>,...>|where=<worktree|in-place>|check=<cmd>
// `tree` is the id of HEAD's tree plus the uncommitted tracked diff (what `git write-tree` would give for it), computed in
// memory (workTreeId), so a read never writes git objects. It only FLAGS that code changed since the check ran
// (`changed_since`); it never expires anything, because every commit would expire everything.
// `hashes` are the files the target relies on (a node's claim sources, a library entry's touches), recorded as git blob
// shas, the kind library.md `touches=` already uses. A line holds only the command and its exit verdict: never output,
// prompt text or secrets.
//
// Honest limits (also in docs/stealth_status_results.md):
//   * "current code" means the cited files' content hash, not the whole tree. A check that depends on a file the target
//     does not cite can go stale without this noticing; cite what the check relies on.
//   * `--run` runs each check in a throwaway worktree (HEAD plus the uncommitted tracked diff), so a check cannot change
//     the checkout. Untracked files are not in that worktree; use --in-place for a check that needs them.
//   * Nothing here runs inside the prompt hook; every read error fails open to `open`, never to `verified`.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync, spawn } from "node:child_process";
import { claimIds, nearestStealthRoot, parseRun, splitLine } from "./plan_check.mjs";
import { kvGet, readPage, claimsOf } from "./survey/claims.mjs";
import { hasSecret, makeContext, validateClaim } from "./survey/validate.mjs";
import { listFiles, readText } from "./survey/files.mjs";
import { resolveUnits } from "./survey/units.mjs";
import { currentShas, parseLibrary } from "./library.mjs";

export const STATUSES = ["verified", "open", "failed", "contested", "expired"];
export const EVIDENCE_METHODS = new Set(["deterministic_check", "independent_agent"]);
const RESULTS = new Set(["pass", "fail"]);
const CHECK_MAX = 500;
const SHA_LEN = 12;
export const EVIDENCE_HEADER =
  "# evidence.md -- one line per check run by `kell status --run`. Append-only. No output, no prompt text, no secrets.\n" +
  "# EVIDENCE|<target id>|<deterministic_check|independent_agent>|<pass|fail>|at=<iso>|commit=<sha or ->|tree=<tree id or ->|hashes=<path#sha=<hex>,...>|where=<worktree|in-place>|check=<command>\n";

// ---------------------------------------------------------------------------------------------- evidence lines

const okPath = (p) => Boolean(p) && !/[|,#\r\n]/.test(p);

export function parseHashes(v) {
  if (!v || v === "-") return [];
  return v.split(",").map((part) => {
    const m = /^(.+?)#sha=([0-9a-f]{4,64})$/.exec(part.trim());
    return m ? { path: m[1], sha: m[2] } : null;
  }).filter(Boolean);
}

export function parseEvidence(text) {
  const out = [];
  String(text || "").split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    if (!line.startsWith("EVIDENCE|")) return;
    const { pos, kv } = splitLine(line, 4);
    const [, target, method, result] = pos;
    if (!target || !EVIDENCE_METHODS.has(method) || !RESULTS.has(result)) return;
    out.push({ n: out.length, line: i + 1, target, method, result, at: kv.at || "", commit: kv.commit || "-",
      tree: kv.tree && kv.tree !== "-" ? kv.tree : null, where: kv.where === "in-place" || kv.where === "worktree" ? kv.where : null,
      hashes: parseHashes(kv.hashes), check: (kv.check || "").trim() });
  });
  return out;
}

// -> the line, or null when it must not be written (a secret-looking check, no target, a bad result).
export function renderEvidence(e) {
  if (!e.target || !RESULTS.has(e.result) || !EVIDENCE_METHODS.has(e.method || "deterministic_check")) return null;
  const check = String(e.check || "").replace(/[\r\n]+/g, " ").trim().slice(0, CHECK_MAX);
  if (hasSecret(check) || hasSecret(String(e.target))) return null;
  const hashes = (e.hashes || []).filter((h) => okPath(h.path) && h.sha).map((h) => `${h.path}#sha=${String(h.sha).slice(0, SHA_LEN)}`).join(",") || "-";
  const where = e.where === "in-place" || e.where === "worktree" ? e.where : null;
  return ["EVIDENCE", e.target, e.method || "deterministic_check", e.result, `at=${e.at || new Date().toISOString()}`,
    `commit=${e.commit || "-"}`, `tree=${/^[0-9a-f]{40}$/.test(e.tree || "") ? e.tree : "-"}`, `hashes=${hashes}`,
    ...(where ? [`where=${where}`] : []), `check=${check}`].join("|");
}

export function appendEvidence(root, lines, dir = path.join(root, ".kell")) {
  if (!lines.length) return 0;
  const file = path.join(dir, "evidence.md");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const fresh = !fs.existsSync(file);
  fs.appendFileSync(file, (fresh ? EVIDENCE_HEADER : "") + lines.join("\n") + "\n");
  return lines.length;
}

// ---------------------------------------------------------------------------------------------- hashes and git

function gitText(root, args) {
  try {
    return execFileSync("git", ["-C", root, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], windowsHide: true, timeout: 15000 }).trim();
  } catch {
    return null;
  }
}

export const headSha = (root) => gitText(root, ["rev-parse", "HEAD"]) || "-";

// The id of HEAD's tree with the uncommitted tracked diff applied (new tracked files, edits, deletions; untracked files
// are not in it), the tree the --run worktree is made from. Built in memory the way `git write-tree` builds it, so reading
// the status writes nothing into .git. null when this is not a git work tree or anything goes wrong (the flag is then unknown).
export function workTreeId(root) {
  try {
    const run = (args, input) => execFileSync("git", ["-C", root, ...args], { input, stdio: ["pipe", "pipe", "ignore"],
      windowsHide: true, timeout: 60000, maxBuffer: 512 << 20 });
    const entries = new Map();                                   // path -> {mode, sha}
    for (const rec of run(["ls-tree", "-r", "-z", "HEAD"]).toString("utf8").split("\0")) {
      const m = /^(\d+) (\w+) ([0-9a-f]{40})\t(.+)$/s.exec(rec);
      if (m) entries.set(m[4], { mode: m[1], sha: m[3] });
    }
    const changed = run(["diff", "--name-only", "-z", "--no-renames", "HEAD"]).toString("utf8").split("\0").filter(Boolean);
    const regular = [];
    for (const rel of changed) {
      let st;
      try { st = fs.lstatSync(path.join(root, rel)); } catch { entries.delete(rel); continue; }
      if (st.isSymbolicLink()) entries.set(rel, { mode: "120000", sha: rawBlobSha(Buffer.from(fs.readlinkSync(path.join(root, rel)))) });
      else if (st.isFile()) regular.push(rel);
      else entries.delete(rel);
    }
    if (regular.length) {
      const shas = run(["hash-object", "--stdin-paths"], regular.join("\n") + "\n").toString("utf8").trim().split(/\r?\n/);
      regular.forEach((rel, i) => {
        const was = entries.get(rel);
        entries.set(rel, { mode: was?.mode === "100755" || (process.platform !== "win32" && fs.statSync(path.join(root, rel)).mode & 0o111) ? "100755" : "100644", sha: shas[i] });
      });
    }
    const top = { files: new Map(), dirs: new Map() };
    for (const [rel, e] of entries) {
      const parts = rel.split("/");
      let node = top;
      for (const d of parts.slice(0, -1)) {
        if (!node.dirs.has(d)) node.dirs.set(d, { files: new Map(), dirs: new Map() });
        node = node.dirs.get(d);
      }
      node.files.set(parts[parts.length - 1], e);
    }
    const hashTree = (node) => {
      const list = [];
      for (const [name, e] of node.files) list.push({ key: Buffer.from(name), name, mode: e.mode, sha: e.sha });
      for (const [name, child] of node.dirs) list.push({ key: Buffer.from(name + "/"), name, mode: "40000", sha: hashTree(child) });
      list.sort((a, b) => Buffer.compare(a.key, b.key));
      const body = Buffer.concat(list.flatMap((x) => [Buffer.from(`${x.mode} ${x.name}\0`), Buffer.from(x.sha, "hex")]));
      return crypto.createHash("sha1").update(`tree ${body.length}\0`).update(body).digest("hex");
    };
    return hashTree(top);
  } catch (e) {
    if (process.env.STEALTH_STATUS_DEBUG) process.stderr.write(`workTreeId: ${e.message}
`);
    return null;
  }
}

function rawBlobSha(buf) {
  return crypto.createHash("sha1").update(`blob ${buf.length}\0`).update(buf).digest("hex");
}

// path -> full blob sha now; git hash-object when this is a git work tree (the kind library.md records), else computed
// the same way from the bytes. A missing file is absent from the map.
export function fileShas(root, paths) {
  const want = [...new Set(paths)].filter(Boolean);
  const out = new Map();
  if (!want.length) return out;
  try {
    for (const [p, sha] of currentShas(root, want)) if (sha) out.set(p, sha);
    if (out.size === want.filter((p) => fs.existsSync(path.join(root, p))).length) return out;
  } catch { /* not a git work tree: fall through to the byte hash */ }
  out.clear();
  for (const p of want) {
    try {
      const abs = path.join(root, p);
      if (fs.statSync(abs).isFile()) out.set(p, rawBlobSha(fs.readFileSync(abs)));
    } catch { /* missing or unreadable: absent */ }
  }
  return out;
}

const shaMatches = (now, recorded) => Boolean(now) && Boolean(recorded) && (now.startsWith(recorded) || recorded.startsWith(now));

// ---------------------------------------------------------------------------------------------- claims

function expandClaimSpec(spec, ids) {
  if (!spec || spec === "-") return [];
  const pages = new Map();
  for (const [id, { page }] of ids) {
    if (!pages.has(page)) pages.set(page, []);
    pages.get(page).push(id);
  }
  const out = new Set();
  for (const part of spec.split(",").map((s) => s.trim()).filter(Boolean)) {
    const [a, b] = part.split("..");
    if (b === undefined) { out.add(a); continue; }
    const pa = ids.get(a)?.page;
    if (pa && pa === ids.get(b)?.page) {
      const list = pages.get(pa);
      const i = list.indexOf(a), j = list.indexOf(b);
      if (i >= 0 && j >= i) for (const id of list.slice(i, j + 1)) out.add(id);
      else { out.add(a); out.add(b); }
    } else { out.add(a); out.add(b); }
  }
  return [...out];
}

function loadClaims(stealthDir) {
  const byId = new Map();
  const pages = [path.join(stealthDir, "claims.md")];
  try {
    for (const f of fs.readdirSync(path.join(stealthDir, "claims"))) if (f.endsWith(".md")) pages.push(path.join(stealthDir, "claims", f));
  } catch { /* no unit pages */ }
  for (const file of pages) {
    try {
      for (const c of claimsOf(readPage(file))) if (!byId.has(c.id)) byId.set(c.id, c);
    } catch { /* unreadable page: its claims are simply missing */ }
  }
  return byId;
}

export const claimSourcePath = (c) => {
  const m = /^([^:]+):\d+/.exec(kvGet(c, "source") || "");
  return m && !m[1].startsWith("search") ? m[1] : null;
};

// id -> {ok, reason}. Uses the survey validator on a COPY of each claim: nothing is stamped or written.
function claimVerdicts(root, claimsById, ids) {
  let ctx = null;
  try {
    const listing = listFiles(root);
    listing.fileSet = new Set(listing.files);
    const resolved = resolveUnits(listing.files, (p) => readText(root, p), { submodules: listing.submodules, fileSet: listing.fileSet });
    ctx = makeContext(root, listing, resolved.units);
  } catch { ctx = null; }
  const out = new Map();
  for (const id of ids) {
    const c = claimsById.get(id);
    if (!c) { out.set(id, { ok: false, reason: `claim ${id} is not on any claims page` }); continue; }
    if (c.status === "stale") { out.set(id, { ok: false, reason: `claim ${id} is marked stale` }); continue; }
    if (!ctx) { out.set(id, { ok: true, unverifiable: true }); continue; }
    try {
      const copy = structuredClone(c);
      const r = validateClaim(copy, ctx, {});
      if (r.action === "reject") out.set(id, { ok: false, reason: `claim ${id} is no longer kept: ${r.reason}` });
      else if (r.action === "stale" || copy.status === "stale") out.set(id, { ok: false, reason: `claim ${id} no longer holds (its cited line changed)` });
      else out.set(id, { ok: true, unverifiable: r.action === "unverifiable" });
    } catch { out.set(id, { ok: true, unverifiable: true }); }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- targets

const depsOf = (n) => {
  const d = (n?.deps || "").trim();
  return !d || d === "-" ? [] : d.split(",").map((s) => s.trim()).filter(Boolean);
};

function readOr(file, fallback = "") {
  try { return fs.readFileSync(file, "utf8"); } catch { return fallback; }
}

// The things to compute a status for, without any hashing yet.
export function collectTargets(root, dir = path.join(root, ".kell")) {
  const targets = [];
  const claimsById = loadClaims(dir);
  const ids = claimIds(dir);
  const runText = readOr(path.join(dir, "run.md"), null);
  if (runText !== null) {
    const { nodes } = parseRun(runText.split(/\r?\n/));
    for (const [id, n] of nodes) {
      const check = (n.check || "").trim();
      const claimList = expandClaimSpec(n.claims, ids);
      const files = [...new Set(claimList.map((cid) => claimsById.get(cid)).filter(Boolean).map(claimSourcePath).filter(Boolean))];
      targets.push({ id, kind: "node", title: n.what || "", handStatus: n.status, checks: check && check !== "-" ? [check] : [],
        claims: claimList, files, deps: depsOf(n), touches: [], synthetic: null, libStale: false });
    }
  }
  let lib = null;
  try { lib = parseLibrary(readOr(path.join(dir, "library.md"))); } catch { lib = null; }
  for (const e of lib?.entries || []) {
    const touches = e.procs.flatMap((p) => p.touches || []).filter((t) => t && t.path);
    const checks = [...new Set(e.procs.flatMap((p) => (p.steps || []).map((s) => s.check)).filter((c) => c && c !== "-"))];
    targets.push({ id: e.id, kind: "library", title: e.title || "", handStatus: e.status, checks, claims: [], files: [...new Set(touches.map((t) => t.path))],
      deps: [], touches, libStale: e.status === "stale", goalId: e.g || null, procedureId: e.procs.map((p) => p.p).find(Boolean) || null,
      // an entry written by dispatch or the Stop hook was verified when it was added: one pass, at the recorded hashes
      synthetic: e.verified_at && (e.outcome === "pass" || e.outcome === "fail") && touches.length
        ? { result: e.outcome, at: e.verified_at, hashes: touches.map((t) => ({ path: t.path, sha: t.sha })) } : null });
  }
  return { targets, claimsById };
}

// ---------------------------------------------------------------------------------------------- the computation

export function computeStatus(root, opts = {}) {
  const dir = opts.dir ? path.resolve(opts.dir) : path.join(root, ".kell");
  const { targets, claimsById } = collectTargets(root, dir);
  const evidence = parseEvidence(readOr(path.join(dir, "evidence.md")));
  const head = opts.head ?? headSha(root);
  let nowTree;                                                   // computed once, and only if some evidence line carries a tree
  const treeNow = () => (nowTree === undefined ? (nowTree = opts.tree !== undefined ? opts.tree : workTreeId(root)) : nowTree);

  const claimIdsNeeded = new Set(targets.flatMap((t) => t.claims));
  const verdicts = claimVerdicts(root, claimsById, claimIdsNeeded);
  const allPaths = new Set();
  for (const t of targets) for (const f of t.files) allPaths.add(f);
  for (const e of evidence) for (const h of e.hashes) allPaths.add(h.path);
  const now = opts.shas ?? fileShas(root, [...allPaths]);

  const byTarget = new Map();
  for (const e of evidence) {
    if (!byTarget.has(e.target)) byTarget.set(e.target, []);
    byTarget.get(e.target).push(e);
  }
  const isCurrent = (ev) => (ev.hashes.length
    ? ev.hashes.every((h) => shaMatches(now.get(h.path), h.sha))
    : ev.commit !== "-" && ev.commit === head);

  const byId = new Map(targets.map((t) => [t.id, t]));
  const memo = new Map();
  const visiting = new Set();

  const evaluate = (t) => {
    if (memo.has(t.id)) return memo.get(t.id);
    if (visiting.has(t.id)) return { status: "open", why: "dependency cycle" };
    visiting.add(t.id);
    const res = evaluateOne(t);
    visiting.delete(t.id);
    memo.set(t.id, res);
    return res;
  };

  const evaluateOne = (t) => {
    // 1. expired: something it relies on changed after its evidence was recorded
    const expired = [];
    for (const cid of t.claims) {
      const v = verdicts.get(cid);
      if (v && !v.ok) expired.push(v.reason);
    }
    if (t.libStale) expired.push("the library entry is marked stale (a file it touched changed)");
    for (const tt of t.touches) {
      if (tt.sha && tt.sha !== "-" && !shaMatches(now.get(tt.path), tt.sha)) expired.push(`${tt.path} changed since the entry was verified`);
    }
    const lines = byTarget.get(t.id) || [];
    const groups = new Map();                                    // check -> its lines, in file order
    for (const ev of lines) {
      if (!groups.has(ev.check)) groups.set(ev.check, []);
      groups.get(ev.check).push(ev);
    }
    for (const [check, list] of groups) {
      if (!isCurrent(list[list.length - 1])) expired.push(`the latest run of "${check || "(no check text)"}" was recorded against files that have changed`);
    }
    if (expired.length) return { status: "expired", why: [...new Set(expired)].join("; ") };

    // 2. no evidence: open
    const hasSynthetic = Boolean(t.synthetic);
    if (!groups.size && !hasSynthetic) {
      if (t.kind === "node" && !t.checks.length) return { status: "open", why: "no check (check=-)" };
      return { status: "open", why: t.handStatus === "done" ? "marked done by hand; no recorded check run" : "no recorded check run" };
    }

    // 3. failed / contested / pass, on the current code
    let anyFail = false, anyContested = false, anyPass = false;
    const checkPass = new Map();
    for (const [check, list] of groups) {
      const cur = list.filter(isCurrent);
      const p = cur.some((e) => e.result === "pass"), f = cur.some((e) => e.result === "fail");
      if (p && f) anyContested = true;
      else if (f) anyFail = true;
      else if (p) { anyPass = true; checkPass.set(check, true); }
    }
    if (t.synthetic) {
      if (t.synthetic.result === "fail") anyFail = true;
      else anyPass = true;
    }
    if (anyContested) return { status: "contested", why: "a pass and a fail were recorded on the same code" };
    if (anyFail) return { status: "failed", why: "the latest recorded run of its check failed on the current code" };
    if (!anyPass) return { status: "open", why: "no passing run on the current code" };
    if (t.kind === "node" && (!t.checks.length || !t.checks.every((c) => checkPass.has(c)))) return { status: "open", why: "no passing run of its check on the current code" };

    // 4. dependencies
    const notReady = [];
    for (const d of t.deps) {
      const dep = byId.get(d);
      const ds = dep ? evaluate(dep).status : "missing";
      if (ds !== "verified") notReady.push(`${d} is ${ds}`);
    }
    if (notReady.length) return { status: "open", why: `dependency not verified: ${notReady.join(", ")}` };
    // verified. Did code change since the passing runs, even though every file the check cites still matches? (a flag only)
    const flags = [];
    for (const list of groups.values()) {
      const lastPass = [...list].reverse().find((ev) => ev.result === "pass" && isCurrent(ev));
      if (lastPass) flags.push(lastPass.tree ? (treeNow() ? lastPass.tree !== treeNow() : null) : null);
    }
    const changedSince = flags.some((f) => f === true) ? true : flags.length && flags.every((f) => f === false) ? false : null;
    const base = hasSynthetic && !groups.size ? "passed when the entry was added, files unchanged since" : "its check passed on the current code";
    return { status: "verified", changed_since: changedSince,
      why: changedSince ? `verified (code changed since the check ran): ${base}` : base };
  };

  const rows = targets.map((t) => {
    const r = evaluate(t);
    return { id: t.id, kind: t.kind, status: r.status, ...(r.status === "verified" ? { changed_since: r.changed_since ?? null } : {}), why: r.why, title: t.title,
      ...(t.goalId ? { goal_id: t.goalId } : {}), hand: t.handStatus, checks: t.checks, deps: t.deps,
      evidence: (byTarget.get(t.id) || []).length };
  });
  const counts = Object.fromEntries(STATUSES.map((s) => [s, rows.filter((r) => r.status === s).length]));
  return { root, commit: head, targets: rows, counts, evidence_lines: evidence.length };
}

// ---------------------------------------------------------------------------------------------- running checks

export function runShell(cmd, { cwd, timeoutMs = 300000 } = {}) {
  return new Promise((resolve) => {
    let child, done = false;
    const finish = (code) => { if (!done) { done = true; clearTimeout(timer); resolve({ code }); } };
    try { child = spawn(cmd, { cwd, shell: true, windowsHide: true, stdio: "ignore" }); } catch { resolve({ code: null }); return; }
    const timer = setTimeout(() => { try { child.kill(); } catch { /* */ } finish(null); }, timeoutMs);
    child.on("error", () => finish(null));
    child.on("close", (code) => finish(code));
  });
}

// Run one check in a throwaway worktree (HEAD + the uncommitted tracked diff); in place when that is not possible.
// -> {code, where}: where says which one it was, and it goes into the evidence line.
async function defaultExec(root, cmd, { inPlace = false, timeoutMs } = {}) {
  if (inPlace) return { ...(await runShell(cmd, { cwd: root, timeoutMs })), where: "in-place" };
  let wt = null;
  try {
    const { createWorktree, defaultWorktreeRoot, headCommit, removeWorktree, repoTopLevel, workingTreeDiff } = await import("./exec/worktree.mjs");
    const repo = await repoTopLevel(root);
    const commit = await headCommit(repo);
    const patch = await workingTreeDiff(repo);
    const dir = path.join(defaultWorktreeRoot(), `status-${process.pid}-${Date.now().toString(36)}-${crypto.randomBytes(3).toString("hex")}`);
    await createWorktree({ repo, commit, dir, patch });
    wt = { repo, dir, removeWorktree };
    const rel = path.relative(repo, root);
    return { ...(await runShell(cmd, { cwd: path.join(dir, rel), timeoutMs })), where: "worktree" };
  } catch {
    return { ...(await runShell(cmd, { cwd: root, timeoutMs })), where: "in-place" };   // not a git work tree (or no commits yet)
  } finally {
    if (wt) { try { await wt.removeWorktree({ repo: wt.repo, dir: wt.dir }); } catch { /* best effort */ } }
  }
}

// The targets whose checks `--run` would run, and the distinct commands among them.
export function pickTargets(root, { dir = path.join(root, ".kell"), only = null } = {}) {
  const { targets } = collectTargets(root, dir);
  return targets.filter((t) => (!only || only.includes(t.id)) && t.checks.length && t.handStatus !== "skipped");
}
export const plannedCommands = (root, opts = {}) => [...new Set(pickTargets(root, opts).flatMap((t) => t.checks))];

// Runs the check of each target (or of the --only ids) and appends exactly one evidence line per check.
export async function runChecks(root, { dir = path.join(root, ".kell"), only = null, exec = defaultExec, inPlace = false, timeoutMs = 300000, now = () => new Date(), tree,
  env = process.env, fetchImpl } = {}) {
  const picked = pickTargets(root, { dir, only });
  const paths = new Set(picked.flatMap((t) => t.files));
  const shas = fileShas(root, [...paths]);
  const head = headSha(root);
  const treeId = tree !== undefined ? tree : workTreeId(root);   // before the checks run: the tree they ran on
  const lines = [], ran = [], skipped = [], records = [];
  for (const t of picked) {
    for (const check of t.checks) {
      const r = await exec(root, check, { inPlace, timeoutMs });
      if (r.code === null || r.code === undefined) { skipped.push({ id: t.id, check, why: "could not run or timed out" }); continue; }
      const where = r.where || (inPlace ? "in-place" : "worktree");
      const line = renderEvidence({ target: t.id, method: "deterministic_check", result: r.code === 0 ? "pass" : "fail", at: now().toISOString(), commit: head,
        tree: treeId, where, hashes: t.files.filter((f) => shas.has(f)).map((f) => ({ path: f, sha: shas.get(f).slice(0, SHA_LEN) })), check });
      if (!line) { skipped.push({ id: t.id, check, why: "not recorded (the check text looks like it holds a secret)" }); continue; }
      lines.push(line);
      records.push({ target: t.id, method: "deterministic_check", result: r.code === 0 ? "pass" : "fail", at: now().toISOString(), commit: head, tree: treeId,
        where, check, hashes: t.files.filter((f) => shas.has(f)).map((f) => ({ path: f, sha: shas.get(f).slice(0, SHA_LEN) })),
        targetKind: t.kind === "library" ? "library_entry" : "plan_node", goalId: t.goalId || undefined, procedureId: t.procedureId || undefined });
      ran.push({ id: t.id, check, result: r.code === 0 ? "pass" : "fail", where });
    }
  }
  appendEvidence(root, lines, dir);
  // Upload is off unless STEALTHLAB_EVIDENCE_UPLOAD=on. When it is on, a failure changes nothing here: the lines are already written,
  // the status is computed from them, and the report waits in the outbox (lib/evidence_record.mjs).
  let upload = null;
  try {
    const { uploadRecords } = await import("./evidence_record.mjs");
    upload = await uploadRecords(root, records, { env, fetchImpl });
  } catch { upload = null; }
  return { ran, skipped, appended: lines.length, in_place: ran.some((x) => x.where === "in-place"), ...(upload && (upload.built || upload.skipped) ? { upload } : {}) };
}

// ---------------------------------------------------------------------------------------------- compacting evidence.md

// Keeps, for each target and check, only the latest pass, the latest fail, and every line recorded on the tree as it is now.
// (Per check, not per target: a library entry with two checks would otherwise lose one check's pass and change status.)
// Refuses when evidence.md has uncommitted changes in git. The old file is kept as evidence.md.prev.
export function compactEvidence(root, { dir = path.join(root, ".kell"), tree } = {}) {
  const file = path.join(dir, "evidence.md");
  const text = readOr(file, null);
  if (text === null) return { ok: false, reason: "there is no evidence.md" };
  const dirty = gitText(dir, ["status", "--porcelain", "--", path.basename(file)]);
  if (dirty) return { ok: false, reason: "evidence.md has uncommitted changes; commit or discard them first" };
  const nowTree = tree !== undefined ? tree : workTreeId(root);
  const rawLines = text.split(/\r?\n/);
  if (rawLines.length && rawLines[rawLines.length - 1] === "") rawLines.pop();
  const parsed = rawLines.map((raw, i) => ({ i, ev: raw.trim().startsWith("EVIDENCE|") ? parseEvidence(raw.trim())[0] || null : null }));
  const latest = new Map();                                      // "target\0check\0result" -> index of the latest such line
  for (const { i, ev } of parsed) if (ev) latest.set(`${ev.target}\0${ev.check}\0${ev.result}`, i);
  const keep = new Set(latest.values());
  for (const { i, ev } of parsed) if (ev && nowTree && ev.tree === nowTree) keep.add(i);
  const out = parsed.filter(({ i, ev }) => !ev || keep.has(i)).map(({ i }) => rawLines[i]);
  const before = parsed.filter((p) => p.ev).length, after = out.length - parsed.filter((p) => !p.ev).length;
  if (after === before) return { ok: true, before, after, changed: false };
  fs.writeFileSync(`${file}.prev`, text);
  fs.writeFileSync(file, out.join("\n") + "\n");
  return { ok: true, before, after, changed: true };
}

// ---------------------------------------------------------------------------------------------- the server's view of a Goal (read only)

// The GOAL_STATUS line of stealth://goals/<id>/claims (backend/app/mcp_server/resources.py) -> {status, guarantees, ..., why} | null
export function parseGoalStatusLine(text) {
  const line = String(text || "").split(/\r?\n/).find((l) => l.startsWith("GOAL_STATUS|"));
  if (!line) return null;
  const parts = line.split("|");
  const kv = {};
  for (const p of parts.slice(3)) {
    const eq = p.indexOf("=");
    if (eq > 0) kv[p.slice(0, eq)] = p.slice(eq + 1);
  }
  const n = (k) => (kv[k] !== undefined && /^[0-9]+$/.test(kv[k]) ? Number(kv[k]) : null);
  return { status: parts[2], guarantees: n("guarantees"), verified: n("verified"), failed: n("failed"), contested: n("contested"), open: n("open"),
    assumptions: n("assumptions"), closed: n("closed"), client_checks: kv.client_checks && kv.client_checks !== "-" ? kv.client_checks : null, why: kv.why || null };
}

// `status --server`: for each library entry that already names a server Goal (library.md g=), read that Goal's COMPUTED status from the
// server. Read only; it never changes a local status, and any failure leaves the row without a `server` field. -> {read, failed}
export async function attachServerStatus(result, { env = process.env, fetchImpl, timeoutMs = 8000, log = () => {} } = {}) {
  const out = { read: 0, failed: 0 };
  let readHostedResource;
  try { ({ readHostedResource } = await import("./exec/hosted.mjs")); } catch { return out; }
  for (const row of result.targets) {
    if (!row.goal_id) continue;
    try {
      const text = await readHostedResource(`stealth://goals/${row.goal_id}/claims`, { env, fetchImpl, timeoutMs });
      const server = parseGoalStatusLine(text);
      if (server) { row.server = server; out.read++; } else out.failed++;
    } catch (err) {
      out.failed++;
      log(`status --server: ${row.id}: ${String(err?.message || err).slice(0, 120)}`);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- CLI

export function renderTable(result) {
  const rows = result.targets;
  if (!rows.length) return "no run.md nodes or library.md entries found";
  const w = Math.max(...rows.map((r) => r.id.length), 2);
  const lines = [`${"ID".padEnd(w)}  ${"KIND".padEnd(7)}  ${"STATUS".padEnd(9)}  WHY`];
  for (const r of rows) lines.push(`${r.id.padEnd(w)}  ${r.kind.padEnd(7)}  ${r.status.padEnd(9)}  ${r.why}${r.server ? `  [server: ${r.server.status}]` : ""}`);
  lines.push("", STATUSES.map((s) => `${s} ${result.counts[s]}`).join("  ·  "));
  return lines.join("\n");
}

export const STATUS_HELP = `kell status [--root <repo>] [--stealth-dir <dir>] [--json] [--server] [--run [--yes] [--in-place] [--only <ids>]] [--compact]

  A computed status for every .kell/run.md node and .kell/library.md entry:
    verified   a check exists, its latest run on the current code passed, and every deps= node is verified
               (flagged "code changed since the check ran" when the tree differs from the one the check ran on,
               though every file it cites still matches: a flag, never an expiry)
    failed     the latest run of its check on the current code failed
    contested  a pass and a fail on the same code
    expired    something it relies on changed after its evidence was recorded (a claim the survey would no longer
               keep, a touched file whose hash differs, evidence recorded against files that changed)
    open       everything else: no check, no evidence, or a dependency that is not verified
  A node marked done by hand with no recorded run is open: the hand-written status is a claim, not evidence.

  Read-only. --run lists the commands it will run first. In a terminal it asks once (y/N); with no terminal it runs only
  with --yes, otherwise it exits 3 ("pass --yes to run these checks"). Each check runs in a throwaway worktree (HEAD plus
  the uncommitted tracked diff); --in-place runs it in your checkout and says so. One line per check is appended to
  .kell/evidence.md. --only limits --run to those ids (comma list).
  --compact rewrites evidence.md keeping, per target and check, the latest pass, the latest fail and every line recorded on
  the tree as it is now (the old file is kept as evidence.md.prev). It refuses when evidence.md has uncommitted changes.
  --server also reads, for each library entry that names a server Goal, that Goal's status COMPUTED by the server from the evidence on
  its guarantee claims (shown as "server" in --json, and "[server: ...]" in the table). Read only, one request per Goal, and any failure
  leaves the local status as it is. It needs a saved token; without --server nothing is sent.
  --stealth-dir reads the plan, claims and evidence from another folder (for example .kell/collab) while --root stays
  the repository whose files are checked.
`;

async function askLine(question) {
  const readline = await import("node:readline");
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  return new Promise((resolve) => rl.question(question, (a) => { rl.close(); resolve(a); }));
}

export async function runStatusCli(argv, { out = (s) => process.stdout.write(s + "\n"), err = (s) => process.stderr.write(s + "\n"), exec,
  tty = Boolean(process.stdin.isTTY && process.stdout.isTTY), ask = askLine, env = process.env, fetchImpl } = {}) {
  const args = [...argv];
  if (args.includes("--help") || args.includes("-h") || args[0] === "help") { err(STATUS_HELP); return 0; }
  const take = (flag) => { const i = args.indexOf(flag); if (i < 0) return undefined; const v = args[i + 1]; args.splice(i, v === undefined || v.startsWith("--") ? 1 : 2); return v === undefined || v.startsWith("--") ? "" : v; };
  const has = (flag) => { const i = args.indexOf(flag); if (i < 0) return false; args.splice(i, 1); return true; };
  const rootArg = take("--root");
  const onlyArg = take("--only");
  const dirArg = take("--stealth-dir");
  const json = has("--json"), run = has("--run"), inPlace = has("--in-place"), yes = has("--yes"), compact = has("--compact"), server = has("--server");
  if (args.length) { err(`unknown argument ${args[0]}\n\n${STATUS_HELP}`); return 2; }
  const root = rootArg ? path.resolve(rootArg) : nearestStealthRoot(process.cwd());
  let ranInfo = null;
  try {
    const dir = dirArg ? path.resolve(dirArg) : undefined;
    const only = onlyArg ? onlyArg.split(",").map((s) => s.trim()).filter(Boolean) : null;
    if (compact) {
      const c = compactEvidence(root, dir ? { dir } : {});
      if (!c.ok) { err(`compact refused: ${c.reason}`); return 1; }
      err(c.changed ? `compacted evidence.md: ${c.before} lines -> ${c.after} (the old file is evidence.md.prev)` : "evidence.md is already compact");
    }
    if (run) {
      // These commands come from files in the repository. Show them, and run them only on a yes.
      const cmds = plannedCommands(root, { ...(dir ? { dir } : {}), only });
      let go = true;
      if (cmds.length) {
        err(`--run will run ${cmds.length} command(s) from the repository's own files, ${inPlace ? "in your checkout" : "each in a throwaway worktree"}:`);
        for (const c of cmds) err(`  $ ${c}`);
        if (!yes) {
          if (!tty) { err("pass --yes to run these checks"); return 3; }
          const answer = String((await ask("Run these? [y/N] ")) ?? "").trim();
          go = /^y(es)?$/i.test(answer);
          if (!go) err("nothing was run");
        }
      }
      if (go) ranInfo = await runChecks(root, { ...(dir ? { dir } : {}), only, inPlace, env, fetchImpl, ...(exec ? { exec } : {}) });
    }
    const result = computeStatus(root, dir ? { dir } : {});
    if (ranInfo) result.run = ranInfo;
    if (server) result.server = await attachServerStatus(result, { env, fetchImpl, log: err });
    const note = ranInfo?.in_place ? "checks ran in your checkout, not a throwaway copy\n\n" : "";
    out(json ? JSON.stringify(result, null, 2) : note + renderTable(result) + (ranInfo ? `\n\nran ${ranInfo.appended} check(s); ${ranInfo.skipped.length} not recorded` : ""));
    return 0;
  } catch (e) {
    err(`status failed (nothing was changed except any evidence lines already appended): ${e.message}`);
    return 1;
  }
}

export const _internals = { expandClaimSpec };
