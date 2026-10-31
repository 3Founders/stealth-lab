import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { changedCode, changesSource, parseVerdict, reviewPatch } from "../lib/review.mjs";

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
    "gemma-4-31b-it": { openai_base_url: "https://example.test/v1", key_env: "K", input_per_mtok: 1, output_per_mtok: 2 } } }));
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
  assert.match(sent.body.messages[0].content, /new meaning the request does not ask for[\s\S]*must give exactly that/, "v2 by default");
  await reviewPatch({ prompt: "x", diff: "d", env: { ...env, STEALTHLAB_REVIEW_PROMPT: "v1" }, fetchImpl });
  assert.doesNotMatch(sent.body.messages[0].content, /new meaning/, "v1: the pre-registered prompt");
});

test("changedCode: the functions a diff changes, read from the worktree after the change; tests left out", () => {
  const wt = fs.mkdtempSync(path.join(os.tmpdir(), "review-wt-"));
  fs.mkdirSync(path.join(wt, "pkg")); fs.mkdirSync(path.join(wt, "tests"));
  const filler = Array.from({ length: 30 }, (_, i) => `def f${i}():\n    return ${i}\n`).join("\n");
  fs.writeFileSync(path.join(wt, "pkg", "m.py"), `${filler}\n\ndef target(x):\n    y = x + 1\n    return y * 2\n`);
  fs.writeFileSync(path.join(wt, "tests", "test_m.py"), "def test_t():\n    assert True\n");
  const n = fs.readFileSync(path.join(wt, "pkg", "m.py"), "utf8").split("\n").findIndex((l) => l.includes("y = x + 1")) + 1;
  const diff = `diff --git a/pkg/m.py b/pkg/m.py\n--- a/pkg/m.py\n+++ b/pkg/m.py\n@@ -${n},1 +${n},1 @@\n-    y = x\n+    y = x + 1\n` +
    "diff --git a/tests/test_m.py b/tests/test_m.py\n--- a/tests/test_m.py\n+++ b/tests/test_m.py\n@@ -1,1 +1,2 @@\n+x\n";
  const code = changedCode(wt, diff);
  assert.match(code, /--- pkg\/m\.py lines \d+-\d+ \(target\), after the change ---[\s\S]*def target\(x\):[\s\S]*return y \* 2/);
  assert.doesNotMatch(code, /def f3\(\)|test_t/, "the changed function only, no test file");
  assert.equal(changedCode(null, diff), "");
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

test("reviewPatchInRepo: the strong reviewer reads the checkout with read-only tools and returns fixes/does_not_fix", async () => {
  const { reviewPatchInRepo } = await import("../lib/review.mjs");
  const calls = [];
  const runClaude = async (args, input) => {
    calls.push({ args, input });
    return { status: 0, stdout: JSON.stringify({ result: '{"verdict": "fixes", "why": "the tests cover both cases"}', total_cost_usd: 0.02 }) };
  };
  const v = await reviewPatchInRepo({ root: os.tmpdir(), prompt: "add tests for strip_meta", diff: "diff --git a/t.py b/t.py\n+def test_x(): pass\n",
                                       env: { STEALTHLAB_SPEND_LOG: "off" }, runClaude });
  assert.equal(v.verdict, "fixes");
  assert.equal(v.cost_usd, 0.02);
  assert.ok(calls[0].args.includes("--system-prompt"));
  assert.match(calls[0].args.join(" "), /Read/);
  assert.match(calls[0].input, /applied in this checkout/);
  assert.equal(await reviewPatchInRepo({ root: os.tmpdir(), prompt: "x", diff: "  ", runClaude }), null);
});

test("without Claude Code a Claude reviewer falls back to an exec.json profile (deepseek-v3.2 by default)", async () => {
  const { reviewPatchInRepo, reviewPatch } = await import("../lib/review.mjs");
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "kell-rev-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: {}, profiles: {
    "deepseek-v3.2": { model_id: "deepseek-v3.2", key_env: "K", openai_base_url: "https://x.test/v1", input_per_mtok: 0.28, output_per_mtok: 0.42 } } }));
  const env = { STEALTHLAB_HOME: home, K: "k", STEALTHLAB_CLAUDE: "off", STEALTHLAB_SPEND_LOG: "off" };
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ url, model: JSON.parse(init.body).model });
    return { ok: true, json: async () => ({ choices: [{ message: { content: '{"verdict": "fixes", "why": "ok"}' } }], usage: { prompt_tokens: 1000, completion_tokens: 10 } }) };
  };
  const v = await reviewPatchInRepo({ root: os.tmpdir(), prompt: "fix f", diff: "+x", env, fetchImpl });
  assert.equal(v.verdict, "fixes");
  assert.equal(v.model, "deepseek-v3.2");
  const w = await reviewPatch({ prompt: "fix f", diff: "+x", env, fetchImpl, model: "claude-sonnet-5-5" });
  assert.equal(w.model, "deepseek-v3.2");
  assert.deepEqual(seen.map((s) => s.model), ["deepseek-v3.2", "deepseek-v3.2"]);
});
