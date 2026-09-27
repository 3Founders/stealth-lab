import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { childEnv, planSpawn, resolveCmdShim } from "../lib/exec/proc.mjs";
import { refusalReason } from "../lib/exec/select.mjs";
import { parseNodeLines } from "../lib/exec/task.mjs";
import { globToRegExp, runChecks, scopeViolations } from "../lib/exec/verify.mjs";
import { tmpDir } from "./fixtures/exec/helpers.mjs";

const IS_WIN = process.platform === "win32";

test("scope globs: anchored, * stays in one dir, ** crosses dirs, trailing / means subtree", () => {
  assert.ok(globToRegExp("calc.py").test("calc.py"));
  assert.ok(!globToRegExp("calc.py").test("sub/calc.py"));
  assert.ok(globToRegExp("src/*.py").test("src/a.py"));
  assert.ok(!globToRegExp("src/*.py").test("src/x/a.py"));
  assert.ok(globToRegExp("src/**/*.py").test("src/a.py"));
  assert.ok(globToRegExp("src/**/*.py").test("src/x/y/a.py"));
  assert.ok(globToRegExp("docs/").test("docs/a/b.md"));
  assert.ok(globToRegExp("./a?.txt").test("ab.txt"));
  assert.ok(!globToRegExp("a.txt").test("aXtxt"));
  assert.deepEqual(scopeViolations(["calc.py", "src/other.py", "src\\x.py"], ["calc.py", "src/x.py"]), ["src/other.py"]);
});

test("NODE lines (plan_and_run format) parse, including a '|' inside check=", () => {
  const nodes = parseNodeLines(
    "Fix it.\n" +
    "NODE|N-3|ready|Configure page size|step=P-1:3|claims=R-001..R-004|deps=N-2|check=node build.js && test -s out.docx\n" +
    "- NODE|N-4|ready|pipe a|b|step=P-1:4|deps=-|check=cat x | grep y\n");
  assert.equal(nodes.length, 2);
  assert.deepEqual(nodes[0].step, { procedure: "P-1", order: 3 });
  assert.equal(nodes[0].check, "node build.js && test -s out.docx");
  assert.equal(nodes[0].claims, "R-001..R-004");
  assert.equal(nodes[1].what, "pipe a|b");
  assert.equal(nodes[1].check, "cat x | grep y");
});

test("child env strips every STEALTHLAB_* and GIT_DIR; an adapter cannot re-inject the token", () => {
  const env = childEnv({ PATH: "/bin", HOME: "/h", STEALTHLAB_TOKEN: "secret", STEALTHLAB_MCP_URL: "u", GIT_DIR: "/x", OPENAI_API_KEY: "mine" },
                       { STEALTHLAB_TOKEN: "secret", STEALTHLAB_FAKE_SCENARIO: "{}", FOO: "1" });
  assert.equal(env.STEALTHLAB_TOKEN, undefined);
  assert.equal(env.STEALTHLAB_MCP_URL, undefined);
  assert.equal(env.GIT_DIR, undefined);
  assert.equal(env.STEALTHLAB_FAKE_SCENARIO, "{}");
  assert.equal(env.FOO, "1");
  assert.equal(env.PATH, "/bin");
  assert.equal(env.OPENAI_API_KEY, "mine"); // the user's own agent key, for the user's own agent
});

test("adapter gate (spec 8.10): refuses without VERIFIED_WITH, when not installed, or on a major-version mismatch", () => {
  const vw = { version: "1.0.0", date: "2026-09-27", source: "--help" };
  assert.match(refusalReason({ id: "x", VERIFIED_WITH: null }, { installed: true, version: "1.0.0" }), /no VERIFIED_WITH/);
  assert.match(refusalReason({ id: "x", VERIFIED_WITH: { version: "1.0.0" } }, { installed: true, version: "1.0.0" }), /no VERIFIED_WITH/);
  assert.match(refusalReason({ id: "x", VERIFIED_WITH: vw }, { installed: false }), /not installed/);
  assert.match(refusalReason({ id: "x", VERIFIED_WITH: vw }, { installed: true, version: "2.1.0" }), /major version 2/);
  assert.match(refusalReason({ id: "x", VERIFIED_WITH: vw }, { installed: true, version: "?" }), /no parseable version/);
  assert.equal(refusalReason({ id: "x", VERIFIED_WITH: vw }, { installed: true, version: "v1.9.3" }), null);
});

test("runChecks: exit codes, 40-line redacted tail, per-check timeout", async () => {
  const dir = tmpDir();
  fs.writeFileSync(path.join(dir, "loud.mjs"),
    "for (let i = 0; i < 100; i++) console.log('line ' + i);\nconsole.error('key sk-test-ABCDEFGHIJKLMNOPQRSTUVWX');\nprocess.exit(3);\n");
  fs.writeFileSync(path.join(dir, "slow.mjs"), "setTimeout(() => {}, 60000);\n");
  const res = await runChecks({ cwd: dir, checks: ["node loud.mjs", "node -e \"process.exit(0)\"", "node slow.mjs"], timeoutS: 1, env: process.env });
  assert.equal(res[0].exit, 3);
  const lines = res[0].tail.split("\n");
  assert.equal(lines.length, 40);
  assert.ok(!res[0].tail.includes("sk-test-ABCDEFGHIJKLMNOPQRSTUVWX"));
  assert.match(res[0].tail, /\[REDACTED:secret_key\]/);
  assert.equal(res[1].exit, 0);
  assert.notEqual(res[2].exit, 0);
  assert.match(res[2].tail, /timeout/);
  assert.ok(res[2].seconds < 10);
});

test("Windows: an npm .cmd shim resolves to the wrapped program, no shell", { skip: !IS_WIN && "Windows-only: .cmd shims exist only on Windows" }, () => {
  const dir = tmpDir();
  const target = path.join(dir, "node_modules", "tool", "bin", "cli.js");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "console.log(process.argv.slice(2).join(','))\n");
  const shim = path.join(dir, "tool.cmd");
  fs.writeFileSync(shim, '@ECHO off\r\nSETLOCAL\r\nIF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n  SET "_prog=node"\r\n)\r\n' +
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\tool\\bin\\cli.js" %*\r\n');
  const r = resolveCmdShim(shim);
  assert.equal(r.prefixArgs[0], target);
  const plan = planSpawn(shim, ["a task with | and \"quotes\" and %PATH%"], process.env);
  assert.equal(plan.shell, false);
  assert.equal(plan.args[1], "a task with | and \"quotes\" and %PATH%");
  // An unresolvable shim with a cmd-unsafe argument is refused, never shell-quoted.
  const opaque = path.join(dir, "opaque.cmd");
  fs.writeFileSync(opaque, "@echo off\r\necho hi\r\n");
  assert.throws(() => planSpawn(opaque, ["a | b"], process.env), /refusing to run opaque\.cmd/);
});

test("POSIX: commands resolve without any .cmd handling", { skip: IS_WIN && "POSIX-only: Windows uses the .cmd shim path" }, () => {
  const plan = planSpawn("node", ["x"], process.env);
  assert.equal(plan.shell, false);
  assert.deepEqual(plan.args, ["x"]);
});
