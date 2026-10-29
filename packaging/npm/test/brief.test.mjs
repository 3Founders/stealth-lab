import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { BRIEF_SYSTEM, conversationPrompt, makeBrief, parseBrief, parseOverride, recentTurns, turnsText } from "../lib/brief.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { runPromptHook } from "../lib/hook.mjs";
import { LEGACY_POLICY } from "./helpers/legacy_policy.mjs";

// the cheap model's task ends with the testing guidance (dispatch.testingNote); these tests compare what comes before it
const withoutTesting = (t) => String(t).replace(/\n\nTesting: [^\n]*$/, "");

// fetch stubs open no socket, and a request's timeout (AbortSignal.timeout) does not keep the event loop alive by
// design: without this, node ends the file at the first stub that waits for its timeout and cancels every test after
// it (19 tests never ran). A real request's socket keeps the loop alive, so the product needs nothing of this.
const keepAlive = setInterval(() => {}, 1 << 30);
after(() => clearInterval(keepAlive));

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));

const REPLY = (o) => JSON.stringify({ task: "Fix calc.py so x is 2", files_hinted: ["calc.py"], acceptance: "pytest passes", parts: [],
                                      clarity: "clear", kind: "code", size: "small", ...o });

// ---- parsing ---------------------------------------------------------------------------------------------------------

test("parseBrief: a model reply becomes a checked brief; a fence, extra text or a bad value is handled", () => {
  const b = parseBrief("Here you go:\n```json\n" + REPLY({}) + "\n```");
  assert.deepEqual(b, { task: "Fix calc.py so x is 2", files_hinted: ["calc.py"], acceptance: "pytest passes", parts: [],
                        clarity: "clear", kind: "code", size: "small", search_terms: [] });
  assert.equal(parseBrief(REPLY({ kind: "banana" })), null);
  assert.equal(parseBrief(REPLY({ size: "huge" })), null);
  assert.equal(parseBrief(REPLY({ task: "  " })), null);
  assert.equal(parseBrief("no json at all"), null);
  assert.equal(parseBrief("{not json}"), null);
  assert.equal(parseBrief(null), null);
  assert.equal(parseBrief(REPLY({ files_hinted: Array.from({ length: 20 }, (_, i) => `f${i}`) })).files_hinted.length, 8);
});

// ---- the gate ----------------------------------------------------------------------------------------------------------

test("there are no word rules: a bug report that starts like a question goes when the server's judge says change", async () => {
  const { env, repo } = setup({ STEALTHLAB_DISPATCH_GATE: "on" });
  const rt = stubRuntime(repo);
  // sqlglot-4051's opener, ruled a question by the old word list ("When ..."), ct-series-v5
  const prompt = "When transpiling from Presto to Spark, regexp_extract comes out without a group argument, but Spark's default is 1 while Presto's is 0.";
  const d = await dispatch({ payload: { cwd: repo, prompt }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, briefImpl: brief({}),
                             triage: { work: "change" } });
  assert.equal(d.handled, true);
});

test("turnsText: the earlier turns for the judge, oldest cut first", () => {
  assert.equal(turnsText([{ role: "user", text: "a" }, { role: "assistant", text: "b" }]), "Developer: a\n\nAssistant: b");
  assert.equal(turnsText([{ role: "user", text: "x".repeat(5000) }], 100).length, 100);
});

test("the brief prompt asks for exactly the fields the code reads", () => {
  for (const f of ["task", "files_hinted", "acceptance", "parts", "clarity", "kind", "size", "search_terms"]) assert.ok(BRIEF_SYSTEM.includes(f), f);
});

// ---- the earlier turns -------------------------------------------------------------------------------------------------

function transcript(lines) {
  const f = path.join(tmp("tr-"), "t.jsonl");
  fs.writeFileSync(f, lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
  return f;
}
const U = (text) => ({ type: "user", message: { role: "user", content: text } });
const A = (text) => ({ type: "assistant", message: { role: "assistant", model: "claude-opus-5-5", content: [{ type: "text", text }, { type: "tool_use", name: "Bash", input: {} }] } });

test("recentTurns: the last 6 text turns, oldest first, no tool traffic, the current prompt dropped, long ones shortened", () => {
  const lines = [];
  for (let i = 1; i <= 5; i++) { lines.push(U(`question ${i}`)); lines.push(A(`answer ${i}`)); }
  lines.push({ type: "user", message: { role: "user", content: [{ type: "tool_result", content: "ls output" }] } });
  lines.push({ type: "user", isMeta: true, message: { role: "user", content: "meta" } });
  lines.push(U("fix that bug please"));                                     // the prompt now being handled
  const turns = recentTurns(transcript(lines), { current: "fix that bug please" });
  assert.deepEqual(turns.map((t) => t.text), ["question 3", "answer 3", "question 4", "answer 4", "question 5", "answer 5"]);
  assert.equal(recentTurns(transcript([U("x".repeat(5000))]))[0].text.length, 700);
  assert.deepEqual(recentTurns(path.join(os.tmpdir(), "nope-xyz.jsonl")), []);
});

// ---- the call ---------------------------------------------------------------------------------------------------------

const PROFILES = { profiles: { "gpt-oss-120b": { name: "gpt-oss-120b", model_id: "gpt-oss-120b", key_env: "GC_KEY", openai_base_url: "https://gc.example/v1",
                                                  input_per_mtok: 0.1, output_per_mtok: 0.4, request_extras: {} } } };
const cfg = () => PROFILES;
const okFetch = (content, calls = []) => async (url, init) => {
  calls.push({ url, body: JSON.parse(init.body), headers: init.headers });
  return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content } }], usage: { prompt_tokens: 1000, completion_tokens: 100 } }) };
};

test("makeBrief: asks the profile's endpoint with the earlier turns, parses the reply, and writes its cost to the ledger", async () => {
  const home = tmp("brief-home-");
  const env = { STEALTHLAB_HOME: home, GC_KEY: "k-test", STEALTHLAB_BRIEF_MODEL: "gpt-oss-120b" };
  const calls = [];
  const tr = transcript([U("calc.py has a bug in add"), A("I see it, it adds 1 too many")]);
  const b = await makeBrief({ prompt: "yes fix it", transcriptPath: tr, env, fetchImpl: okFetch(REPLY({}), calls), readConfig: cfg });
  assert.equal(b.kind, "code");
  assert.equal(calls[0].url, "https://gc.example/v1/chat/completions");
  assert.equal(calls[0].body.model, "gpt-oss-120b");
  assert.equal(calls[0].body.temperature, 0);
  assert.equal(calls[0].headers.authorization, "Bearer k-test");
  const user = calls[0].body.messages.find((m) => m.role === "user").content;
  assert.match(user, /Developer: calc\.py has a bug in add/);
  assert.match(user, /Assistant: I see it, it adds 1 too many/);
  assert.match(user, /Latest message:\nyes fix it/);
  const row = JSON.parse(fs.readFileSync(path.join(home, "dispatch_costs.jsonl"), "utf8").trim());
  assert.equal(row.what, "brief");
  assert.ok(Math.abs(row.cost_usd - 0.00014) < 1e-9);
});

test("makeBrief: no profile, HTTP error, a reply that is not a brief, a timeout and the off switch all give null", async () => {
  const env = { STEALTHLAB_HOME: tmp("brief-home-"), GC_KEY: "k", STEALTHLAB_BRIEF_MODEL: "gpt-oss-120b" };
  const logs = [];
  const call = (o) => makeBrief({ prompt: "fix calc.py", env, fetchImpl: okFetch(REPLY({})), readConfig: cfg, log: (m) => logs.push(m), ...o });
  assert.equal(await call({ readConfig: () => ({ profiles: {} }) }), null);
  assert.equal(await call({ fetchImpl: async () => ({ ok: false, status: 503 }) }), null);
  assert.equal(await call({ fetchImpl: okFetch("sorry, I cannot") }), null);
  assert.equal(await call({ fetchImpl: async (u, init) => { await new Promise((r) => init.signal.addEventListener("abort", r)); throw Object.assign(new Error("t"), { name: "TimeoutError" }); },
                            env: { ...env, STEALTHLAB_BRIEF_TIMEOUT_MS: "500" } }), null);
  assert.equal(await call({ env: { ...env, STEALTHLAB_BRIEF: "off" } }), null);
  assert.ok(logs.some((m) => /timed out/.test(m)));
});

test("makeBrief: an endpoint that rejects a system turn gets it inside the user turn", async () => {
  const calls = [];
  const noSys = { profiles: { "gpt-oss-120b": { ...PROFILES.profiles["gpt-oss-120b"], no_system_role: true } } };
  await makeBrief({ prompt: "fix calc.py", env: { STEALTHLAB_HOME: tmp("h-"), GC_KEY: "k", STEALTHLAB_BRIEF_MODEL: "gpt-oss-120b" }, fetchImpl: okFetch(REPLY({}), calls), readConfig: () => noSys });
  assert.deepEqual(calls[0].body.messages.map((m) => m.role), ["user"]);
  assert.ok(calls[0].body.messages[0].content.startsWith(BRIEF_SYSTEM));
});

// ---- dispatch uses it ------------------------------------------------------------------------------------------------

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";

function setup(extra = {}) {
  const home = tmp("disp-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const repo = tmp("disp-repo-");
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "x = 1\n");
  fs.mkdirSync(path.join(repo, ".kell"));
  fs.writeFileSync(path.join(repo, ".kell", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, ...LEGACY_POLICY, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                  STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_DISPATCH_LOCATE: "off", STEALTHLAB_BRIEF: "on", STEALTHLAB_DISPATCH_GOALFILE: "off", ...extra }, repo };
}
function stubRuntime(repo) {
  const calls = [];
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult() { return { state: "verified", cost_usd: 0.001, checks: [{ tail: "ok" }] }; },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; } };
}
const brief = (o) => async () => parseBrief(REPLY(o));

test("dispatch: a clear code task runs, and the cheap model is told the conversation and the request as said, not the brief", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const tr = transcript([U("calc.py has a bug: x should be 2"), A("I see it."), U("yes fix it")]);
  const d = await dispatch({ payload: { cwd: repo, prompt: "yes fix it", transcript_path: tr }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                             briefImpl: brief({ task: "ALWAYS do something the user never said" }) });
  assert.equal(d.handled, true);
  assert.equal(withoutTesting(rt.calls[0].task), "Earlier in this conversation:\nDeveloper: calc.py has a bug: x should be 2\n\nAssistant: I see it.\n\nThe request:\nyes fix it");
  assert.doesNotMatch(rt.calls[0].task, /ALWAYS/);
  assert.match(fs.readFileSync(path.join(repo, ".kell", "library.md"), "utf8"), /yes fix it/, "the library title stays the user's own words");
});

test("dispatch: the brief decides nothing -- a brief that says question, or no brief, still dispatches what the judge calls a change", async () => {
  for (const b of [brief({ kind: "question" }), brief({ clarity: "unclear" }), async () => null]) {
    const { env, repo } = setup({ STEALTHLAB_DISPATCH_GATE: "on" });
    const rt = stubRuntime(repo);
    const d = await dispatch({ payload: { cwd: repo, prompt: "fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                               briefImpl: b, triage: { work: "change" } });
    assert.equal(d.handled, true);
  }
});

test("dispatch: a large task goes to the cheap models like any other", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, briefImpl: brief({ size: "large" }) });
  assert.equal(d.handled, true);
});

test("dispatch: !cheap (force) skips the gate; STEALTHLAB_BRIEF=off is the old behaviour", async () => {
  const a = setup();
  const rtA = stubRuntime(a.repo);
  const forced = await dispatch({ payload: { cwd: a.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: a.repo, mine: MINE, env: a.env, runtime: rtA,
                                  briefImpl: brief({ clarity: "unclear" }), force: true });
  assert.equal(forced.handled, true);
  const b = setup({ STEALTHLAB_BRIEF: "off" });
  const rtB = stubRuntime(b.repo);
  const off = await dispatch({ payload: { cwd: b.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: b.repo, mine: MINE, env: b.env, runtime: rtB,
                              briefImpl: async () => assert.fail("no brief when it is off") });
  assert.equal(off.handled, true);
  assert.equal(withoutTesting(rtB.calls[0].task), "Fix calc.py so x is 2");
  assert.match(rtB.calls[0].task, /\n\nTesting: run /, "the testing guidance follows the request");
});

test("dispatch: !cheap tries the cheap models even when the plan puts the session's model first", async () => {
  const first = { ...PLAN, ladder: ["claude-opus-5-5|claude-code", "gemma-4-31b-it|stealth"] };
  const a = setup({ STEALTHLAB_DISPATCH_EXPLORE: "0" });
  const rtA = stubRuntime(a.repo);
  assert.equal(await dispatch({ payload: { cwd: a.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: first }, root: a.repo, mine: MINE, env: a.env, runtime: rtA,
                               briefImpl: brief({}) }), null);
  const b = setup({ STEALTHLAB_DISPATCH_EXPLORE: "0" });
  const rtB = stubRuntime(b.repo);
  const d = await dispatch({ payload: { cwd: b.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: first }, root: b.repo, mine: MINE, env: b.env, runtime: rtB,
                             briefImpl: async () => null, force: true });
  assert.equal(d.handled, true);
});

test("test-first writer is told what the user said too", async () => {
  const { env, repo } = setup({ STEALTHLAB_DISPATCH_TESTFIRST: "on" });
  const rt = stubRuntime(repo);
  const seen = [];
  await dispatch({ payload: { cwd: repo, prompt: "yes fix it" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                   runClaude: async (args, input) => { seen.push(String(input)); return { status: 0, stdout: "SKIP" }; },
                   briefImpl: brief({ task: "a rewording" }) });
  assert.ok(seen.length, "the test writer ran");
  assert.ok(seen.every((s) => /yes fix it/.test(s) && !/a rewording/.test(s)));
});

// ---- overrides ---------------------------------------------------------------------------------------------------------

test("parseOverride: !opus and !cheap at the start only; the prefix is removed; the switch turns it off", () => {
  assert.deepEqual(parseOverride("!opus  rewrite the parser", {}), { kind: "opus", prompt: "rewrite the parser" });
  assert.deepEqual(parseOverride("  !CHEAP fix the typo", {}), { kind: "cheap", prompt: "fix the typo" });
  assert.deepEqual(parseOverride("please use !opus for this", {}), { kind: null, prompt: "please use !opus for this" });
  assert.deepEqual(parseOverride("!opusx hi", {}), { kind: null, prompt: "!opusx hi" });
  assert.deepEqual(parseOverride("!opus hi", { STEALTHLAB_OVERRIDES: "off" }), { kind: null, prompt: "!opus hi" });
});

test("hook: !opus skips kell entirely (no lookup, no dispatch) and tells the session to ignore the prefix", async () => {
  const out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "!opus rewrite the whole parser to be incremental", cwd: os.tmpdir(), session_id: "s1" }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: { STEALTHLAB_HOME: tmp("h-") },
    fetchImpl: async () => assert.fail("no network call for !opus"), write: (s) => out.push(JSON.parse(s)), log: () => {},
    dispatchImpl: async () => assert.fail("no dispatch for !opus") });
  assert.equal(out.length, 1);
  assert.match(out[0].hookSpecificOutput.additionalContext, /"!opus" is an override/);
});

test("hook: !cheap forces the dispatch and gives it the prompt without the prefix", async () => {
  const seen = [];
  const reply = { outcome: "no_match", model_plan: PLAN };
  const fetchImpl = async (url, init) => {
    const body = init.body ? JSON.parse(init.body) : {};
    const headers = new Map([["mcp-session-id", "S1"], ["content-type", "text/event-stream"]]);
    const h = { get: (k) => headers.get(k) };
    if (body.method === "initialize") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} })}\n\n` };
    if (body.method === "tools/call") return { ok: true, status: 200, headers: h, text: async () => `data: ${JSON.stringify({ jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: JSON.stringify(reply) }] } })}\n\n` };
    return { ok: true, status: 202, headers: h, text: async () => "" };
  };
  const out = [];
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "!cheap fix the typo in the readme heading please", cwd: os.tmpdir(), session_id: "s1" }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: { STEALTHLAB_HOME: tmp("h-") }, fetchImpl, write: (s) => out.push(JSON.parse(s)), log: () => {},
    dispatchImpl: async (a) => { seen.push({ prompt: a.payload.prompt, force: a.force }); return { handled: true, text: "Done by gemma", model: "gemma", files: [] }; } });
  assert.deepEqual(seen, [{ prompt: "fix the typo in the readme heading please", force: true }]);
  assert.equal(out[0].decision, "block");
});

// ---- the request as said, within the task limit -----------------------------------------------------------------------

test("conversationPrompt: earlier turns, then the request word for word; the turns give way first, the request is whole", () => {
  assert.equal(conversationPrompt([], "fix it"), "fix it");
  const turns = [{ role: "user", text: "u".repeat(3000) }, { role: "assistant", text: "a".repeat(3000) }];
  const p = conversationPrompt(turns, "fix it please", 1000);
  assert.ok(p.length <= 1000, String(p.length));
  assert.ok(p.endsWith("The request:\nfix it please"));
  assert.match(p, /^Earlier in this conversation:\n\.\.\.a+/, "the oldest is cut first");
  const long = "x".repeat(5000);
  assert.equal(conversationPrompt(turns, long, 1000).length, 1000);
});

test("dispatch: a long request still fits the task limit", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const raw = `yes fix it ${"and also keep the old behaviour ".repeat(1400)}`;
  await dispatch({ payload: { cwd: repo, prompt: raw }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, briefImpl: brief({}) });
  const task = rt.calls[0].task;
  assert.ok(task.length <= 40000, String(task.length));
  assert.match(task, /yes fix it and also keep/);
});

test("the brief sees the start of files the message names, only inside the project", async () => {
  const { namedFiles } = await import("../lib/brief.mjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "named-"));
  fs.writeFileSync(path.join(dir, "TASK.md"), "Full task: a valid file name has at most three digits.");
  fs.writeFileSync(path.join(path.dirname(dir), "secret.txt"), "outside");
  const got = namedFiles("A name is conside -- Implement it in solution.js (the task is in TASK.md). See ../secret.txt and `missing.md`.", dir);
  assert.deepEqual(got.map((f) => f.name), ["TASK.md"]);
  assert.match(got[0].text, /at most three digits/);
  assert.deepEqual(namedFiles("fix TASK.md", null), []);
});

test("a chat or question brief with no task is still a brief (and is gated out); a code brief needs a task", () => {
  const chat = parseBrief('{"task":"","files_hinted":[],"acceptance":"","parts":[],"clarity":"clear","kind":"chat","size":"small"}');
  assert.equal(chat.kind, "chat");
  assert.equal(parseBrief('{"task":"","files_hinted":[],"acceptance":"","parts":[],"clarity":"clear","kind":"code","size":"small"}'), null);
});
