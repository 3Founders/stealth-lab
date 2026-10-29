// The project's own virtualenv, activated for everything a prompt hook starts (the check, the baseline, the cheap agents'
// shell commands): they all run `python` from PATH, inside a git worktree that does not carry the gitignored `.venv`.
//
// Why: without it they ran the machine's default Python. On kell-try (jsonargparse) that was 3.14, which the repo's
// version does not support: 417 tests failed on the unchanged code in every dispatch (2026-10-29), so "passes the
// check" meant "no new failures among tests that mostly cannot pass". A project with a venv is tested the way its
// developer tests it. STEALTHLAB_VENV=off skips it; a VIRTUAL_ENV already set (a shell that activated one) wins.
import fs from "node:fs";
import path from "node:path";

export const VENV_DIRS = [".venv", "venv", "env"];

// -> { dir, bin } of the first virtualenv at the repository root, or null
export function findVenv(root, { platform = process.platform, exists = fs.existsSync } = {}) {
  if (!root) return null;
  for (const name of VENV_DIRS) {
    const dir = path.join(root, name);
    const bin = path.join(dir, platform === "win32" ? "Scripts" : "bin");
    const py = path.join(bin, platform === "win32" ? "python.exe" : "python");
    try { if (exists(path.join(dir, "pyvenv.cfg")) && exists(py)) return { dir, bin }; } catch { /* unreadable: no venv */ }
  }
  return null;
}

// Activates the root's venv in `env` (PATH first, VIRTUAL_ENV) -> the venv dir, or null when none was activated.
export function activateVenv(root, env = process.env, { platform = process.platform, exists = fs.existsSync } = {}) {
  if (String(env.STEALTHLAB_VENV || "on").toLowerCase() === "off" || env.VIRTUAL_ENV) return null;
  const v = findVenv(root, { platform, exists });
  if (!v) return null;
  // Windows keeps the variable as Path; writing PATH beside it would leave two
  const key = Object.keys(env).find((k) => k.toUpperCase() === "PATH") || "PATH";
  env[key] = [v.bin, env[key]].filter(Boolean).join(path.delimiter);
  env.VIRTUAL_ENV = v.dir;
  delete env.PYTHONHOME;
  return v.dir;
}
