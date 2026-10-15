// Project dependencies inside a run's throwaway worktree.
//
// A worktree is a fresh checkout, so it has no node_modules and no virtualenv: a JS project's check cannot even start, and a
// Python project's check runs on the system interpreter, not the project's. Reinstalling per run costs minutes and network.
// Instead the worktree gets a LINK to the project's own (a junction on Windows, a symlink elsewhere) and the run's PATH leads
// with the project's venv and node_modules/.bin, so checks and the agent's own test runs use what the project already has.
//
// Safety, because a link into the user's real dependency tree is the one place a cleanup could do harm:
//   * only a directory that git IGNORES in the project is linked (a tracked node_modules is the project's own business), and
//     only if the link is ignored inside the worktree too -- otherwise `git add -A` there would treat it as a change to the
//     answer. On Windows a junction passes; on POSIX a symlink is not matched by a `node_modules/` pattern, so it is
//     taken out again and that project simply runs without the link.
//   * removing a worktree deletes THROUGH a junction on Windows: git worktree remove --force emptied the real node_modules
//     (tested on Windows 11, git 2.4x). So every removal first takes the links out with the call that removes only the link
//     (unlinkProjectDeps, called by removeWorktree), never recursively.
//   * nothing ever writes through the link: a run that installs into node_modules would change the project's own, so the
//     link is for reading; a run that needs a different dependency set fails its check, as it would have before.
// STEALTHLAB_EXEC_LINK_DEPS=off turns the links and the PATH change off.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const LINK_NAMES = ["node_modules", ".venv", "venv"];

export const linkDepsEnabled = (env = process.env) => (env.STEALTHLAB_EXEC_LINK_DEPS || "on").toLowerCase() !== "off";

function ignored(cwd, name) {
  try {
    execFileSync("git", ["-C", cwd, "check-ignore", "-q", "--", name], { stdio: "ignore", windowsHide: true });
    return true;
  } catch {
    return false;
  }
}

// Remove a link, and only the link. rmdir takes a Windows junction out (and refuses a real, non-empty directory); unlink
// takes a POSIX symlink out. A real directory is never touched here.
function removeLink(p) {
  try {
    if (!fs.lstatSync(p).isSymbolicLink()) return false;
  } catch {
    return false;
  }
  try { fs.rmdirSync(p); return true; } catch { /* a POSIX symlink: not a directory to rmdir */ }
  try { fs.unlinkSync(p); return true; } catch { return false; }
}

// -> the names linked. Never throws: a run without links is just the old behaviour.
export function linkProjectDeps({ repo, dir, env = process.env }) {
  const linked = [];
  if (!linkDepsEnabled(env)) return linked;
  for (const name of LINK_NAMES) {
    try {
      const src = path.join(repo, name);
      const dst = path.join(dir, name);
      if (!fs.existsSync(src) || !fs.statSync(src).isDirectory() || fs.existsSync(dst)) continue;
      if (!ignored(repo, name)) continue;                          // the project tracks it, or it is not a dependency dir
      fs.symlinkSync(fs.realpathSync(src), dst, process.platform === "win32" ? "junction" : "dir");
      if (!ignored(dir, name)) { removeLink(dst); continue; }       // git would see the link as a change
      linked.push(name);
    } catch { /* this one is not linked */ }
  }
  return linked;
}

// Before a worktree is removed: take every link out first.
export function unlinkProjectDeps(dir) {
  for (const name of LINK_NAMES) removeLink(path.join(dir, name));
}

// The env for a run in `dir`: PATH leads with the project's venv and node_modules/.bin when they are there. Returns `env`
// itself when there is nothing to add.
export function depsEnv(dir, env = process.env) {
  if (!linkDepsEnabled(env)) return env;
  const add = [];
  let venv = null;
  for (const v of [".venv", "venv"]) {
    const bin = path.join(dir, v, process.platform === "win32" ? "Scripts" : "bin");
    if (fs.existsSync(bin)) { add.push(bin); venv = path.join(dir, v); break; }
  }
  const nb = path.join(dir, "node_modules", ".bin");
  if (fs.existsSync(nb)) add.push(nb);
  // a Python project in this worktree: its own code first on the import path -- the project is installed (editable)
  // from the user's checkout, so a src/ layout imported THAT copy, and an answer's change in the worktree was not what
  // its checks tested (pipeline survey, 2026-10-16: black, tox, streamlink, fromager are src/ layouts)
  const py = ["pyproject.toml", "setup.py", "setup.cfg"].some((f) => fs.existsSync(path.join(dir, f)));
  const pyPath = py ? [fs.existsSync(path.join(dir, "src")) ? path.join(dir, "src") : null, dir].filter(Boolean) : [];
  if (!add.length && !pyPath.length) return env;
  const key = Object.keys(env).find((k) => k.toUpperCase() === "PATH") || "PATH";
  const out = { ...env, [key]: [...add, env[key] || ""].filter(Boolean).join(path.delimiter) };
  if (pyPath.length) out.PYTHONPATH = [...pyPath, env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter);
  if (venv) {
    try { out.VIRTUAL_ENV = fs.realpathSync(venv); } catch { out.VIRTUAL_ENV = venv; }
  }
  return out;
}
