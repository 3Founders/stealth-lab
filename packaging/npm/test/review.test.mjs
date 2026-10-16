import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { changesSource, parseVerdict, reviewPatch } from "../lib/review.mjs";

test("parseVerdict: the JSON verdict, inside other text too; anything else is no verdict", () => {
  assert.deepEqual(parseVerdict('ok {"verdict": "fixes", "why": "handles both"}'), { verdict: "fixes", why: "handles both" });
  assert.equal(parseVerdict('{"verdict": "maybe"}'), null);
  assert.equal(parseVerdict("no json"), null);
});

test("changesSource: tests, docs and config alone are not a fix", () => {
  assert.equal(changesSource(["tests/test_x.py", "README.md", "setup.cfg"]), false);
  assert.equal(changesSource(["src/pkg/x.py", "tests/test_x.py"]), true);
  assert.equal(changesSource(["web/app.spec.ts"]), false);
});

test("reviewPatch: one call to the review profile with the request and the diff; off or no profile -> null", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "review-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: {}, profiles: {
    "gpt-oss-120b": { openai_base_url: "https://example.test/v1", key_env: "K", input_per_mtok: 1, output_per_mtok: 2 } } }));
  const env = { STEALTHLAB_HOME: home, K: "k" };
  let sent = null;
  const fetchImpl = async (url, o) => { sent = { url, body: JSON.parse(o.body) };
    return { ok: true, json: async () => ({ choices: [{ message: { content: '{"verdict": "does_not_fix", "why": "only the example"}' } }], usage: { prompt_tokens: 1000, completion_tokens: 100 } }) }; };
  const r = await reviewPatch({ prompt: "make f handle negatives", diff: "--- a/f.py\n+++ b/f.py\n+x", env, fetchImpl });
  assert.equal(r.verdict, "does_not_fix");
  assert.equal(r.cost_usd, (1000 * 1 + 100 * 2) / 1e6);
  assert.match(sent.url, /example\.test\/v1\/chat\/completions$/);
  assert.match(sent.body.messages[1].content, /make f handle negatives[\s\S]*\+\+\+ b\/f\.py/);
  assert.equal(await reviewPatch({ prompt: "x", diff: "d", env: { ...env, STEALTHLAB_REVIEW_MODEL: "off" }, fetchImpl }), null);
  assert.equal(await reviewPatch({ prompt: "x", diff: "d", env: { ...env, STEALTHLAB_REVIEW_MODEL: "nope" }, fetchImpl }), null);
});

test("reviewPatch with a Claude model: one lean claude -p call, the verdict from its JSON result", async () => {
  let args = null, input = null;
  const runClaude = async (a, i) => { args = a; input = i;
    return { status: 0, stdout: JSON.stringify({ result: '{"verdict": "fixes", "why": "both cases"}', total_cost_usd: 0.012 }) }; };
  const r = await reviewPatch({ prompt: "fix f", diff: "+++ b/f.py\n+x", env: {}, model: "claude-sonnet-5-5", runClaude });
  assert.deepEqual([r.verdict, r.model, r.cost_usd], ["fixes", "claude-sonnet-5-5", 0.012]);
  assert.deepEqual(args.slice(0, 3), ["-p", "--model", "claude-sonnet-5-5"]);
  assert.ok(args.includes("--tools") && args[args.indexOf("--tools") + 1] === "", "no tools");
  assert.match(input, /fix f[\s\S]*\+\+\+ b\/f\.py/);
  const bad = await reviewPatch({ prompt: "x", diff: "d", env: {}, model: "claude-sonnet-5-5", runClaude: async () => ({ status: 1, stdout: "" }) });
  assert.equal(bad, null);
});
