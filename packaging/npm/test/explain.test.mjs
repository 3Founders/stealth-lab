import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { explainChange, tidy } from "../lib/explain.mjs";

const PROFILE = { profiles: { "gemma-4-31b-it": { openai_base_url: "https://example.test/v1", model_id: "gpt-oss-120b", key_env: "X_KEY",
  input_per_mtok: 0.04, output_per_mtok: 0.17 } } };
const env = () => ({ STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "explain-")), X_KEY: "k" });
const reply = (content, status = 200) => async () => ({ ok: status === 200, status,
  json: async () => ({ choices: [{ message: { content } }], usage: { prompt_tokens: 2000, completion_tokens: 100 } }) });

test("a cheap model explains the change; the cost goes to dispatch's ledger", async () => {
  const e = env();
  let sent = null;
  const text = await explainChange({ request: "make add() reject strings", diff: "+    if isinstance(a, str): raise TypeError", check: "pytest -q",
    env: e, readConfig: () => PROFILE, fetchImpl: async (url, init) => { sent = JSON.parse(init.body); return reply("add() in calc.py now raises TypeError for strings.")(); } });
  assert.equal(text, "add() in calc.py now raises TypeError for strings.");
  assert.match(sent.messages[1].content, /make add\(\) reject strings/);
  assert.match(fs.readFileSync(path.join(e.STEALTHLAB_HOME, "dispatch_costs.jsonl"), "utf-8"), /"what":"explain"/);
});

test("no profile, an error, a timeout or the switch off: the agent's own summary, shortened", async () => {
  const summary = "Changed calc.py so add rejects strings.";
  assert.equal(await explainChange({ request: "r", diff: "d", summary, env: env(), readConfig: () => ({}) }), summary);
  assert.equal(await explainChange({ request: "r", diff: "d", summary, env: env(), readConfig: () => PROFILE, fetchImpl: reply("", 503) }), summary);
  assert.equal(await explainChange({ request: "r", diff: "d", summary, env: env(), readConfig: () => PROFILE,
    fetchImpl: async () => { const e = new Error("t"); e.name = "TimeoutError"; throw e; } }), summary);
  let called = false;
  assert.equal(await explainChange({ request: "r", diff: "d", summary, env: { ...env(), STEALTHLAB_EXPLAIN: "off" }, readConfig: () => PROFILE,
    fetchImpl: async () => { called = true; return reply("x")(); } }), summary);
  assert.equal(called, false);
  assert.equal(await explainChange({ request: "r", diff: "d", env: env(), readConfig: () => ({}) }), "", "nothing to say: nothing");
});

test("explanations are plain and short: code blocks dropped, long text cut at a word", () => {
  assert.equal(tidy("Fixed it.\n```py\nx=1\n```"), "Fixed it.");
  const long = tidy("word ".repeat(400));
  assert.ok(long.length <= 700 && long.endsWith("..."));
});
