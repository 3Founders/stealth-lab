// The cheap agent's shell must not install packages while the worktree's dependency directories are LINKS to the project's own.
//
// lib/exec/deps.mjs gives a run's worktree a link to the project's node_modules and virtualenv so its checks can run. A link is for
// reading: `npm install` or `pip install` through it would change the project's REAL dependency tree, outside the throwaway
// worktree and outside the answer's diff. So when any of those directories is a link, an install command in the agent's shell is
// refused with an error that says why and what to do instead. Without links (a project that has none, or
// STEALTHLAB_EXEC_LINK_DEPS=off) installs stay as they were: they land in the throwaway worktree.
//
// What counts as an install: npm / pnpm / yarn / bun install, i, add, ci (and a bare `yarn`, which installs); pip / pip3 /
// `python -m pip` / `py -m pip` / `uv pip` install; `uv add`, `uv sync`, `poetry add|install`, `pipenv install`. Each command of a
// pipeline or `&&` / `;` chain is looked at, after any leading `sudo`, `env` or VAR=value words. A command that merely mentions
// one of these words (an echo, a grep, a comment) is not an install.
// STEALTHLAB_EXEC_BLOCK_INSTALLS=off turns the refusal off (the stealth executor passes it to the agent as `block_installs: false`).
import fs from "node:fs";
import path from "node:path";

export const LINKED_NAMES = ["node_modules", ".venv", "venv"];

// flags between the tool and its subcommand; --prefix and friends take a value
const FLAGS = String.raw`(?:\s+(?:--prefix|--cwd|--dir|--workspace|-w|-C)\s+\S+|\s+-{1,2}[\w@./:=-]+)*`;
const NODE_INSTALL = new RegExp(String.raw`^(?:npm|pnpm|yarn|bun)${FLAGS}\s+(?:install|i|add|ci)\b`, "i");
const PY_INSTALL = new RegExp(String.raw`^(?:pip3?|(?:python3?(?:\.\d+)?|py)(?:\s+-\d)?\s+-m\s+pip|uv\s+pip)${FLAGS}\s+install\b`, "i");
const OTHER_INSTALL = /^(?:uv\s+(?:add|sync)|poetry\s+(?:add|install)|pipenv\s+install)\b/i;

// the commands of a shell line, each without leading sudo / env / VAR=value words
export function shellCommands(line) {
  return String(line || "").split(/&&|\|\||;|\||\r?\n|\(|\)|`|\$\(/).map((seg) => {
    let s = seg.trim();
    for (;;) {
      const next = s.replace(/^(?:sudo|env|time|command|exec)\s+/i, "").replace(/^[A-Za-z_][A-Za-z0-9_]*=\S*\s+/, "");
      if (next === s) return s;
      s = next;
    }
  }).filter(Boolean);
}

export function isInstallCommand(line) {
  return shellCommands(line).some((c) => NODE_INSTALL.test(c) || /^yarn(?:\s+-{1,2}[\w@./:=-]+)*$/i.test(c) || PY_INSTALL.test(c) || OTHER_INSTALL.test(c));
}

// the dependency directories of `cwd` that are links
export function linkedDeps(cwd) {
  const out = [];
  for (const name of LINKED_NAMES) {
    try { if (fs.lstatSync(path.join(cwd, name)).isSymbolicLink()) out.push(name); } catch { /* not there */ }
  }
  return out;
}

// -> an error message, or null when the command may run
export function installRefusal(cwd, command, { enabled = true } = {}) {
  if (!enabled || !isInstallCommand(command)) return null;
  const linked = linkedDeps(cwd);
  if (!linked.length) return null;
  return `package installs are refused here: ${linked.join(" and ")} in this worktree ${linked.length > 1 ? "are links" : "is a link"} to the project's own, so ` +
    "an install would change the real dependencies, not just this attempt. Use what is already installed; if something is missing, " +
    "finish and say which package the task needs.";
}
