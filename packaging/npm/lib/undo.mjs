// Undo for a dispatched answer: a cheap model's verified change is copied into the user's checkout, so the way back must
// exist BEFORE the copy.
//
// The undo point is a stash-like git ref, written with plumbing only: `refs/stealthlab/undo/<id>` points at a commit
// whose tree holds each file the answer will touch AS IT IS NOW (a file that does not exist yet is recorded as absent).
// It is not on any branch, never in `git log` of the user's branch, never in their index or working tree, and the ref
// keeps its objects from `git gc`. A small manifest under the StealthLab home (undo/<id>.json) says which files, the
// model, the task and, once the answer is applied, the hash each file has then.
//
//   stealthlab-mcp undo            restore the latest undo point of this repository
//   stealthlab-mcp undo --list     the undo points of this repository
//   stealthlab-mcp undo <id>       a particular one
//   stealthlab-mcp undo --force    restore even when a file was edited again since the answer was applied
//
// Without --force an undo refuses when a touched file no longer matches what the answer wrote: your own later edit is
// never thrown away silently. The 20 newest points per repository are kept. STEALTHLAB_DISPATCH_UNDO=off skips making them.
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { configDir } from "./config.mjs";

export const KEEP = 20;
export const REF_PREFIX = "refs/stealthlab/undo/";

export const undoEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_UNDO || "on").toLowerCase() !== "off";

const dir = (env) => path.join(configDir(env), "undo");

function git(repo, args, { input, indexFile, buffer = false } = {}) {
  const out = execFileSync("git", ["-C", repo, "-c", "core.quotepath=false", ...args], {
    input, encoding: buffer ? "buffer" : "utf8", windowsHide: true, maxBuffer: 256 * 1024 * 1024, stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, ...(indexFile ? { GIT_INDEX_FILE: indexFile } : {}) },
  });
  return buffer ? out : out.trim();
}

const hashOf = (repo, rel) => {
  const abs = path.join(repo, rel);
  if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) return null;
  return git(repo, ["hash-object", "--no-filters", "--", rel]);
};

// -> {id, ref, files}. Throws when it cannot be made (the caller decides what that means for the apply).
export function createUndoPoint({ repo, files, model = null, task = "", env = process.env, now = new Date() }) {
  const id = `u-${now.toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}-${crypto.randomBytes(3).toString("hex")}`;
  const index = path.join(os.tmpdir(), `stealth-undo-${id}.index`);
  try {
    const entries = [];
    for (const rel of files) {
      const abs = path.join(repo, rel);
      if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
        const sha = git(repo, ["hash-object", "-w", "--no-filters", "--", rel]);
        const exec = process.platform !== "win32" && (fs.statSync(abs).mode & 0o111) !== 0;
        git(repo, ["update-index", "--add", "--cacheinfo", `${exec ? "100755" : "100644"},${sha},${rel}`], { indexFile: index });
        entries.push({ path: rel, existed: true });
      } else {
        entries.push({ path: rel, existed: false });
      }
    }
    const tree = git(repo, ["write-tree"], { indexFile: index });
    const commit = git(repo, ["-c", "user.name=stealthlab", "-c", "user.email=undo@stealthlab.invalid", "commit-tree", tree, "-m", `stealthlab undo point ${id}`]);
    const ref = `${REF_PREFIX}${id}`;
    git(repo, ["update-ref", ref, commit]);
    fs.mkdirSync(dir(env), { recursive: true, mode: 0o700 });
    const manifest = { id, repo: path.resolve(repo), ref, commit, at: now.toISOString(), model, task: String(task).slice(0, 200),
                       files: entries, after: null, undone: false };
    fs.writeFileSync(path.join(dir(env), `${id}.json`), JSON.stringify(manifest, null, 2), { mode: 0o600 });
    prune(repo, env);
    return { id, ref, files: entries.map((e) => e.path) };
  } finally {
    try { fs.rmSync(index, { force: true }); } catch { /* temp file */ }
  }
}

function read(env, id) {
  try { return JSON.parse(fs.readFileSync(path.join(dir(env), `${id}.json`), "utf8")); } catch { return null; }
}
const write = (env, m) => fs.writeFileSync(path.join(dir(env), `${m.id}.json`), JSON.stringify(m, null, 2), { mode: 0o600 });

// The answer was applied: remember what each touched file is now, so an undo can tell a later edit from the answer.
export function finishUndoPoint(point, { repo, env = process.env }) {
  const m = read(env, point.id);
  if (!m) return;
  m.after = Object.fromEntries(m.files.map((f) => [f.path, hashOf(repo, f.path)]));
  write(env, m);
}

// The answer was not applied after all: the point is not needed.
export function discardUndoPoint(point, { repo, env = process.env }) {
  try { git(repo, ["update-ref", "-d", point.ref]); } catch { /* already gone */ }
  try { fs.rmSync(path.join(dir(env), `${point.id}.json`), { force: true }); } catch { /* already gone */ }
}

export function listUndoPoints({ repo, env = process.env }) {
  let names = [];
  try { names = fs.readdirSync(dir(env)).filter((n) => n.endsWith(".json")); } catch { return []; }
  const want = path.resolve(repo);
  return names.map((n) => read(env, n.slice(0, -5))).filter((m) => m && path.resolve(m.repo) === want)
    .sort((a, b) => (a.at < b.at ? 1 : -1));
}

function prune(repo, env) {
  for (const m of listUndoPoints({ repo, env }).slice(KEEP)) discardUndoPoint(m, { repo, env });
}

// -> {id, restored: [paths], removed: [paths]}. Throws a message naming the files when one was edited since the answer.
export function undo({ repo, id = null, force = false, env = process.env }) {
  const points = listUndoPoints({ repo, env });
  const m = id ? points.find((p) => p.id === id) : points.find((p) => p.after && !p.undone);
  if (!m) throw new Error(id ? `no undo point ${id} for ${repo}` : `nothing to undo in ${repo} (stealthlab-mcp undo --list shows what is kept)`);
  if (m.undone) throw new Error(`${m.id} was already undone`);
  if (!m.after) throw new Error(`${m.id} was never applied`);
  if (!force) {
    const edited = m.files.filter((f) => hashOf(repo, f.path) !== m.after[f.path]).map((f) => f.path);
    if (edited.length) throw new Error(`not undone: ${edited.join(", ")} changed since the answer was applied; undo would throw those edits away (--force does it anyway)`);
  }
  const restored = [];
  const removed = [];
  for (const f of m.files) {
    const abs = path.join(repo, f.path);
    if (f.existed) {
      const bytes = git(repo, ["cat-file", "blob", `${m.commit}:${f.path}`], { buffer: true });
      fs.mkdirSync(path.dirname(abs), { recursive: true });
      fs.writeFileSync(abs, bytes);
      restored.push(f.path);
    } else if (fs.existsSync(abs)) {
      fs.rmSync(abs, { force: true });
      removed.push(f.path);
    }
  }
  m.undone = true;
  write(env, m);
  return { id: m.id, restored, removed };
}

// ---- the command ---------------------------------------------------------------------------------------------------------

export function runUndoCli(argv, { cwd = process.cwd(), env = process.env, print = (s) => process.stdout.write(`${s}\n`) } = {}) {
  const force = argv.includes("--force");
  const list = argv.includes("--list");
  const id = argv.find((a) => !a.startsWith("-")) || null;
  let repo;
  try { repo = git(cwd, ["rev-parse", "--show-toplevel"]); } catch { print("not inside a git repository"); return 1; }
  if (list) {
    const points = listUndoPoints({ repo, env });
    if (!points.length) print("no undo points");
    for (const p of points) print(`${p.id}  ${p.at}  ${p.model || "?"}  ${p.undone ? "(undone) " : p.after ? "" : "(not applied) "}${p.files.map((f) => f.path).join(", ")}  ${p.task}`);
    return 0;
  }
  try {
    const r = undo({ repo, id, force, env });
    print(`undone ${r.id}: restored ${r.restored.join(", ") || "nothing"}${r.removed.length ? `; removed ${r.removed.join(", ")}` : ""}`);
    return 0;
  } catch (err) {
    print(err.message);
    return 1;
  }
}
