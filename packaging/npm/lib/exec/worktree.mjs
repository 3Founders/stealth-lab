// Git worktree manager. The user's checkout is read (rev-parse, diff, hash-object) but never written,
// except by applyToCheckout(), which only apply_run calls.
//
// Pattern (from experiments/swebench/generate.py checkout()/release(), reimplemented):
//   git -C <repo> worktree add --detach <tmp>/stealth-runs/<run_id>-a<N> <base-commit>
//   ... run ...
//   git -C <repo> worktree remove --force <path>; rm -rf <path>; git -C <repo> worktree prune
// No branch is ever created (always --detach), so the user's branch namespace is untouched.
//
// Writes into the user's repository that this module does make, all by design and all invisible to
// `git status` in the main checkout: the worktree's admin dir (.git/worktrees/<name>, removed on cleanup
// and by `worktree prune`), and loose objects from `git add -A` / `git write-tree` INSIDE the worktree
// (its index is its own; the objects are unreachable and go away with the user's normal `git gc`).
import { execFile } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { childEnv } from "./proc.mjs";

export function git(args, { cwd, input, env = process.env, maxBuffer = 64 * 1024 * 1024, timeoutMs = 120000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile("git", ["-c", "core.quotepath=false", ...args], {
      cwd, env: childEnv(env), windowsHide: true, maxBuffer, timeout: timeoutMs, encoding: "buffer",
    }, (err, stdout, stderr) => {
      if (err) {
        const msg = (stderr && stderr.toString("utf8").trim()) || err.message;
        const e = new Error(`git ${args.filter((a) => !String(a).includes("\n")).slice(0, 4).join(" ")}: ${msg}`);
        e.code = err.code;
        e.stderr = stderr ? stderr.toString("utf8") : "";
        return reject(e);
      }
      resolve(stdout);
    });
    if (input !== undefined) child.stdin.end(input);
  });
}
const gitText = async (args, opts) => (await git(args, opts)).toString("utf8");

export function defaultWorktreeRoot(env = process.env) {
  return env.STEALTHLAB_EXEC_WORKTREE_ROOT || path.join(os.tmpdir(), "stealth-runs");
}

export async function repoTopLevel(repoPath, env) {
  if (!repoPath || !fs.existsSync(repoPath)) throw new Error(`repo_path does not exist: ${repoPath}`);
  let top;
  try {
    top = (await gitText(["-C", repoPath, "rev-parse", "--show-toplevel"], { env })).trim();
  } catch {
    throw new Error(`repo_path is not inside a git work tree: ${repoPath}`);
  }
  return path.resolve(top);
}

// Refuse on unresolved merge / rebase / cherry-pick / revert state or unmerged index entries.
export async function assertNoMergeState(repo, env) {
  const markers = ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"];
  for (const m of markers) {
    const p = (await gitText(["-C", repo, "rev-parse", "--git-path", m], { env })).trim();
    if (fs.existsSync(path.resolve(repo, p))) {
      throw new Error(`refusing: ${repo} has an unresolved ${m.replace(/_HEAD$/, "").toLowerCase()} in progress (${m})`);
    }
  }
  const unmerged = (await gitText(["-C", repo, "ls-files", "-u"], { env })).trim();
  if (unmerged) throw new Error(`refusing: ${repo} has unmerged paths`);
}

export async function headCommit(repo, env) {
  try {
    return (await gitText(["-C", repo, "rev-parse", "--verify", "HEAD^{commit}"], { env })).trim();
  } catch {
    throw new Error(`refusing: ${repo} has no commits yet (HEAD is unborn)`);
  }
}

// The user's uncommitted diff against HEAD, binary-safe. Untracked files are NOT included
// (`git diff HEAD` does not show them) -- stated, not hidden.
export async function workingTreeDiff(repo, env) {
  return git(["-C", repo, "diff", "--binary", "--no-color", "--no-ext-diff", "HEAD"], { env });
}

export async function createWorktree({ repo, commit, dir, patch, env }) {
  fs.mkdirSync(path.dirname(dir), { recursive: true, mode: 0o700 });
  if (fs.existsSync(dir)) throw new Error(`worktree path already exists: ${dir}`);
  await git(["-C", repo, "worktree", "add", "--detach", "--", dir, commit], { env });
  if (patch && patch.length) {
    await git(["-C", dir, "apply", "--binary", "--whitespace=nowarn", "-"], { env, input: patch });
  }
  return { dir, baseTree: await snapshotTree(dir, env) };
}

// Stage everything in the WORKTREE's own index and return its tree id: the base for scope/diff when the
// run starts, the "after" when it ends. Works whether or not the executor committed.
//
// Generated artifacts are excluded (ARTIFACT_EXCLUDES): an executor that runs the tests itself -- as the executor
// contract asks -- leaves bytecode and tool caches behind. Counting those as "changed files" failed the scope
// check of a correct fix in the acceptance run (OpenCode fixed calc.py; pytest left __pycache__/*.pyc). They are
// never the product, so they are kept out of the diff, the scope check, the patch and apply_run alike.
export const ARTIFACT_EXCLUDES = [
  "**/__pycache__/**", "**/*.pyc", "**/*.pyo", "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**",
  "**/.tox/**", "**/.coverage", "**/node_modules/.cache/**",
];

export async function snapshotTree(dir, env) {
  await git(["-C", dir, "add", "-A", "--", ".", ...ARTIFACT_EXCLUDES.map((g) => `:(glob,exclude)${g}`)], { env });
  return (await gitText(["-C", dir, "write-tree"], { env })).trim();
}

export async function collectDiff({ dir, baseTree, env }) {
  const afterTree = await snapshotTree(dir, env);
  const names = (await gitText(["-C", dir, "diff", "--no-renames", "--name-only", "-z", baseTree, afterTree], { env }))
    .split("\0").filter(Boolean);
  const stat = (await gitText(["-C", dir, "diff", "--no-renames", "--stat", "--no-color", baseTree, afterTree], { env })).trimEnd();
  const patch = await git(["-C", dir, "diff", "--no-renames", "--binary", "--no-color", "--no-ext-diff", baseTree, afterTree], { env });
  return { files: names, stat, patch, afterTree };
}

export async function removeWorktree({ repo, dir, env }) {
  try { await git(["-C", repo, "worktree", "remove", "--force", "--", dir], { env }); } catch { /* fall through */ }
  try { fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch { /* best effort */ }
  try { await git(["-C", repo, "worktree", "prune"], { env }); } catch { /* best effort */ }
}

async function blobAt(dir, tree, file, env) {
  try {
    return (await gitText(["-C", dir, "rev-parse", "--verify", "--quiet", `${tree}:${file}`], { env })).trim() || null;
  } catch {
    return null;
  }
}

async function checkoutBlob(repo, file, env) {
  const abs = path.join(repo, file);
  if (!fs.existsSync(abs)) return null;
  const st = fs.lstatSync(abs);
  if (st.isDirectory()) return "<directory>";
  return (await gitText(["-C", repo, "hash-object", "--", file], { env })).trim();
}

function insideRoot(root, p) {
  const rel = path.relative(root, p);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

// apply_run's guard + copy. For every file in the run's diff: the user's current blob must equal the
// blob the run started from (baseTree). Then the worktree's version of each file is copied over (or the
// file removed). Copying is equivalent to applying the patch exactly because the guard proved the target
// equals the base, and it is robust to autocrlf and binary content. Symlinks and paths escaping the repo
// are refused.
export async function applyToCheckout({ repo, dir, baseTree, files, env }) {
  const realRepo = fs.realpathSync(repo);
  const changed = [];
  for (const f of files) {
    const abs = path.resolve(repo, f);
    if (!insideRoot(path.resolve(repo), abs)) throw new Error(`refusing: path escapes the repo: ${f}`);
    let parent = path.dirname(abs);
    while (!fs.existsSync(parent)) parent = path.dirname(parent);
    if (!insideRoot(realRepo, fs.realpathSync(parent))) throw new Error(`refusing: ${f} resolves outside the repo`);
    const src = path.join(dir, f);
    if (fs.existsSync(src) && fs.lstatSync(src).isSymbolicLink()) throw new Error(`refusing: ${f} is a symlink in the run`);
    const base = await blobAt(dir, baseTree, f, env);
    const now = await checkoutBlob(repo, f, env);
    if (base !== now) changed.push(f);
  }
  if (changed.length) {
    const e = new Error(`refusing: target file(s) changed since the run started: ${changed.join(", ")}`);
    e.changed = changed;
    throw e;
  }
  const applied = [];
  for (const f of files) {
    const src = path.join(dir, f);
    const dst = path.join(repo, f);
    if (fs.existsSync(src)) {
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(src, dst);
      try { fs.chmodSync(dst, fs.statSync(src).mode & 0o777); } catch { /* Windows */ }
      applied.push({ path: f, action: "write" });
    } else {
      fs.rmSync(dst, { force: true });
      applied.push({ path: f, action: "delete" });
    }
  }
  return applied;
}
