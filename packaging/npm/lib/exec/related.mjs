// Tests related to what an answer changed, run BEFORE the full check.
//
// A wrong answer usually breaks the tests next to the code it touched, and the full suite of a real project takes minutes.
// So the verifier first runs only the tests related to the changed files (fail fast: a failure there ends the attempt, with
// that failure as its tail), and only if they pass runs the project's full check, as it always did. The full check is never
// skipped: nothing is accepted on the related tests alone.
//
//   pytest   test_<name>.py / <name>_test.py found in the worktree for each changed module (a changed test file counts itself)
//   vitest   `npx vitest related <changed files> --run`
//   jest     `npx jest --findRelatedTests <changed files>`
// Only a check that names pytest / vitest / jest and does not already pin its targets gets a related run (a check such as
// `python -m pytest -q test_check.py` already names its file). Anything else, and a change with no related test found, is
// the full check alone, as before. STEALTHLAB_EXEC_RELATED=off turns it off.
import fs from "node:fs";
import path from "node:path";

export const relatedEnabled = (env = process.env) => (env.STEALTHLAB_EXEC_RELATED || "on").toLowerCase() !== "off";

const SKIP_DIRS = new Set(["node_modules", ".git", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build"]);
const isPyTest = (f) => /(^|\/)(test_[^/]*|[^/]*_test)\.py$/.test(f);
const quote = (p) => (/[\s"'&|<>^()]/.test(p) ? `"${p.replace(/"/g, '\\"')}"` : p);

// every test_x.py / x_test.py in the worktree, forward-slash relative, bounded
function pythonTestFiles(cwd, limit = 4000) {
  const out = [];
  let seen = 0;
  const walk = (rel, depth) => {
    if (depth > 5 || seen > limit) return;
    let entries = [];
    try { entries = fs.readdirSync(path.join(cwd, rel), { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      seen++;
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name) && !e.name.startsWith(".")) walk(r, depth + 1); } else if (isPyTest(r)) out.push(r);
    }
  };
  walk("", 0);
  return out;
}

// the check's text before the first `&&` (the project's own command; what follows may be the test-first runner)
const head = (check) => String(check).split("&&")[0].trim();

// -> [{ cmd, files }]: related-test commands to run first, possibly none
export function relatedChecks(checks, changed, cwd, env = process.env) {
  if (!relatedEnabled(env)) return [];
  const files = (changed || []).map((f) => String(f).replace(/\\/g, "/"));
  const out = [];
  for (const check of checks || []) {
    const h = head(check);
    if (/\bpytest\b/.test(h)) {
      const after = h.slice(h.search(/\bpytest\b/) + "pytest".length).trim().split(/\s+/).filter(Boolean);
      if (after.some((a) => !a.startsWith("-"))) continue;                      // the check already names its tests
      const py = files.filter((f) => f.endsWith(".py"));
      if (!py.length) continue;
      const tests = new Set(py.filter((f) => isPyTest(f) && fs.existsSync(path.join(cwd, f))));
      const all = pythonTestFiles(cwd);
      for (const f of py.filter((x) => !isPyTest(x))) {
        const base = path.posix.basename(f, ".py");
        for (const t of all) if (path.posix.basename(t) === `test_${base}.py` || path.posix.basename(t) === `${base}_test.py`) tests.add(t);
      }
      if (tests.size) out.push({ cmd: `${h} -x ${[...tests].sort().map(quote).join(" ")}`, files: [...tests].sort() });
    } else if (/\bvitest\b/.test(h) && !/\brelated\b/.test(h)) {
      const src = files.filter((f) => /\.(m?[jt]sx?)$/.test(f));
      if (src.length) out.push({ cmd: `npx vitest related ${src.map(quote).join(" ")} --run`, files: src });
    } else if (/\bjest\b/.test(h) && !/findRelatedTests/.test(h)) {
      const src = files.filter((f) => /\.(m?[jt]sx?)$/.test(f));
      if (src.length) out.push({ cmd: `npx jest --findRelatedTests ${src.map(quote).join(" ")}`, files: src });
    }
  }
  return out;
}
