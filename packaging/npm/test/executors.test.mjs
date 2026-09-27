import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  ADAPTERS, getAdapter, listAdapters, runnable, assertRunnable, resolveBin, parseCmdShim, extractLearned,
} from "../lib/executors/index.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.join(here, "fixtures", "executors");
const fixture = (name) => fs.readFileSync(path.join(FIX, name), "utf8");
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "slexec-"));
const WIN = process.platform === "win32";
const REAL = ["opencode", "codex", "claude", "gemini", "openhands", "cline"];
const VERIFIED = ["opencode", "codex", "claude", "cline"];
// A bin path that needs no PATH lookup: .exe on Windows (spawned directly), a plain file elsewhere.
const STUB_BIN = WIN ? "C:\\agents\\agent.exe" : "/opt/agents/agent";

// --- registry -------------------------------------------------------------------

test("registry: all seven adapters, each with the fixed interface", () => {
  assert.deepEqual(Object.keys(ADAPTERS).sort(), [...REAL, "fake"].sort());
  for (const [id, a] of Object.entries(ADAPTERS)) {
    assert.equal(a.id, id);
    assert.ok("VERIFIED_WITH" in a, `${id} VERIFIED_WITH`);
    for (const fn of ["detect", "buildCommand", "parseOutput", "health"]) assert.equal(typeof a[fn], "function", `${id}.${fn}`);
  }
});

test("getAdapter: unknown id throws; fake only with STEALTHLAB_EXEC_ALLOW_FAKE=1", () => {
  assert.throws(() => getAdapter("nope", { env: {} }), /unknown executor "nope"/);
  assert.throws(() => getAdapter("__proto__", { env: {} }), /unknown executor/);
  assert.throws(() => getAdapter(undefined, { env: {} }), /unknown executor/);
  assert.throws(() => getAdapter("fake", { env: {} }), /test-only/);
  assert.throws(() => getAdapter("fake", { env: { STEALTHLAB_EXEC_ALLOW_FAKE: "true" } }), /test-only/);
  assert.equal(getAdapter("fake", { env: { STEALTHLAB_EXEC_ALLOW_FAKE: "1" } }).id, "fake");
  assert.equal(getAdapter("opencode", { env: {} }).id, "opencode");
  assert.ok(!listAdapters({ env: {} }).some((a) => a.id === "fake"));
  assert.ok(listAdapters({ env: { STEALTHLAB_EXEC_ALLOW_FAKE: "1" } }).some((a) => a.id === "fake"));
});

// --- verification gate --------------------------------------------------------------

test("VERIFIED_WITH: installed-and-checked agents carry version/date/source; unverified ones are null and refuse", () => {
  for (const id of VERIFIED) {
    const v = ADAPTERS[id].VERIFIED_WITH;
    assert.match(v.version, /^\d+\.\d+\.\d+$/, id);
    assert.equal(v.date, "2026-09-27", id);
    assert.match(v.source, /--help/, id);
  }
  for (const id of ["gemini", "openhands"]) {
    assert.equal(ADAPTERS[id].VERIFIED_WITH, null, id);
    assert.match(ADAPTERS[id].DOCS_SOURCE, /^https:\/\//, id);
    const r = runnable(ADAPTERS[id], { installed: true, version: "1.0.0", bin: "x" });
    assert.equal(r.ok, false);
    assert.match(r.reason, /unverified.*refuses to run/);
    assert.throws(() => assertRunnable(ADAPTERS[id], { installed: true, version: "1.0.0" }), /unverified/);
  }
});

test("runnable: not installed, unknown version and a different major version all refuse", () => {
  const a = ADAPTERS.opencode; // verified with 1.18.32
  assert.equal(runnable(a, { installed: false, version: null }).ok, false);
  assert.match(runnable(a, { installed: true, version: null }).reason, /could not read the installed version/);
  assert.match(runnable(a, { installed: true, version: "2.0.0" }).reason, /differs in major version/);
  assert.equal(runnable(a, { installed: true, version: "1.99.0" }).ok, true);
  assert.equal(runnable({ ...a, VERIFIED_WITH: undefined }, { installed: true, version: "1.18.32" }).ok, false);
});

// --- detect(): PATH lookup with stub executables ------------------------------------------

function npmNodeShim(dir, name, targetRel) {
  // Same shape npm's cmd-shim writes (captured from %APPDATA%\npm\codex.cmd on 2026-09-27).
  fs.writeFileSync(path.join(dir, `${name}.cmd`), [
    "@ECHO off", "GOTO start", ":find_dp0", "SET dp0=%~dp0", "EXIT /b", ":start", "SETLOCAL", "CALL :find_dp0", "",
    'IF EXIST "%dp0%\\node.exe" (', '  SET "_prog=%dp0%\\node.exe"', ") ELSE (", '  SET "_prog=node"', "  SET PATHEXT=%PATHEXT:;.JS;=;%", ")", "",
    `endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\${targetRel}" %*`, "",
  ].join("\r\n"));
}

test("detect (Windows): npm .cmd shim is unwrapped to node + script, never a shell", { skip: WIN ? false : "Windows-only: .cmd shims exist only on win32" }, async () => {
  const dir = tmp();
  const rel = "node_modules\\opencode-ai\\bin\\opencode.js";
  fs.mkdirSync(path.join(dir, "node_modules", "opencode-ai", "bin"), { recursive: true });
  fs.writeFileSync(path.join(dir, rel), "console.log('7.8.9');\n");
  npmNodeShim(dir, "opencode", rel);
  fs.writeFileSync(path.join(dir, "opencode"), "#!/bin/sh\necho posix-script-ignored-on-windows\n"); // npm drops this too
  const env = { Path: dir, PATHEXT: ".COM;.EXE;.BAT;.CMD", SystemRoot: process.env.SystemRoot };
  const d = await ADAPTERS.opencode.detect({ env });
  assert.deepEqual(d, { installed: true, version: "7.8.9", bin: path.join(dir, "opencode.cmd") });
  const c = ADAPTERS.opencode.buildCommand({ task: "t", env });
  assert.equal(c.cmd, process.execPath); // no dp0\node.exe in the stub dir -> the current node
  assert.equal(c.args[0], path.join(dir, rel));
  assert.equal(c.args[1], "run");
  // Wrong major version than VERIFIED_WITH (1.x) -> unhealthy.
  const h = await ADAPTERS.opencode.health({ env });
  assert.equal(h.healthy, false);
  assert.match(h.detail, /major version/);
});

test("detect (Windows): an unrecognised .cmd is reported but refused (no shell fallback)", { skip: WIN ? false : "Windows-only: .cmd shims exist only on win32" }, async () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, "codex.cmd"), "@echo off\r\necho 0.153.4\r\n");
  const env = { Path: dir, PATHEXT: ".EXE;.CMD" };
  const d = await ADAPTERS.codex.detect({ env });
  assert.equal(d.installed, true);
  assert.equal(d.version, null);
  assert.match(d.detail, /unrecognised launcher shim/);
  assert.throws(() => ADAPTERS.codex.buildCommand({ task: "t", env }), /refusing to run it through a shell/);
  assert.equal((await ADAPTERS.codex.health({ env })).healthy, false);
});

test("detect (POSIX): executable stub on PATH; a non-executable file is ignored", { skip: WIN ? "POSIX-only: exec bits and #! scripts do not apply on win32" : false }, async () => {
  const dir = tmp();
  const bin = path.join(dir, "cline");
  fs.writeFileSync(bin, "#!/bin/sh\necho 3.0.99\n");
  fs.chmodSync(bin, 0o755);
  const other = tmp();
  fs.writeFileSync(path.join(other, "codex"), "#!/bin/sh\necho 0.153.4\n"); // not chmod +x
  const env = { PATH: `${other}:${dir}` };
  assert.deepEqual(await ADAPTERS.cline.detect({ env }), { installed: true, version: "3.0.99", bin });
  assert.equal((await ADAPTERS.cline.health({ env })).healthy, true);
  assert.deepEqual(await ADAPTERS.codex.detect({ env }), { installed: false, version: null, bin: null });
});

test("detect: not on PATH -> not installed, buildCommand throws, health unhealthy", async () => {
  const env = WIN ? { Path: tmp(), PATHEXT: ".EXE;.CMD" } : { PATH: tmp() };
  for (const id of REAL) {
    assert.deepEqual(await ADAPTERS[id].detect({ env }), { installed: false, version: null, bin: null }, id);
    assert.throws(() => ADAPTERS[id].buildCommand({ task: "t", env }), /not installed/, id);
    assert.equal((await ADAPTERS[id].health({ env })).healthy, false, id);
  }
});

test("resolveBin: PATHEXT order and the Windows 'Path' key; parseCmdShim on real npm shim shapes", () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, "agent.cmd"), "x");
  fs.writeFileSync(path.join(dir, "agent.exe"), "x");
  assert.deepEqual(resolveBin("agent", { env: { Path: dir, PATHEXT: ".EXE;.CMD" }, platform: "win32" }), { path: path.win32.join(dir, "agent.exe"), kind: "exe" });
  assert.deepEqual(resolveBin("agent", { env: { PATH: dir, PATHEXT: ".CMD;.EXE" }, platform: "win32" }), { path: path.win32.join(dir, "agent.cmd"), kind: "cmd" });
  assert.equal(resolveBin("missing", { env: { Path: dir }, platform: "win32" }), null);

  // opencode.cmd as npm wrote it on this machine: target is an .exe -> spawned directly.
  const oc = '@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n"%dp0%\\node_modules\\opencode-ai\\bin\\opencode.exe"   %*\r\n';
  assert.deepEqual(parseCmdShim(oc, "C:\\npm\\opencode.cmd"), { cmd: "C:\\npm\\node_modules\\opencode-ai\\bin\\opencode.exe", prefix: [] });
  // codex.cmd shape: node script; the IF EXIST node.exe reference is the interpreter, not the target.
  const cx = 'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n)\r\nendLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n';
  const p = parseCmdShim(cx, "C:\\nowhere\\codex.cmd");
  assert.deepEqual(p.prefix, ["C:\\nowhere\\node_modules\\@openai\\codex\\bin\\codex.js"]);
  assert.equal(p.cmd, process.execPath);
  assert.equal(parseCmdShim("@echo off\r\nnode foo.js %*\r\n", "C:\\x\\y.cmd"), null);
  assert.equal(parseCmdShim('"%dp0%\\other.cmd" %*', "C:\\x\\y.cmd"), null);
});

// --- buildCommand shapes ------------------------------------------------------------------

const baseEnv = () => ({ PATH: "/usr/bin", HOME: "/home/u", STEALTHLAB_TOKEN: "sl_secret", STEALTHLAB_MCP_URL: "https://x", OPENAI_API_KEY: "kept-for-the-agent" });

function build(id, extra = {}) {
  return ADAPTERS[id].buildCommand({ task: "Fix add() in calc.py", model: "prov/model-1", worktree: "/tmp/wt", timeoutS: 120, env: baseEnv(), bin: STUB_BIN, ...extra });
}

test("buildCommand: exact argv per adapter (task as one argv element or stdin, never a shell string)", () => {
  assert.deepEqual(build("opencode").args, ["run", "--format", "json", "--auto", "--dir", "/tmp/wt", "--model", "prov/model-1", "Fix add() in calc.py"]);
  const cx = build("codex");
  assert.deepEqual(cx.args, ["exec", "--json", "--color", "never", "--sandbox", "workspace-write", "--cd", "/tmp/wt", "--model", "prov/model-1", "-"]);
  assert.equal(cx.stdinText, "Fix add() in calc.py");
  assert.deepEqual(build("claude").args, ["-p", "Fix add() in calc.py", "--output-format", "json", "--no-session-persistence",
    "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--model", "prov/model-1", "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep"]);
  assert.deepEqual(build("cline").args, ["--json", "--auto-approve", "true", "--cwd", "/tmp/wt", "--model", "prov/model-1", "--timeout", "120", "Fix add() in calc.py"]);
  assert.deepEqual(build("gemini").args, ["--output-format", "json", "--approval-mode", "yolo", "--model", "prov/model-1", "--prompt", "Fix add() in calc.py"]);
  const oh = build("openhands");
  assert.deepEqual(oh.args, ["--headless", "--json", "--override-with-envs", "--task", "Fix add() in calc.py"]);
  assert.equal(oh.env.LLM_MODEL, "prov/model-1");
  assert.equal("LLM_API_KEY" in oh.env, false);
  for (const id of REAL) assert.equal(build(id).cmd, STUB_BIN, id);
});

test("buildCommand: no model -> no model flag; strips STEALTHLAB_*, keeps PATH/HOME/agent keys", () => {
  for (const id of REAL) {
    const c = build(id, { model: undefined });
    assert.ok(!c.args.includes("--model"), id);
    assert.equal(c.env.STEALTHLAB_TOKEN, undefined, id);
    assert.equal(c.env.STEALTHLAB_MCP_URL, undefined, id);
    assert.equal(c.env.PATH, "/usr/bin", id);
    assert.equal(c.env.HOME, "/home/u", id);
    assert.equal(c.env.OPENAI_API_KEY, "kept-for-the-agent", id); // the user's own agent, the user's own key
  }
  assert.equal(build("openhands", { model: undefined }).args.includes("--override-with-envs"), false);
});

test("buildCommand: a task starting with '-' cannot become a flag; empty task throws", () => {
  for (const id of ["opencode", "claude", "cline", "gemini", "openhands"]) {
    const c = build(id, { task: "--dangerously-skip-permissions do x" });
    assert.ok(!c.args.includes("--dangerously-skip-permissions do x"), id);
    assert.ok(c.args.includes("Task: --dangerously-skip-permissions do x"), id);
    assert.throws(() => build(id, { task: "   " }), /empty/, id);
  }
  assert.throws(() => build("codex", { task: "" }), /empty/);
});

test("no invented flags: every flag a verified adapter emits appears in the captured --help", () => {
  const help = { opencode: "opencode-run-help.txt", codex: "codex-exec-help.txt", claude: "claude-help.txt", cline: "cline-help.txt" };
  const versions = fixture("versions.txt");
  for (const id of VERIFIED) {
    const text = fixture(help[id]);
    const flags = build(id).args.filter((a) => /^--?[a-zA-Z]/.test(a) && !a.startsWith("Task:"));
    assert.ok(flags.length >= 3, id);
    for (const f of flags) assert.ok(new RegExp(`(^|[\\s,])${f.replace(/[-]/g, "\\-")}([\\s,=<\\[]|$)`, "m").test(text), `${id}: ${f} not in ${help[id]}`);
    assert.ok(versions.includes(ADAPTERS[id].VERIFIED_WITH.version), `${id}: VERIFIED_WITH.version matches the captured --version`);
  }
});

// --- parseOutput ----------------------------------------------------------------------------

test("parseOutput: opencode JSON events -> last text, summed tokens/cost, learned items", () => {
  const r = ADAPTERS.opencode.parseOutput({ stdout: fixture("opencode.jsonl"), stderr: "", exitCode: 0 });
  assert.match(r.finalMessage, /^Fixed add\(\) in calc\.py/);
  assert.deepEqual(r.learned, ["tests import calc from repo root"]);
  assert.deepEqual(r.tokens, { in: 1200, out: 100 });
  assert.equal(r.costUsd, 0.0042);
  const e = ADAPTERS.opencode.parseOutput({ stdout: '{"type":"error","timestamp":1,"sessionID":"s","error":{"name":"APIError","data":{"message":"model not found"}}}\n' });
  assert.equal(e.finalMessage, "error: model not found");
});

test("parseOutput: codex JSONL (documented example) -> agent_message text and usage", () => {
  const r = ADAPTERS.codex.parseOutput({ stdout: fixture("codex.jsonl"), stderr: "", exitCode: 0 });
  assert.equal(r.finalMessage, "Repo contains docs, sdk, and examples directories.");
  assert.deepEqual(r.tokens, { in: 24763, out: 122 });
  assert.equal(r.costUsd, null);
  assert.equal(ADAPTERS.codex.parseOutput({ stdout: '{"type":"turn.failed","error":{"message":"boom"}}' }).finalMessage, "error: boom");
});

test("parseOutput: claude json result -> result, cost, tokens incl. cache, learned", () => {
  const r = ADAPTERS.claude.parseOutput({ stdout: fixture("claude.json"), stderr: "", exitCode: 0 });
  assert.match(r.finalMessage, /^Fixed add\(\)/);
  assert.deepEqual(r.learned, ["conftest.py sets rootdir", "add was using subtraction"]);
  assert.deepEqual(r.tokens, { in: 1110, out: 55 });
  assert.equal(r.costUsd, 0.0123);
});

test("parseOutput: cline 3.x agent_event stream and the documented legacy say/ask shape", () => {
  const r = ADAPTERS.cline.parseOutput({ stdout: fixture("cline.jsonl"), stderr: "", exitCode: 0 });
  assert.equal(r.finalMessage, "Done: add() fixed, tests pass.");
  assert.deepEqual(r.tokens, { in: 900, out: 120 });
  assert.equal(r.costUsd, 0.002);
  assert.equal(ADAPTERS.cline.parseOutput({ stdout: fixture("cline-legacy-docs.jsonl") }).finalMessage, "I'll create the file now.");
});

test("parseOutput: gemini json and openhands jsonl (docs-derived)", () => {
  const g = ADAPTERS.gemini.parseOutput({ stdout: fixture("gemini.json") });
  assert.equal(g.finalMessage, "Fixed add() in calc.py.");
  assert.deepEqual(g.tokens, { in: 500, out: 42 });
  const o = ADAPTERS.openhands.parseOutput({ stdout: fixture("openhands.jsonl") });
  assert.equal(o.finalMessage, "File created successfully");
  assert.equal(o.tokens, null);
});

test("parseOutput: plain text degrades gracefully for every adapter", () => {
  const text = "Changed calc.py.\n\nLearned:\n- one\n- two\n";
  for (const id of Object.keys(ADAPTERS)) {
    const r = ADAPTERS[id].parseOutput({ stdout: text, stderr: "", exitCode: 0 });
    assert.equal(r.finalMessage, text.trim(), id);
    assert.deepEqual(r.learned, ["one", "two"], id);
    assert.equal(r.tokens, null, id);
    assert.equal(r.costUsd, null, id);
    const empty = ADAPTERS[id].parseOutput({ stdout: "", stderr: "fatal: not logged in", exitCode: 1 });
    assert.equal(empty.finalMessage, "fatal: not logged in", id);
    assert.deepEqual(ADAPTERS[id].parseOutput({}).learned, [], id);
  }
});

test("extractLearned: header variants, inline value, cap of 5, none -> []", () => {
  assert.deepEqual(extractLearned("Result: ok\n**Anything learned:** the fixture needs cwd"), ["the fixture needs cwd"]);
  assert.deepEqual(extractLearned("## Surprises\n1. a\n2) b\n* c\n- d\n- e\n- f\n"), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(extractLearned("Learned: none"), []);
  assert.deepEqual(extractLearned("all good, nothing else"), []);
});

// --- fake adapter end-to-end -----------------------------------------------------------------

function runCmd({ cmd, args, env, stdinText }, cwd) {
  return new Promise((resolve) => {
    const p = spawn(cmd, args, { cwd, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    let stdout = "", stderr = "";
    p.stdout.on("data", (d) => (stdout += d));
    p.stderr.on("data", (d) => (stderr += d));
    p.on("close", (exitCode) => resolve({ stdout, stderr, exitCode }));
    p.stdin.end(stdinText ?? "");
  });
}

test("fake: scenario edits files, reports final message/learned/tokens, exit code, and a planted secret", async () => {
  const fake = getAdapter("fake", { env: { STEALTHLAB_EXEC_ALLOW_FAKE: "1" } });
  assert.deepEqual(await fake.detect({ env: {} }), { installed: true, version: "1.0.0", bin: fake.bin });
  assert.equal((await fake.health({ env: {} })).healthy, true);
  assert.equal(runnable(fake, await fake.detect({ env: {} })).ok, true);
  const wt = tmp();
  const scenario = { edits: [{ path: "src/calc.py", content: "def add(a, b):\n    return a + b\n" }], exitCode: 2, finalMessage: "fixed", learned: ["x"], tokens: { in: 5, out: 6 }, printSecret: true, sleepMs: 250 };
  const c = fake.buildCommand({ task: "do it", model: "fake/m", worktree: wt, env: { ...process.env, STEALTHLAB_TOKEN: "sl_x", STEALTHLAB_FAKE_SCENARIO: JSON.stringify(scenario), NODE_TEST_CONTEXT: "child-v8" } });
  assert.equal(c.env.STEALTHLAB_TOKEN, undefined);
  assert.equal(c.env.NODE_TEST_CONTEXT, undefined);
  assert.equal(c.stdinText, "do it");
  const out = await runCmd(c, wt);
  assert.equal(out.exitCode, 2);
  assert.equal(fs.readFileSync(path.join(wt, "src", "calc.py"), "utf8"), scenario.edits[0].content);
  assert.match(out.stdout, /model=fake\/m task_chars=5/);
  assert.match(out.stdout, /working 0/);
  assert.match(out.stdout, /sk-test-[A-Za-z0-9]{20,}/); // unredacted on purpose: the runtime must redact it
  const r = fake.parseOutput(out);
  assert.match(r.finalMessage, /^fixed \(key sk-test-/);
  assert.deepEqual(r.learned, ["x"]);
  assert.deepEqual(r.tokens, { in: 5, out: 6 });
});

test("fake: spawnGrandchild announces agent/child/grandchild pids and all exit within childLifeMs", async () => {
  const fake = ADAPTERS.fake;
  const wt = tmp();
  const pidFile = path.join(wt, "pids.txt");
  const c = fake.buildCommand({ task: "t", worktree: wt, env: { ...process.env, STEALTHLAB_FAKE_SCENARIO: JSON.stringify({ spawnGrandchild: true, childLifeMs: 1200, pidFile }) } });
  const out = await runCmd(c, wt);
  assert.equal(out.exitCode, 0);
  const pids = Object.fromEntries(fs.readFileSync(pidFile, "utf8").trim().split(/\r?\n/).map((l) => l.split(" ")));
  assert.deepEqual(Object.keys(pids).sort(), ["agent", "child", "grandchild"]);
  await new Promise((r) => setTimeout(r, 1500));
  for (const [who, pid] of Object.entries(pids)) {
    let alive = true;
    try { process.kill(Number(pid), 0); } catch { alive = false; }
    assert.equal(alive, false, `${who} ${pid} still alive`);
  }
});
