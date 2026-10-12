import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { detectCheck } from "../lib/detect_check.mjs";
import { dispatchCheck, dispatchPolicy } from "../lib/dispatch.mjs";

// a fixture repo: { "relative/path": "text" }
function repo(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "detect-"));
  for (const [rel, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), text);
  }
  return root;
}
const pkg = (o) => JSON.stringify(o);

test("pytest: a config file, or a tests directory with real test files", () => {
  assert.equal(detectCheck(repo({ "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n" })), "python -m pytest -q");
  assert.equal(detectCheck(repo({ "pytest.ini": "[pytest]\n" })), "python -m pytest -q");
  assert.equal(detectCheck(repo({ "tox.ini": "[pytest]\nminversion = 6\n" })), "python -m pytest -q");
  assert.equal(detectCheck(repo({ "setup.cfg": "[tool:pytest]\nxfail_strict = true\n" })), "python -m pytest -q");
  assert.equal(detectCheck(repo({ "tests/test_calc.py": "def test_a(): pass\n" })), "python -m pytest -q");
  assert.equal(detectCheck(repo({ "test/calc_test.py": "def test_a(): pass\n" })), "python -m pytest -q");
});

test("pytest: a pyproject without pytest config, an empty tests dir and a stray test file are NOT a check", () => {
  assert.equal(detectCheck(repo({ "pyproject.toml": "[project]\nname='x'\n" })), null);
  assert.equal(detectCheck(repo({ "tests/README.md": "x" })), null);
  assert.equal(detectCheck(repo({ "test_stray.py": "def test_a(): pass\n" })), null);
});

test("npm test: a real script counts; npm's default placeholder does not", () => {
  assert.equal(detectCheck(repo({ "package.json": pkg({ scripts: { test: "node --test" } }) })), "npm test");
  assert.equal(detectCheck(repo({ "package.json": pkg({ scripts: { test: 'echo "Error: no test specified" && exit 1' } }) })), null);
  assert.equal(detectCheck(repo({ "package.json": pkg({ scripts: { build: "tsc" } }) })), null);
  assert.equal(detectCheck(repo({ "package.json": "{not json" })), null);
});

test("vitest and jest: by script or by dependency; vitest always one-shot", () => {
  assert.equal(detectCheck(repo({ "package.json": pkg({ scripts: { test: "vitest" } }) })), "npx vitest run");
  assert.equal(detectCheck(repo({ "package.json": pkg({ devDependencies: { vitest: "^2" } }) })), "npx vitest run");
  assert.equal(detectCheck(repo({ "package.json": pkg({ devDependencies: { jest: "^29" } }) })), "npx jest");
  assert.equal(detectCheck(repo({ "package.json": pkg({ scripts: { test: "jest --ci" } }) })), "npm test");
});

test("dbt, go and cargo", () => {
  assert.equal(detectCheck(repo({ "dbt_project.yml": "name: shop\n" })), "dbt build");
  assert.equal(detectCheck(repo({ "go.mod": "module x\n" })), "go test ./...");
  assert.equal(detectCheck(repo({ "Cargo.toml": "[package]\nname='x'\n" })), "cargo test");
});

test("a repo with no check, an empty one and a missing root give null", () => {
  assert.equal(detectCheck(repo({ "README.md": "hello" })), null);
  assert.equal(detectCheck(repo({})), null);
  assert.equal(detectCheck(null), null);
  assert.equal(detectCheck(path.join(os.tmpdir(), "does-not-exist-xyz")), null);
});

test("only the root counts: a sub-package's tests are not guessed", () => {
  assert.equal(detectCheck(repo({ "pkg/package.json": pkg({ scripts: { test: "node --test" } }) })), null);
});

test("dispatchCheck: configured wins, then the library entry, then detection; the switch turns detection off", () => {
  const root = repo({ "go.mod": "module x\n" });
  assert.equal(dispatchCheck({ check: "make test", detect: true }, {}, root), "make test");
  assert.equal(dispatchCheck({ check: null, detect: true }, { library_matches: [] }, root), "go test ./...");
  assert.equal(dispatchCheck({ check: null, detect: false }, { library_matches: [] }, root), null);
  assert.equal(dispatchPolicy({}).detect, true);
  assert.equal(dispatchPolicy({ STEALTHLAB_DISPATCH_DETECT: "off" }).detect, false);
});
