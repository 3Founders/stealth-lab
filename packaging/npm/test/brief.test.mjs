import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { BRIEF_SYSTEM, briefGate, briefText, guardKind, makeBrief, parseBrief, parseOverride, recentTurns } from "../lib/brief.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { runPromptHook } from "../lib/hook.mjs";

const FIXTURE = JSON.parse(fs.readFileSync(new URL("./fixtures/brief_prompts.json", import.meta.url), "utf8")).prompts;
const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));

const REPLY = (o) => JSON.stringify({ task: "Fix calc.py so x is 2", files_hinted: ["calc.py"], acceptance: "pytest passes", parts: [],
                                      clarity: "clear", kind: "code", size: "small", ...o });

// ---- parsing ---------------------------------------------------------------------------------------------------------

test("parseBrief: a model reply becomes a checked brief; a fence, extra text or a bad value is handled", () => {
  const b = parseBrief("Here you go:\n```json\n" + REPLY({}) + "\n```");
  assert.deepEqual(b, { task: "Fix calc.py so x is 2", files_hinted: ["calc.py"], acceptance: "pytest passes", parts: [],
                        clarity: "clear", kind: "code", size: "small" });
  assert.equal(parseBrief(REPLY({ kind: "banana" })), null);
  assert.equal(parseBrief(REPLY({ size: "huge" })), null);
  assert.equal(parseBrief(REPLY({ task: "  " })), null);
  assert.equal(parseBrief("no json at all"), null);
  assert.equal(parseBrief("{not json}"), null);
  assert.equal(parseBrief(null), null);
  assert.equal(parseBrief(REPLY({ files_hinted: Array.from({ length: 20 }, (_, i) => `f${i}`) })).files_hinted.length, 8);
});

test("briefText: the task, the parts when there are several, the files and the acceptance", () => {
  assert.equal(briefText(parseBrief(REPLY({}))), "Fix calc.py so x is 2\n\nFiles likely involved: calc.py\n\nDone when: pytest passes");
  const t = briefText(parseBrief(REPLY({ parts: ["rename f", "add a test"], files_hinted: [], acceptance: "" })));
  assert.match(t, /This is several things; do all of them:\n1\. rename f\n2\. add a test/);
  assert.doesNotMatch(t, /Files likely|Done when/);
});

// ---- the gate ----------------------------------------------------------------------------------------------------------

test("gate: only a clear, small or medium code task goes; no brief means today's behaviour", () => {
  const ok = (o) => briefGate(parseBrief(REPLY(o)), "fix calc.py so x is 2").ok;
  assert.equal(ok({}), true);
  assert.equal(ok({ size: "medium" }), true);
  assert.equal(ok({ size: "large" }), false);
  assert.equal(ok({ clarity: "unclear" }), false);
  assert.equal(ok({ kind: "question" }), false);
  assert.equal(ok({ kind: "chat" }), false);
  assert.equal(briefGate(null, "fix calc.py so x is 2").ok, true);
});

test("guard: it never says non-code for a code prompt in the fixture, and catches the plain questions and pleasantries", () => {
  const code = FIXTURE.filter((p) => p.kind === "code");
  for (const p of code) assert.equal(guardKind(p.prompt), null, `a code prompt was ruled out: ${p.prompt}`);
  const others = FIXTURE.filter((p) => p.kind !== "code");
  const caught = others.filter((p) => guardKind(p.prompt) === p.kind);
  assert.ok(others.every((p) => guardKind(p.prompt) === null || guardKind(p.prompt) !== "code"));
  assert.ok(caught.length >= 18, `the guard catches ${caught.length} of ${others.length} questions and chat`);
});

test("pipeline on the fixture with a model that is wrong in the worst way (everything is a clear small code task)", () => {
  const worst = parseBrief(REPLY({}));
  const dispatched = FIXTURE.filter((p) => briefGate(worst, p.prompt).ok);
  const wronglyDispatched = dispatched.filter((p) => p.kind !== "code");
  // what the guard cannot catch is what the live model must: listed here so the number is visible, not asserted away
  assert.deepEqual(wronglyDispatched.map((p) => p.prompt).sort(), FIXTURE.filter((p) => p.kind !== "code" && guardKind(p.prompt) === null).map((p) => p.prompt).sort());
  assert.ok(wronglyDispatched.length <= 6, `${wronglyDispatched.length} of ${FIXTURE.length - 12} non-code prompts rely on the model alone`);
});

test("the brief prompt asks for exactly the fields the code reads", () => {
  for (const f of ["task", "files_hinted", "acceptance", "parts", "clarity", "kind", "size"]) assert.ok(BRIEF_SYSTEM.includes(f), f);
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
  const env = { STEALTHLAB_HOME: home, GC_KEY: "k-test" };
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
  const env = { STEALTHLAB_HOME: tmp("brief-home-"), GC_KEY: "k" };
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
  await makeBrief({ prompt: "fix calc.py", env: { STEALTHLAB_HOME: tmp("h-"), GC_KEY: "k" }, fetchImpl: okFetch(REPLY({}), calls), readConfig: () => noSys });
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
  fs.mkdirSync(path.join(repo, ".stealth"));
  fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                  STEALTHLAB_DISPATCH_CROSSCHECK: "off", ...extra }, repo };
}
function stubRuntime(repo) {
  const calls = [];
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult() { return { state: "verified", cost_usd: 0.001, checks: [{ tail: "ok" }] }; },
    async applyRun() { fs.writeFileSync(path.join(repo, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; } };
}
const brief = (o) => async () => parseBrief(REPLY(o));

test("dispatch: a clear code task runs, and the cheap model gets the BRIEF, not the raw prompt", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "yes fix it" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                             briefImpl: brief({ task: "Fix calc.py so x is 2 (the bug discussed above)" }) });
  assert.equal(d.handled, true);
  assert.match(rt.calls[0].task, /^Fix calc\.py so x is 2 \(the bug discussed above\)/);
  assert.doesNotMatch(rt.calls[0].task, /yes fix it/);
  assert.match(fs.readFileSync(path.join(repo, ".stealth", "library.md"), "utf8"), /yes fix it/, "the library title stays the user's own words");
});

test("dispatch: a question, an unclear request or a large change is not dispatched; nothing runs", async () => {
  for (const o of [{ kind: "question" }, { kind: "chat" }, { clarity: "unclear" }, { size: "large" }]) {
    const { env, repo } = setup();
    const rt = stubRuntime(repo);
    const d = await dispatch({ payload: { cwd: repo, prompt: "fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, briefImpl: brief(o) });
    assert.equal(d, null, JSON.stringify(o));
    assert.equal(rt.calls.length, 0);
  }
});

test("dispatch: a plain question is never dispatched even when no brief could be made (the guard)", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "why is the build failing on windows only" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                             briefImpl: async () => null });
  assert.equal(d, null);
  assert.equal(rt.calls.length, 0);
});

test("dispatch: no brief (timeout, no profile) falls back to the raw prompt, as before", async () => {
  const { env, repo } = setup();
  const rt = stubRuntime(repo);
  const d = await dispatch({ payload: { cwd: repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt, briefImpl: async () => null });
  assert.equal(d.handled, true);
  assert.equal(rt.calls[0].task, "Fix calc.py so x is 2");
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
  assert.equal(rtB.calls[0].task, "Fix calc.py so x is 2");
});

test("dispatch: !cheap tries the cheap models even when the plan puts the session's model first", async () => {
  const first = { ...PLAN, ladder: ["claude-opus-5-5|claude-code", "gemma-4-31b-it|stealth"] };
  const a = setup({ STEALTHLAB_DISPATCH_EXPLORE: "0" });
  const rtA = stubRuntime(a.repo);
  assert.equal(await dispatch({ payload: { cwd: a.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: first }, root: a.repo, mine: MINE, env: a.env, runtime: rtA,
                               briefImpl: async () => null }), null);
  const b = setup({ STEALTHLAB_DISPATCH_EXPLORE: "0" });
  const rtB = stubRuntime(b.repo);
  const d = await dispatch({ payload: { cwd: b.repo, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: first }, root: b.repo, mine: MINE, env: b.env, runtime: rtB,
                             briefImpl: async () => null, force: true });
  assert.equal(d.handled, true);
});

test("test-first writer is told the brief too", async () => {
  const { env, repo } = setup({ STEALTHLAB_DISPATCH_TESTFIRST: "on" });
  const rt = stubRuntime(repo);
  const seen = [];
  await dispatch({ payload: { cwd: repo, prompt: "yes fix it" }, reply: { model_plan: PLAN }, root: repo, mine: MINE, env, runtime: rt,
                   runClaude: async (args, input) => { seen.push(String(input)); return { status: 0, stdout: "SKIP" }; },
                   briefImpl: brief({ task: "Fix calc.py so x is 2 (the bug discussed above)" }) });
  assert.ok(seen.length, "the test writer ran");
  assert.ok(seen.every((s) => s.includes("Fix calc.py so x is 2 (the bug discussed above)") && !s.includes("yes fix it")));
});

// ---- overrides ---------------------------------------------------------------------------------------------------------

test("parseOverride: !opus and !cheap at the start only; the prefix is removed; the switch turns it off", () => {
  assert.deepEqual(parseOverride("!opus  rewrite the parser", {}), { kind: "opus", prompt: "rewrite the parser" });
  assert.deepEqual(parseOverride("  !CHEAP fix the typo", {}), { kind: "cheap", prompt: "fix the typo" });
  assert.deepEqual(parseOverride("please use !opus for this", {}), { kind: null, prompt: "please use !opus for this" });
  assert.deepEqual(parseOverride("!opusx hi", {}), { kind: null, prompt: "!opusx hi" });
  assert.deepEqual(parseOverride("!opus hi", { STEALTHLAB_OVERRIDES: "off" }), { kind: null, prompt: "!opus hi" });
});

test("hook: !opus skips StealthLab entirely (no lookup, no dispatch) and tells the session to ignore the prefix", async () => {
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
