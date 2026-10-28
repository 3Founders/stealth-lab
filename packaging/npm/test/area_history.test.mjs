import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { areaHistory, enclosingNames, historyFor } from "../lib/area_history.mjs";
import { moduleFiles } from "../lib/locate.mjs";

const git = (cwd, ...args) => execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });

// a repo whose history has: a fix in parse(), a feature in render(), a change to another file
function repo() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), "area-"));
  git(d, "init", "-q");
  git(d, "config", "user.email", "t@t");
  git(d, "config", "user.name", "t");
  fs.mkdirSync(path.join(d, "pkg"));
  const code = (parseBody, renderBody) => `import os\n\n\nclass Tool:\n    def parse(self, value):\n${parseBody}\n\n    def render(self, value):\n${renderBody}\n`;
  const write = (p, t) => fs.writeFileSync(path.join(d, p), t);
  write("pkg/_core.py", code("        return value", "        return str(value)"));
  write("pkg/other.py", "def helper():\n    return 1\n");
  git(d, "add", "-A"); git(d, "commit", "-qm", "start");
  write("pkg/_core.py", code("        kwargs = dict(self.kwargs)\n        return value", "        return str(value)"));
  git(d, "commit", "-qam", "Fix parse mutating the shared kwargs (#12)");
  write("pkg/_core.py", code("        kwargs = dict(self.kwargs)\n        return value", "        return repr(value)"));
  git(d, "commit", "-qam", "Add repr rendering");
  write("pkg/other.py", "def helper():\n    return 2\n");
  git(d, "commit", "-qam", "Fix helper");
  return d;
}

test("enclosingNames: the function a line is in and the class around it", () => {
  const text = "class A:\n    def f(self):\n        x = 1\n        return x\n\ndef g():\n    pass\n";
  assert.deepEqual(enclosingNames(text, 3, 3), ["f", "A"]);
  assert.deepEqual(enclosingNames(text, 7, 7), ["g"]);
});

test("historyFor: past changes to one function, bug fixes first; another function's changes are left out", () => {
  const d = repo();
  const out = historyFor(d, "pkg/_core.py", "parse");
  assert.match(out, /Fix parse mutating the shared kwargs \(#12\)/);
  assert.match(out, /kwargs = dict\(self\.kwargs\)/);
  assert.doesNotMatch(out, /Add repr rendering/);
  assert.match(historyFor(d, "pkg/_core.py", "render"), /Add repr rendering/);
  assert.equal(historyFor(d, "pkg/_core.py", "nothing_here"), "");
  assert.equal(historyFor(d, "pkg/missing.py"), "");
});

test("areaHistory: keyed by the located lines' functions, within the budget", () => {
  const d = repo();
  const out = areaHistory(d, [{ file: "pkg/_core.py", start: 6, end: 7 }]);
  assert.match(out, /in parse\)/);
  assert.doesNotMatch(out, /Fix helper/);
  assert.equal(areaHistory(d, []), "");
  assert.ok(areaHistory(d, [{ file: "pkg/_core.py", start: 6, end: 7 }], { budget: 100 }).length <= 100);
});

test("moduleFiles: a dotted module path in a task names its file", () => {
  const d = repo();
  assert.deepEqual(moduleFiles(d, "calling pkg._core.Tool.parse fails"), ["pkg/_core.py"]);
  assert.deepEqual(moduleFiles(d, "see example.com and setup.py"), []);
});
