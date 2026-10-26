// A cheap model answers a question or a conversational turn (lib/answer.mjs): from the session's notes, checked by a
// reviewer, delivered by the hook before the session's model runs; a reply not called good is never shown.
import { after, test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { answerTurn, sessionNotes } from "../lib/answer.mjs";
import { dispatchedTurns, pendingLines } from "../lib/continuity.mjs";
import { runPromptHook } from "../lib/hook.mjs";

const keepAlive = setInterval(() => {}, 1 << 30);          // fetch stubs open no socket
after(() => clearInterval(keepAlive));

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));

function setup() {
  const home = tmp("ans-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ profiles: { "glm-5.3-flash": {
    model_id: "glm-5.3-flash", key_env: "ZK", openai_base_url: "https://z.example/v4", input_per_mtok: 0.15, output_per_mtok: 0.5 } } }));
  const repo = tmp("ans-repo-");
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(repo, "calc.py"), "def group():\n    return None\n");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  const transcript = path.join(home, "t.jsonl");
  fs.writeFileSync(transcript, [
    JSON.stringify({ type: "user", timestamp: new Date(Date.now() - 60000).toISOString(), message: { role: "user", content: "make group() return 0" } }),
    JSON.stringify({ type: "assistant", timestamp: new Date(Date.now() - 50000).toISOString(), message: { role: "assistant", content: [{ type: "text", text: "Done: group() returns 0." }] } }),
  ].join("\n") + "\n");
  fs.writeFileSync(path.join(repo, "calc.py"), "def group():\n    return 0\n");     // the session's change
  return { env: { STEALTHLAB_HOME: home, ZK: "k" }, repo, transcript };
}
const chat = (content, seen = []) => async (url, init) => {
  seen.push({ url: String(url), body: JSON.parse(init.body) });
  return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content } }], usage: { prompt_tokens: 2000, completion_tokens: 200 } }) };
};
const reviewer = (verdict, seen = []) => async (a) => { seen.push(a); return { verdict, why: "w", model: "claude-sonnet-5-5" }; };

test("the notes: the requests so far, the files changed in this session and the change itself", () => {
  const { env, repo, transcript } = setup();
  const n = sessionNotes({ root: repo, transcriptPath: transcript, sessionId: "s1", env });
  assert.match(n, /Requests in this session so far:\n1\. make group\(\) return 0/);
  assert.match(n, /Files changed in this session: calc\.py/);
  assert.match(n, /-    return None\n\+    return 0/);
});

test("a reply the reviewer calls good is the answer; the model saw the notes; its cost is in the ledger", async () => {
  const { env, repo, transcript } = setup();
  const seen = [], reviewed = [];
  const a = await answerTurn({ prompt: "so group() returns 0 now?", root: repo, transcriptPath: transcript, sessionId: "s1", env,
                               fetchImpl: chat("Yes: calc.py's group() now returns 0 instead of None.", seen), review: reviewer("good", reviewed) });
  assert.equal(a.handled, true);
  assert.match(a.text, /^Answered by glm-5\.3-flash in \d+ s \(kell answered it before this session's model ran; checked by claude-sonnet-5-5\)\.\n\nYes: calc\.py/);
  const user = seen[0].body.messages.find((m) => m.role === "user").content;
  assert.match(user, /\+    return 0/, "the change itself");
  assert.match(user, /The developer's latest message:\nso group\(\) returns 0 now\?$/);
  assert.equal(reviewed[0].reply, "Yes: calc.py's group() now returns 0 instead of None.");
  const row = JSON.parse(fs.readFileSync(path.join(env.STEALTHLAB_HOME, "dispatch_costs.jsonl"), "utf8").trim().split("\n").pop());
  assert.equal(row.what, "answer");
  assert.ok(Math.abs(row.cost_usd - 0.0004) < 1e-9);
});

test("a reply the reviewer does not call good is never shown; no profile, an error or an empty reply is no answer", async () => {
  const { env, repo, transcript } = setup();
  for (const verdict of ["bad", "unsure"]) {
    assert.equal(await answerTurn({ prompt: "why 0?", root: repo, transcriptPath: transcript, env, fetchImpl: chat("Because."), review: reviewer(verdict) }), null);
  }
  assert.equal(await answerTurn({ prompt: "why 0?", root: repo, env, fetchImpl: chat("x"), review: async () => null }), null);
  assert.equal(await answerTurn({ prompt: "why 0?", root: repo, env: { ...env, STEALTHLAB_ANSWER_MODEL: "nope" }, fetchImpl: chat("x"), review: reviewer("good") }), null);
  assert.equal(await answerTurn({ prompt: "why 0?", root: repo, env, fetchImpl: async () => ({ ok: false, status: 500 }), review: reviewer("good") }), null);
  assert.equal(await answerTurn({ prompt: "why 0?", root: repo, env, fetchImpl: chat("   "), review: reviewer("good") }), null);
  assert.equal(await answerTurn({ prompt: "why 0?", root: repo, env: { ...env, STEALTHLAB_ANSWERS: "off" }, fetchImpl: chat("x"), review: reviewer("good") }), null);
});

test("hook: the judge says work=answer -> the cheap answer is delivered, remembered as a turn, and the session is told", async () => {
  const { env, repo, transcript } = setup();
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "config.json"), JSON.stringify({ dispatch: true }));
  const e = { ...env, STEALTHLAB_REVIEW_STRONG_MODEL: "glm-5.3-flash" };       // the reviewer through the same stub endpoint
  const out = [];
  const fetchImpl = async (url, init) => {
    if (String(url).endsWith("/triage")) return { ok: true, status: 200, text: async () => JSON.stringify({ needs_retrieval: false, kind: "repo_specific", work: "answer", provider: "jev" }) };
    const body = JSON.parse(init.body);
    const sys = body.messages[0].content;
    const content = /You check a reply/.test(sys) ? JSON.stringify({ verdict: "good", why: "matches the diff" }) : "Yes, group() returns 0 now.";
    return { ok: true, status: 200, json: async () => ({ choices: [{ message: { content } }], usage: {} }) };
  };
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "so group() returns 0 now?", cwd: repo, session_id: "s1", transcript_path: transcript }),
    settings: { url: "http://x/mcp" }, userAgent: "t", env: e, fetchImpl, write: (s) => out.push(JSON.parse(s)), log: () => {},
    dispatchImpl: async () => assert.fail("a question is not dispatched") });
  assert.equal(out.length, 1);
  assert.equal(out[0].decision, "block");
  assert.match(out[0].reason, /^Answered by glm-5\.3-flash in \d+ s[\s\S]*Yes, group\(\) returns 0 now\./);
  assert.deepEqual(dispatchedTurns("s1", { env: e }).map((t) => t.role), ["user", "assistant"]);
  assert.deepEqual(pendingLines("s1", { env: e }), ['Earlier in this session, glm-5.3-flash answered "so group() returns 0 now?" (kell, checked by a reviewer).']);
});
