import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  CAPTURE_HOOKS, buildReport, looksLikeTest, lookupIdentity, modelFromTranscript, onPostToolUse, onStop,
  rememberLookup, runCaptureHook, runCaptureWorker, sessionFile, testVerdict,
} from "../lib/capture_hook.mjs";
import { removeClaudeHook, upsertClaudeHook } from "../lib/clients.mjs";
import { REPORT_FIELDS } from "../lib/exec/evidence.mjs";
import { runPromptHook } from "../lib/hook.mjs";

const tmpEnv = () => ({ STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "sl-capture-")) });

const RESOLVED = { outcome: "resolved", goal: { goal_id: "g1" }, procedures: [{ procedure_id: "p1", goal_id: "g1", steps: [] }] };
const SUGGESTED = { outcome: "ambiguous", suggested: { goal_id: "g2", procedure_id: "p2", goal_name: "Near" } };

function transcript(dir, model = "claude-sonnet-5") {
  const file = path.join(dir, "t.jsonl");
  fs.writeFileSync(file, [
    JSON.stringify({ type: "user", message: { role: "user", content: "fix the parser" } }),
    JSON.stringify({ type: "assistant", message: { role: "assistant", model, content: [] } }),
  ].join("\n") + "\n");
  return file;
}

test("verdicts come from the runner's own summary line; unknown output is null", () => {
  assert.equal(testVerdict("===== 3 passed in 0.12s ====="), true);
  assert.equal(testVerdict("3 passed in 0.02s"), true, "pytest -q");
  assert.equal(testVerdict("==== 1 failed, 2 passed in 0.30s ===="), false);
  assert.equal(testVerdict("1 error in 0.05s"), false);
  assert.equal(testVerdict("Ran 4 tests in 0.001s\n\nOK"), true);
  assert.equal(testVerdict("Ran 4 tests in 0.001s\n\nFAILED (failures=1)"), false);
  assert.equal(testVerdict("Tests:       2 failed, 5 passed, 7 total"), false);
  assert.equal(testVerdict("Tests:       7 passed, 7 total"), true);
  assert.equal(testVerdict("  5 passing (20ms)\n  1 failing"), false);
  assert.equal(testVerdict("ok  \tgithub.com/x/y\t0.01s"), true);
  assert.equal(testVerdict("--- FAIL: TestAdd (0.00s)\nFAIL\tgithub.com/x/y"), false);
  assert.equal(testVerdict("test result: ok. 3 passed; 0 failed"), true);
  assert.equal(testVerdict("test result: FAILED. 1 passed; 1 failed"), false);
  assert.equal(testVerdict("everything looks fine"), null);
  assert.equal(testVerdict(""), null);
});

test("test commands are recognised; other commands are not", () => {
  for (const c of ["pytest -q", "python -m pytest tests/", "npm test", "go test ./...", "cargo test", "npx vitest run"]) {
    assert.equal(looksLikeTest(c), true, c);
  }
  for (const c of ["ls -la", "git diff", "python app.py", "npm install"]) assert.equal(looksLikeTest(c), false, c);
});

test("lookup identity: resolved way, else the suggested candidate; related examples never count", () => {
  assert.deepEqual(lookupIdentity(RESOLVED), { outcome: "resolved", goal_id: "g1", procedure_id: "p1" });
  assert.deepEqual(lookupIdentity(SUGGESTED), { outcome: "suggested", goal_id: "g2", procedure_id: "p2" });
  assert.equal(lookupIdentity({ outcome: "no_match", related_examples: [{ goal_id: "g9", procedure_id: "p9" }] }), null);
  assert.equal(lookupIdentity({ outcome: "ambiguous", candidates: [] }), null);
  assert.equal(lookupIdentity(null), null);
});

test("full flow: lookup -> test runs -> Stop sends ONE whitelisted report per prompt, via a detached worker", () => {
  const env = tmpEnv();
  const base = { session_id: "sess-1", prompt_id: "pr-1" };
  assert.equal(rememberLookup(base, RESOLVED, { env }), true);
  assert.equal(onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "ls" }, tool_response: { stdout: "3 passed in 1s" } }, { env }), null);
  assert.equal(onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest -q" }, tool_response: { stdout: "1 failed, 2 passed in 0.3s", stderr: "" } }, { env }), false);
  assert.equal(onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest -q" }, tool_response: { stdout: "3 passed in 0.2s" } }, { env }), true);

  const spawned = [];
  const r = onStop({ ...base, transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: true, spawnWorker: (f) => spawned.push(f) });
  assert.equal(r.status, "detached");
  assert.equal(spawned.length, 1);
  assert.deepEqual(Object.keys(r.report).filter((k) => r.report[k] !== undefined).sort(),
    ["accepted", "attempt_index", "check_kind", "goal_id", "instance_key", "model", "procedure_id", "scaffold"]);
  for (const k of Object.keys(r.report)) assert.ok(REPORT_FIELDS.includes(k), `${k} is a whitelisted report field`);
  assert.equal(r.report.accepted, true, "the LAST verdict decides");
  assert.equal(r.report.model, "claude-sonnet-5");
  assert.equal(r.report.scaffold, "claude-code");
  assert.equal(r.report.goal_id, "g1");
  assert.match(r.report.instance_key, /^cc-[0-9a-f]{24}$/);
  const job = JSON.parse(fs.readFileSync(spawned[0], "utf8"));
  assert.deepEqual(job.report, r.report);

  // Stop fires every turn: the same prompt is never reported twice
  assert.equal(onStop({ ...base, transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: true, spawnWorker: () => assert.fail() }).status, "nothing-to-report");
  // a new prompt starts a new record
  rememberLookup({ ...base, prompt_id: "pr-2" }, SUGGESTED, { env });
  onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "npm test" }, tool_response: { stdout: "Tests:  1 failed, 1 total" } }, { env });
  const r2 = onStop({ ...base, transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: true, spawnWorker: () => {} });
  assert.equal(r2.report.accepted, false);
  assert.equal(r2.report.procedure_id, "p2");
  assert.notEqual(r2.report.instance_key, r.report.instance_key);
});

test("privacy: the session file holds ids, verdicts and times only -- no prompt, command or output", () => {
  const env = tmpEnv();
  const base = { session_id: "sess-p", prompt_id: "pr", prompt: "SECRET-PROMPT fix the billing bug for ACME" };
  rememberLookup(base, RESOLVED, { env });
  onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest tests/test_acme_secret.py" }, tool_response: { stdout: "SECRET-OUTPUT\n2 passed in 0.1s" } }, { env });
  const text = fs.readFileSync(sessionFile(env, "sess-p"), "utf8");
  for (const s of ["SECRET-PROMPT", "ACME", "acme_secret", "SECRET-OUTPUT", "pytest"]) assert.ok(!text.includes(s), s);
});

test("nothing is reported without a token, a lookup identity, a known verdict or a model; STEALTHLAB_CAPTURE=off disables", () => {
  const env = tmpEnv();
  const base = { session_id: "sess-2", prompt_id: "p" };
  const tr = transcript(env.STEALTHLAB_HOME);
  assert.equal(onStop({ ...base, transcript_path: tr }, { env, hasToken: true }).status, "no-lookup");
  rememberLookup(base, RESOLVED, { env });
  assert.equal(onStop({ ...base, transcript_path: tr }, { env, hasToken: false }).status, "no-token");
  assert.equal(onStop({ ...base, transcript_path: tr }, { env, hasToken: true }).status, "nothing-to-report", "no test run yet");
  onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest" }, tool_response: { stdout: "hmm" } }, { env });
  assert.equal(onStop({ ...base, transcript_path: tr }, { env, hasToken: true }).status, "nothing-to-report", "unknown verdict");
  assert.equal(buildReport({ lookup: { goal_id: "g" }, tests: [{ verdict: true }] }, { sessionId: "s", model: null }), null, "no model");
  // a later prompt the lookup could not identify clears the record, so it can't inherit the earlier Goal
  rememberLookup({ ...base, prompt_id: "p2" }, { outcome: "no_match" }, { env });
  assert.equal(fs.existsSync(sessionFile(env, "sess-2")), false);
  const off = { ...env, STEALTHLAB_CAPTURE: "off" };
  assert.equal(rememberLookup(base, RESOLVED, { env: off }), false);
  assert.equal(onStop({ ...base, transcript_path: tr }, { env: off, hasToken: true }).status, "disabled");
  assert.equal(sessionFile(env, "../../etc/passwd"), null, "session ids are validated");
});

test("model comes from the last assistant message in the transcript tail", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-tr-"));
  const file = path.join(dir, "t.jsonl");
  fs.writeFileSync(file, [
    JSON.stringify({ message: { model: "claude-haiku-4-5-20251001" } }),
    JSON.stringify({ message: { model: "claude-opus-5-5" } }),
    JSON.stringify({ message: { model: "<synthetic>" } }),
    "not json",
  ].join("\n"));
  assert.equal(modelFromTranscript(file), "claude-opus-5-5");
  assert.equal(modelFromTranscript(path.join(dir, "missing.jsonl")), null);
});

test("worker: sends the job's report and deletes the job file", async () => {
  const env = tmpEnv();
  const job = path.join(env.STEALTHLAB_HOME, "job.json");
  const report = { model: "m", scaffold: "claude-code", accepted: true, instance_key: "cc-x", goal_id: "g", check_kind: "tests" };
  fs.writeFileSync(job, JSON.stringify({ report }));
  const sent = [];
  const r = await runCaptureWorker(job, { env, report: async (p) => { sent.push(p); return { reported: true }; } });
  assert.deepEqual(sent, [report]);
  assert.equal(r.reported, true);
  assert.equal(fs.existsSync(job), false);
});

test("CLI entry never throws on malformed input", async () => {
  const env = tmpEnv();
  assert.equal((await runCaptureHook("capture-tool", { stdinText: "{not json", env })).status, "malformed");
  assert.equal((await runCaptureHook("capture-stop", { stdinText: "{}", env, hasToken: true })).status, "no-lookup");
});

test("the prompt hook remembers what its lookup identified", async () => {
  const env = tmpEnv();
  const fetchImpl = async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    const h = { get: (k) => ({ "mcp-session-id": "S1" })[k] };
    const reply = (result) => ({ ok: true, status: 200, headers: h, text: async () => JSON.stringify({ jsonrpc: "2.0", id: body.id, result }) });
    if (body.method === "initialize") return reply({ protocolVersion: "2025-06-18" });
    if (body.method === "tools/call") return reply({ content: [{ type: "text", text: JSON.stringify(RESOLVED) }] });
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
  await runPromptHook({
    stdinText: JSON.stringify({ session_id: "sess-h", prompt_id: "pp", prompt: "fix the date parser so february 30 raises", cwd: os.tmpdir() }),
    settings: { url: "https://example.invalid/mcp" }, userAgent: "t", env, fetchImpl, write: () => {}, log: () => {},
  });
  const s = JSON.parse(fs.readFileSync(sessionFile(env, "sess-h"), "utf8"));
  assert.deepEqual(s.lookup, { outcome: "resolved", goal_id: "g1", procedure_id: "p1" });
});

test("install adds PostToolUse(Bash) and Stop next to the knowledge hook; uninstall restores the user's file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-settings-"));
  const file = path.join(dir, "settings.json");
  const user = { model: "opus", hooks: {
    PostToolUse: [{ matcher: "Edit", hooks: [{ type: "command", command: "prettier-hook" }] }],
    Stop: [{ hooks: [{ type: "command", command: "notify-me" }] }] } };
  fs.writeFileSync(file, JSON.stringify(user));
  const spec = { command: "node", args: ["C:\\x\\bin\\stealthlab-mcp.mjs", "hook-prompt"] };
  upsertClaudeHook(file, spec);
  upsertClaudeHook(file, spec);   // idempotent
  const doc = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.equal(doc.hooks.UserPromptSubmit.length, 1);
  assert.equal(doc.hooks.PostToolUse.length, 3);              // the user's, capture, and the model guard's report hook
  assert.equal(doc.hooks.PostToolUse[1].matcher, CAPTURE_HOOKS.PostToolUse.matcher);
  assert.match(doc.hooks.PostToolUse[2].hooks[0].command, /hook route-report$/);
  assert.match(doc.hooks.PreToolUse[0].hooks[0].command, /hook route-subagent$/);
  assert.match(doc.hooks.PostToolUse[1].hooks[0].command, /stealthlab-mcp\.mjs"? hook capture-tool$/);   // quoted when the path has backslashes
  assert.match(doc.hooks.Stop[1].hooks[0].command, /stealthlab-mcp\.mjs"? hook capture-stop$/);
  assert.equal(removeClaudeHook(file), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), user);
});

// --- the library grows by itself (STEALTHLAB_LIBRARY_AUTO) ----------------------------------------

import { execFileSync } from "node:child_process";

function stealthRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-autolib-"));
  const g = (...a) => execFileSync("git", ["-C", dir, ...a], { stdio: "pipe" });
  g("init", "-q");
  g("config", "user.email", "t@example.com");
  g("config", "user.name", "t");
  fs.writeFileSync(path.join(dir, "calc.py"), "def add(a, b):\n    return a - b\n");
  fs.mkdirSync(path.join(dir, ".stealth"));
  fs.writeFileSync(path.join(dir, ".stealth", "routing.md"), "");
  g("add", "-A");
  g("commit", "-q", "-m", "init");
  return dir;
}

function solvedTurn(env, repo, { sid = "auto-1", verdictOut = "3 passed in 0.2s", change = true, reply = RESOLVED } = {}) {
  const base = { session_id: sid, prompt_id: "p", cwd: repo, prompt: "Fix add() in calc.py\nit subtracts instead of adding" };
  rememberLookup(base, reply, { env });
  if (change) fs.writeFileSync(path.join(repo, "calc.py"), "def add(a, b):\n    return a + b\n");
  onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest -q tests/test_calc.py" }, tool_response: { stdout: verdictOut } }, { env });
  return onStop({ ...base, transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: false });
}

test("library auto: a passing turn with a diff adds one verified entry (title, check, solution), once", () => {
  const env = tmpEnv();
  const repo = stealthRepo();
  const out = solvedTurn(env, repo);
  assert.match(out.library_entry, /^L-[0-9a-f]+$/);
  const lib = fs.readFileSync(path.join(repo, ".stealth", "library.md"), "utf8");
  assert.ok(lib.includes("Fix add() in calc.py"), "the prompt's first line is the title");
  assert.ok(!lib.includes("subtracts instead"), "only the first line");
  assert.ok(lib.includes("pytest -q tests/test_calc.py"), "the passing test command is the check");
  const diff = fs.readFileSync(path.join(repo, ".stealth", "library", "solutions", `${out.library_entry}.diff`), "utf8");
  assert.match(diff, /\+    return a \+ b/);
  assert.ok(!diff.includes(".stealth/"), "the solution never includes .stealth's own files");
  const again = onStop({ session_id: "auto-1", transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: false });
  assert.equal(again.library_entry, undefined, "one entry per prompt");
});

test("library auto: nothing is added for a failing or unknown verdict, no diff, an entry the lookup matched, or when off", () => {
  assert.equal(solvedTurn(tmpEnv(), stealthRepo(), { verdictOut: "1 failed, 2 passed in 0.3s" }).library_entry, undefined);
  assert.equal(solvedTurn(tmpEnv(), stealthRepo(), { verdictOut: "hmm" }).library_entry, undefined);
  assert.equal(solvedTurn(tmpEnv(), stealthRepo(), { change: false }).library_entry, undefined);
  const matched = { ...RESOLVED, library_matches: [{ id: "L-abcd", relation: "matches" }] };
  assert.equal(solvedTurn(tmpEnv(), stealthRepo(), { reply: matched }).library_entry, undefined);
  const env = { ...tmpEnv(), STEALTHLAB_LIBRARY_AUTO: "off" };
  const repo = stealthRepo();
  assert.equal(solvedTurn(env, repo, { sid: "off-1" }).library_entry, undefined);
  assert.equal(fs.existsSync(path.join(repo, ".stealth", "library.md")), false);
  const text = fs.readFileSync(sessionFile(env, "off-1"), "utf8");
  for (const s of ["Fix add", "pytest"]) assert.ok(!text.includes(s), `off: the session file keeps no ${s}`);
});

test("library auto: the worker asks for the new Ways' codes, and a token-less run still adds the entry", async () => {
  const env = tmpEnv();
  const repo = stealthRepo();
  const base = { session_id: "w-1", prompt_id: "p", cwd: repo, prompt: "Fix add() in calc.py" };
  rememberLookup(base, RESOLVED, { env });
  fs.writeFileSync(path.join(repo, "calc.py"), "def add(a, b):\n    return a + b\n");
  onPostToolUse({ ...base, tool_name: "Bash", tool_input: { command: "pytest -q" }, tool_response: { stdout: "1 passed in 0.1s" } }, { env });
  let job = null;
  const out = onStop({ ...base, transcript_path: transcript(env.STEALTHLAB_HOME) }, { env, hasToken: true, spawnWorker: (f) => { job = f; } });
  assert.equal(out.status, "detached");
  assert.ok(out.library_entry);
  assert.equal(JSON.parse(fs.readFileSync(job, "utf8")).code_ways, repo);
});

test("Windows: a test run through Claude Code's PowerShell tool is captured like one through Bash", () => {
  const env = tmpEnv();
  const base = { session_id: "ps-1", prompt_id: "p" };
  rememberLookup(base, RESOLVED, { env });
  assert.equal(onPostToolUse({ ...base, tool_name: "PowerShell", tool_input: { command: "python -m pytest -q test_check.py" },
    tool_response: { stdout: "1 passed in 1.29s" } }, { env }), true);
  assert.equal(onPostToolUse({ ...base, tool_name: "Read", tool_input: { command: "pytest" }, tool_response: {} }, { env }), null);
  assert.equal(CAPTURE_HOOKS.PostToolUse.matcher, "Bash|PowerShell");
});
