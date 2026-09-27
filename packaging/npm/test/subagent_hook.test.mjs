// Spec section 8 items 12 (hook) and 13 (Windows path cases, hook side). runChecks / reportModelRun
// (lib/exec/**, Builder A) are injected as stubs, so these tests load and pass without lib/exec.
// Payload samples are the SubagentStart / SubagentStop examples from
// https://code.claude.com/docs/en/hooks (read 2026-09-27), with only cwd / task_prompt filled in.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  buildStopJob, findNodeLine, hookLogPath, nodeRefFromPrompt, parseNodeLine, parseStealthResult, procedureFor,
  processStopJob, runStopWorker, runSubagentHook,
} from "../lib/subagent_hook.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.join(HERE, "..", "bin", "stealthlab-mcp.mjs");
const tmp = (p = "slsub-") => fs.mkdtempSync(path.join(os.tmpdir(), p));

const RUN_MD = [
  "# run.md",
  "NODE|N-2|done|Install docx|step=P-1:2|claims=R-001..R-002|deps=-|check=node -e \"process.exit(0)\"",
  "NODE|N-3|ready|Configure page size|step=P-1:3|claims=R-001..R-004|deps=N-2|check=node build.js | grep -q ok && test -s out.docx",
  "NODE|N-4|ready|Write notes|step=-|claims=-|deps=N-3|check=-",
].join("\r\n");   // CRLF, as an editor on Windows writes it
const PROCEDURES_MD = "PROCEDURE|P-1|0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b|v2|Export DOCX|goal=export a report\nSTEP|P-1:3|action|set page size|locator=-|needs=-|check=-\n";

function planRepo(dir = tmp()) {
  fs.mkdirSync(path.join(dir, ".stealth"), { recursive: true });
  fs.writeFileSync(path.join(dir, ".stealth", "run.md"), RUN_MD);
  fs.writeFileSync(path.join(dir, ".stealth", "procedures.md"), PROCEDURES_MD);
  return dir;
}

const docStart = (cwd, task_prompt) => ({
  session_id: "abc123", prompt_id: "550e8400-e29b-41d4-a716-446655440000",
  transcript_path: "/home/user/.claude/projects/.../transcript.jsonl", cwd,
  permission_mode: "default", hook_event_name: "SubagentStart",
  agent_id: "agent-uuid-123", agent_type: "stealth-executor", task_prompt,
});
const docStop = (cwd, last_assistant_message = "Here is my analysis...") => ({
  session_id: "abc123", prompt_id: "550e8400-e29b-41d4-a716-446655440000",
  transcript_path: "/home/user/.claude/projects/.../transcript.jsonl", cwd,
  permission_mode: "default", hook_event_name: "SubagentStop",
  agent_id: "agent-uuid-123", agent_type: "stealth-executor", stop_hook_active: true,
  agent_transcript_path: "/path/to/subagent/transcript.jsonl", last_assistant_message, effort: { level: "high" },
});

function stubs({ exit = 0 } = {}) {
  const calls = { checks: [], reports: [] };
  return {
    calls,
    runChecks: async (args) => { calls.checks.push(args); return [{ cmd: args.checks[0], exit, seconds: 0.1, tail: "" }]; },
    reportModelRun: async (payload, opts) => { calls.reports.push({ payload, opts }); return { reported: false, queued: true }; },
  };
}

const readLog = (env) => { try { return fs.readFileSync(hookLogPath(env), "utf8"); } catch { return ""; } };

test("parse: NODE line with pipes in check=, '-' check, CRLF; PROCEDURE alias; STEALTH_RESULT", () => {
  const n3 = findNodeLine(RUN_MD, "N-3");
  assert.equal(n3.check, "node build.js | grep -q ok && test -s out.docx");
  assert.equal(n3.fields.step, "P-1:3");
  assert.equal(n3.fields.deps, "N-2");
  assert.equal(findNodeLine(RUN_MD, "N-4").check, "");
  assert.equal(findNodeLine(RUN_MD, "N-9"), null);
  assert.equal(parseNodeLine("CLAIM|R-001|current"), null);
  assert.equal(procedureFor(PROCEDURES_MD, "P-1"), "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b");
  assert.equal(procedureFor(PROCEDURES_MD, "P-2"), null);
  assert.deepEqual(nodeRefFromPrompt('Do node N-3. Read: rg "N-3" .stealth/run.md, then the claims and step lines it names.'),
    { nodeId: "N-3", nodeLine: null });
  assert.equal(nodeRefFromPrompt("Explore the codebase structure"), null);
  const reply = "done.\nSTEALTH_RESULT node=N-1 cwd=/a\nmore\n`STEALTH_RESULT node=N-3 cwd=/tmp/wt one`\r\n";
  assert.deepEqual(parseStealthResult(reply), { nodeId: "N-3", cwd: "/tmp/wt one" });
  assert.equal(parseStealthResult("Here is my analysis..."), null);
});

test("docs sample payloads: SubagentStart stashes the node, SubagentStop runs its check and queues report_model_run", async () => {
  const repo = planRepo();
  const env = { STEALTHLAB_HOME: tmp() };
  const s = stubs();
  const start = await runSubagentHook("subagent-start", {
    env, stdinText: JSON.stringify(docStart(repo, 'Do node N-3. Read: rg "N-3" .stealth/run.md, then the claims and step lines it names. Reply with: result, proof (diff / command output), anything you learned.')),
  });
  assert.equal(start.status, "stashed");
  const stashed = fs.readdirSync(path.join(env.STEALTHLAB_HOME, "hooks", "subagents"));
  assert.equal(stashed.length, 1);
  assert.doesNotMatch(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "hooks", "subagents", stashed[0]), "utf8"), /Reply with/,
    "only the node id is kept, never the prompt");

  const r = await runSubagentHook("subagent-stop", { env, detach: false, stdinText: JSON.stringify(docStop(repo)), ...s });
  assert.equal(r.status, "reported");
  assert.equal(s.calls.checks.length, 1);
  assert.equal(s.calls.checks[0].cwd, repo);
  assert.deepEqual(s.calls.checks[0].checks, ["node build.js | grep -q ok && test -s out.docx"]);
  assert.ok(s.calls.checks[0].timeoutS < 60);
  assert.equal(Object.keys(s.calls.checks[0].env).some((k) => k.startsWith("STEALTHLAB_")), false, "STEALTHLAB_* scrubbed from the check env");
  const p = s.calls.reports[0].payload;
  assert.equal(p.scaffold, "claude-code-subagent");
  assert.equal(p.model, "claude");
  assert.equal(p.accepted, true);
  assert.equal(p.check_kind, "procedure_check");
  assert.equal(p.procedure_id, "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b");
  assert.equal(p.step_order, 3);
  assert.match(p.instance_key, /^cc-[0-9a-f]{16}$/);
  assert.equal(typeof p.latency_ms, "number");
  assert.equal(s.calls.reports[0].opts.env, env);
  assert.match(readLog(env), /subagent-stop agent=stealth-executor node=N-3 reported accepted=true exit=0 cwd=session/);
  assert.equal(fs.readdirSync(path.join(env.STEALTHLAB_HOME, "hooks", "subagents")).length, 0, "stash consumed");
});

test("failing check -> accepted=false is still reported", async () => {
  const repo = planRepo();
  const env = { STEALTHLAB_HOME: tmp() };
  const s = stubs({ exit: 1 });
  const r = await runSubagentHook("subagent-stop", { env, detach: false, ...s,
    stdinText: JSON.stringify(docStop(repo, `tried\nSTEALTH_RESULT node=N-3 cwd=${repo}`)) });
  assert.equal(r.status, "reported");
  assert.equal(s.calls.reports[0].payload.accepted, false);
  assert.equal(s.calls.reports[0].payload.latency_ms, undefined, "no SubagentStart seen -> no latency");
});

test("claimed cwd is used only when it is a checkout of the same repository", async () => {
  const repo = planRepo();
  const worktree = tmp("slwt-");
  const job = { session_id: "s", agent_id: "a", agent_type: "stealth-executor", cwd: repo, node_id: "N-3", claimed_cwd: worktree };
  let s = stubs();
  let r = await processStopJob(job, { env: {}, ...s, commonDir: () => "/same/.git" });
  assert.equal(r.cwdSource, "subagent");
  assert.equal(s.calls.checks[0].cwd, worktree);
  s = stubs();
  r = await processStopJob(job, { env: {}, ...s, commonDir: (d) => (d === repo ? "/repo/.git" : "/elsewhere/.git") });
  assert.equal(r.cwdSource, "session");
  assert.equal(s.calls.checks[0].cwd, repo);
  s = stubs();
  r = await processStopJob({ ...job, claimed_cwd: path.join(worktree, "gone") }, { env: {}, ...s, commonDir: () => "/same/.git" });
  assert.equal(s.calls.checks[0].cwd, repo, "a removed worktree falls back to the session checkout");
});

test("no check=, unknown node, no procedure: nothing reported, reason logged", async () => {
  const repo = planRepo();
  const env = { STEALTHLAB_HOME: tmp() };
  for (const [node, re] of [["N-4", /has no check=/], ["N-9", /not found in \.stealth\/run\.md/]]) {
    const s = stubs();
    const r = await runSubagentHook("subagent-stop", { env, detach: false, ...s,
      stdinText: JSON.stringify(docStop(repo, `STEALTH_RESULT node=${node}`)) });
    assert.equal(r.status, "skipped");
    assert.match(r.reason, re);
    assert.equal(s.calls.checks.length + s.calls.reports.length, 0);
  }
  fs.rmSync(path.join(repo, ".stealth", "procedures.md"));
  const s = stubs();
  const r = await runSubagentHook("subagent-stop", { env, detach: false, ...s, stdinText: JSON.stringify(docStop(repo, "STEALTH_RESULT node=N-3")) });
  assert.equal(r.status, "checked");
  assert.equal(s.calls.checks.length, 1);
  assert.equal(s.calls.reports.length, 0);
  assert.match(readLog(env), /no procedure_id for step=P-1:3; not reported/);
});

test("ordinary subagent (no NODE) is ignored silently: no check, no report, no log", async () => {
  const env = { STEALTHLAB_HOME: tmp() };
  const s = stubs();
  await runSubagentHook("subagent-start", { env, stdinText: JSON.stringify(docStart(os.tmpdir(), "Explore the codebase structure")) });
  const r = await runSubagentHook("subagent-stop", { env, detach: false, ...s, stdinText: JSON.stringify(docStop(os.tmpdir())) });
  assert.equal(r.status, "skipped");
  assert.equal(s.calls.checks.length, 0);
  assert.equal(readLog(env), "");
});

test("malformed payloads: resolved, never thrown, one log line each", async () => {
  const env = { STEALTHLAB_HOME: tmp() };
  for (const text of ["not json{", "", "[1,2]", "null"]) {
    const r = await runSubagentHook("subagent-stop", { env, stdinText: text });
    assert.equal(r.status, "error");
  }
  const r = await runSubagentHook("bogus-event", { env, stdinText: "{}" });
  assert.equal(r.status, "error");
  const lines = readLog(env).trim().split("\n");
  assert.equal(lines.length, 5);
  assert.match(lines[0], /^\S+Z subagent-stop error: malformed hook payload/);
  assert.match(lines[4], /unknown hook event "bogus-event"/);
});

test("CLI: malformed payload -> exit 0, nothing on stdout, a line in hooks.log", () => {
  const home = tmp();
  for (const ev of ["subagent-stop", "subagent-start"]) {
    const r = spawnSync(process.execPath, [BIN, "hook", ev], {
      input: "{{ not json", encoding: "utf8", timeout: 20000,
      env: { ...process.env, STEALTHLAB_HOME: home, STEALTHLAB_HOOK_WORKER_FILE: "" },
    });
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, "");
  }
  const log = fs.readFileSync(path.join(home, "hooks.log"), "utf8");
  assert.match(log, /subagent-stop error: malformed hook payload/);
  assert.match(log, /subagent-start error: malformed hook payload/);
});

test("hard timeout: a check that never finishes is abandoned and logged", async () => {
  const repo = planRepo();
  const env = { STEALTHLAB_HOME: tmp() };
  const t0 = Date.now();
  const r = await runSubagentHook("subagent-stop", { env, detach: false, timeoutMs: 100,
    runChecks: () => new Promise(() => {}), reportModelRun: async () => assert.fail("must not report"),
    stdinText: JSON.stringify(docStop(repo, "STEALTH_RESULT node=N-3")) });
  assert.equal(r.status, "timeout");
  assert.ok(Date.now() - t0 < 5000);
  assert.match(readLog(env), /node=N-3 timeout hard timeout 100 ms/);
});

test("detached path: the hook hands a minimal job to a worker (no assistant text) and the worker consumes it", async () => {
  const repo = planRepo();
  const env = { STEALTHLAB_HOME: tmp() };
  const spawned = [];
  const secretish = `Result: done. token sk-test-${"X".repeat(24)}\nSTEALTH_RESULT node=N-3 cwd=${repo}`;
  const r = await runSubagentHook("subagent-stop", { env, stdinText: JSON.stringify(docStop(repo, secretish)),
    spawnWorker: (file, e) => spawned.push({ file, e }) });
  assert.equal(r.status, "detached");
  assert.equal(spawned.length, 1);
  const jobText = fs.readFileSync(spawned[0].file, "utf8");
  assert.doesNotMatch(jobText, /sk-test|Result: done|transcript/);
  assert.deepEqual(Object.keys(JSON.parse(jobText)).sort(),
    ["agent_id", "agent_type", "claimed_cwd", "cwd", "node_id", "node_line", "session_id", "started_at"]);
  const s = stubs();
  const w = await runStopWorker(spawned[0].file, { env, ...s, commonDir: () => "/same/.git" });
  assert.equal(w.status, "reported");
  assert.equal(fs.existsSync(spawned[0].file), false, "job file deleted");
});

test("buildStopJob: stashed NODE line is used only for the same node", () => {
  const env = { STEALTHLAB_HOME: tmp() };
  const dir = path.join(env.STEALTHLAB_HOME, "hooks", "subagents");
  fs.mkdirSync(dir, { recursive: true });
  const line = "NODE|N-7|ready|x|step=P-1:1|claims=-|deps=-|check=true";
  fs.writeFileSync(path.join(dir, "agent-uuid-123.json"), JSON.stringify({ node_id: "N-7", node_line: line, started_at: 1 }));
  const job = buildStopJob(docStop("/r", "STEALTH_RESULT node=N-7"), { env });
  assert.equal(job.node_line, line);
  fs.writeFileSync(path.join(dir, "agent-uuid-123.json"), JSON.stringify({ node_id: "N-7", node_line: line, started_at: 1 }));
  assert.equal(buildStopJob(docStop("/r", "STEALTH_RESULT node=N-8"), { env }).node_line, null);
});

test("Windows paths: claimed cwd with spaces and backslashes, CRLF reply", { skip: process.platform !== "win32" && "Windows-only: backslash drive paths do not exist on this OS" }, async () => {
  const repo = planRepo(tmp("sl repo "));
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), "sl wt "));
  assert.match(wt, /^[A-Za-z]:\\/);
  const reply = `done\r\nSTEALTH_RESULT node=N-3 cwd=${wt}\r\n`;
  assert.equal(parseStealthResult(reply).cwd, wt);
  const s = stubs();
  const r = await runSubagentHook("subagent-stop", { env: { STEALTHLAB_HOME: tmp() }, detach: false, ...s,
    commonDir: () => "c:\\same\\.git", stdinText: JSON.stringify(docStop(repo, reply)) });
  assert.equal(r.status, "reported");
  assert.equal(s.calls.checks[0].cwd, wt);
});

test("POSIX paths: claimed cwd with spaces", { skip: process.platform === "win32" && "POSIX-only: covered by the Windows path test on this OS" }, async () => {
  const repo = planRepo(tmp("sl repo "));
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), "sl wt "));
  assert.ok(wt.startsWith("/"));
  const s = stubs();
  const r = await runSubagentHook("subagent-stop", { env: { STEALTHLAB_HOME: tmp() }, detach: false, ...s,
    commonDir: () => "/same/.git", stdinText: JSON.stringify(docStop(repo, `done\nSTEALTH_RESULT node=N-3 cwd=${wt}\n`)) });
  assert.equal(r.status, "reported");
  assert.equal(s.calls.checks[0].cwd, wt);
});

test("gitCommonDir: a real worktree and its main checkout share one common dir", { skip: spawnSync("git", ["--version"]).status !== 0 && "git not on PATH" }, async () => {
  const { gitCommonDir } = await import("../lib/subagent_hook.mjs");
  const repo = tmp("slgit-");
  const g = (...a) => spawnSync("git", ["-C", repo, ...a], { encoding: "utf8" });
  g("init", "-q");
  g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x");
  const wt = path.join(tmp("slgitwt-"), "wt");
  assert.equal(g("worktree", "add", "-q", "--detach", wt).status, 0);
  assert.ok(gitCommonDir(repo));
  assert.equal(gitCommonDir(wt), gitCommonDir(repo));
  assert.equal(gitCommonDir(tmp("slnogit-")), null);
  g("worktree", "remove", "--force", wt);
});

const HAVE_EXEC = fs.existsSync(path.join(HERE, "..", "lib", "exec", "verify.mjs")) && fs.existsSync(path.join(HERE, "..", "lib", "exec", "evidence.mjs"));

test("integration with lib/exec (real runChecks + reportModelRun): the check really runs, the report lands in the outbox", {
  skip: !HAVE_EXEC && "lib/exec/{verify,evidence}.mjs not present yet (Builder A)",
}, async () => {
  const repo = tmp();
  fs.mkdirSync(path.join(repo, ".stealth"));
  fs.writeFileSync(path.join(repo, ".stealth", "run.md"),
    'NODE|N-1|ready|make it|step=P-1:1|claims=-|deps=-|check=node -e "process.exit(require(\'fs\').existsSync(\'made.txt\') ? 0 : 3)"\n');
  fs.writeFileSync(path.join(repo, ".stealth", "procedures.md"), PROCEDURES_MD);
  // Not logged in and no URL: nothing can leave the machine, so the report must be queued.
  const env = { STEALTHLAB_HOME: tmp(), PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, ComSpec: process.env.ComSpec };
  let r = await runSubagentHook("subagent-stop", { env, detach: false, stdinText: JSON.stringify(docStop(repo, "STEALTH_RESULT node=N-1")) });
  assert.equal(r.status, "reported", JSON.stringify(r));
  assert.equal(r.accepted, false);
  assert.equal(r.exits, "3");
  fs.writeFileSync(path.join(repo, "made.txt"), "x");
  r = await runSubagentHook("subagent-stop", { env, detach: false, stdinText: JSON.stringify(docStop(repo, "STEALTH_RESULT node=N-1")) });
  assert.equal(r.accepted, true);
  assert.equal(r.evidence.queued, true);
  const outbox = path.join(env.STEALTHLAB_HOME, "outbox");
  const entries = fs.readdirSync(outbox).filter((f) => f.endsWith(".json")).map((f) => JSON.parse(fs.readFileSync(path.join(outbox, f), "utf8")));
  assert.equal(entries.length, 2);
  for (const e of entries) {
    assert.equal(e.tool, "report_model_run");
    assert.equal(e.args.scaffold, "claude-code-subagent");
    assert.equal(e.args.check_kind, "procedure_check");
    assert.equal(e.args.procedure_id, "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b");
  }
  assert.deepEqual(entries.map((e) => e.args.accepted).sort(), [false, true]);
  assert.match(readLog(env), /node=N-1 reported accepted=true exit=0 cwd=session evidence=queued/);
});

test("CLI detached worker: the hook exits at once, the worker runs the check and logs the outcome", {
  skip: !HAVE_EXEC && "lib/exec/{verify,evidence}.mjs not present yet (Builder A)",
}, async () => {
  const repo = tmp();
  fs.mkdirSync(path.join(repo, ".stealth"));
  fs.writeFileSync(path.join(repo, ".stealth", "run.md"), 'NODE|N-1|ready|x|step=P-1:1|claims=-|deps=-|check=node -e "process.exit(0)"\n');
  fs.writeFileSync(path.join(repo, ".stealth", "procedures.md"), PROCEDURES_MD);
  const home = tmp();
  const env = { ...process.env, STEALTHLAB_HOME: home, STEALTHLAB_MCP_URL: "", STEALTHLAB_TOKEN: "", STEALTHLAB_HOOK_WORKER_FILE: "", STEALTHLAB_HOOK_SYNC: "" };
  const t0 = Date.now();
  const r = spawnSync(process.execPath, [BIN, "hook", "subagent-stop"], {
    input: JSON.stringify(docStop(repo, "STEALTH_RESULT node=N-1")), encoding: "utf8", timeout: 20000, env,
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, "");
  assert.ok(Date.now() - t0 < 10000, "the hook itself does not wait for the check");
  const log = path.join(home, "hooks.log");
  let text = "";
  for (let i = 0; i < 150 && !/node=N-1/.test(text); i++) {
    await new Promise((res) => setTimeout(res, 100));
    try { text = fs.readFileSync(log, "utf8"); } catch { /* not yet */ }
  }
  assert.match(text, /subagent-stop agent=stealth-executor node=N-1 reported accepted=true exit=0/);
  assert.deepEqual(fs.readdirSync(path.join(home, "hooks", "jobs")), [], "job file consumed");
});
