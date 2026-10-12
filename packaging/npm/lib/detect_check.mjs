// Find the project's own test command, so dispatch has a CHECK without STEALTHLAB_DISPATCH_CHECK being set.
//
// Dispatch only applies a cheap answer that passed a check, so a repo with no configured check never got dispatch --
// the biggest reason a real user (not a benchmark runner) saw no saving. This is a pure function of the files in the
// project root: no command is run to find the answer and nothing is written.
//
//   detectCheck(root) -> "<test command>" | null     (null = no check found: no dispatch, as before)
//
// Deliberately conservative: only the project ROOT is looked at (a monorepo's sub-package tests are not guessed), and a
// signal that is weak (a loose test_*.py with no pytest config and no tests dir) does not count. A wrong check would let
// wrong answers in, which is worse than no dispatch. STEALTHLAB_DISPATCH_DETECT=off turns detection off.
import fs from "node:fs";
import path from "node:path";

const exists = (root, ...p) => { try { return fs.existsSync(path.join(root, ...p)); } catch { return false; } };
const read = (root, f) => { try { return fs.readFileSync(path.join(root, f), "utf8"); } catch { return ""; } };

// npm's own default script, written by `npm init`: `echo "Error: no test specified" && exit 1`
function realTestScript(script) {
  const s = String(script || "").trim();
  return !!s && !/no test specified/i.test(s);
}

function nodeCheck(root) {
  let pkg = null;
  try { pkg = JSON.parse(read(root, "package.json")); } catch { return null; }
  if (!pkg || typeof pkg !== "object") return null;
  const script = pkg.scripts?.test;
  if (realTestScript(script)) {
    // `vitest` alone starts watch mode on a terminal; the one-shot form never hangs a check
    if (/^\s*vitest\s*$/.test(script)) return "npx vitest run";
    return "npm test";
  }
  const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
  if (deps.vitest) return "npx vitest run";
  if (deps.jest) return "npx jest";
  return null;
}

function hasPytestConfig(root) {
  if (exists(root, "pytest.ini") || exists(root, "conftest.py")) return true;
  if (/\[tool\.pytest/.test(read(root, "pyproject.toml"))) return true;
  if (/^\s*\[pytest\]/m.test(read(root, "tox.ini"))) return true;
  if (/^\s*\[tool:pytest\]/m.test(read(root, "setup.cfg"))) return true;
  return false;
}

function hasPythonTests(root) {
  const isTest = (f) => /^test_.*\.py$|.*_test\.py$/.test(f);
  try {
    for (const dir of ["tests", "test"]) {
      const p = path.join(root, dir);
      if (fs.statSync(p, { throwIfNoEntry: false })?.isDirectory() && fs.readdirSync(p).some(isTest)) return true;
    }
  } catch { /* unreadable: no tests dir */ }
  return false;
}

export function detectCheck(root) {
  if (!root) return null;
  if (exists(root, "dbt_project.yml")) return "dbt build";
  if (exists(root, "go.mod")) return "go test ./...";
  if (exists(root, "Cargo.toml")) return "cargo test";
  if (exists(root, "package.json")) {
    const c = nodeCheck(root);
    if (c) return c;
  }
  // python last: a config file or a tests directory with real test files, not one stray file
  if (hasPytestConfig(root) || hasPythonTests(root)) return "python -m pytest -q";
  return null;
}
