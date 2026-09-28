import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { deliveryMode, detectModel, formatKnowledge, hookPolicy, runPromptHook } from "../lib/hook.mjs";

const RESOLVED = { outcome: "resolved", procedures: [{ procedure_id: "p1", name: "Way", steps: [{ order: 1, do: "do it" }],
  verified_solution: { code: "x = 1", language: "python" } }],
  related_examples: [{ goal_name: "Other", verified_solution: { code: "y = 2" } }] };
const AMBIGUOUS = { outcome: "ambiguous", suggested: { goal_name: "Near", verified_solution: { code: "z = 3" } } };

test("default mode is full and unchanged: resolved way, near misses and related examples", () => {
  const p = hookPolicy({});
  assert.equal(p.mode, "full");
  assert.equal(p.strongMode, null);
  assert.equal(formatKnowledge(RESOLVED, 8000), formatKnowledge(RESOLVED, 8000, { mode: "full" }));
  assert.match(formatKnowledge(RESOLVED), /Similar solved problem/);
  assert.match(formatKnowledge(AMBIGUOUS), /Closest known Goal/);
});

test("lean: only a resolved way; near misses and related examples are dropped; off: nothing", () => {
  const lean = formatKnowledge(RESOLVED, 8000, { mode: "lean" });
  assert.match(lean, /Known way/);
  assert.match(lean, /x = 1/);
  assert.doesNotMatch(lean, /Similar solved problem|y = 2/);
  assert.equal(formatKnowledge(AMBIGUOUS, 8000, { mode: "lean" }), "");
  assert.equal(formatKnowledge(RESOLVED, 8000, { mode: "off" }), "");
});

test("the strong-model override applies only when set and the model matches", () => {
  const p = hookPolicy({ STEALTHLAB_HOOK_MODE_STRONG: "off" });
  assert.equal(deliveryMode(p, "claude-sonnet-5"), "off");
  assert.equal(deliveryMode(p, "claude-opus-5-5"), "off");
  assert.equal(deliveryMode(p, "gpt-oss-120b"), "full");
  assert.equal(deliveryMode(p, null), "full", "unknown model -> the general mode");
  assert.equal(deliveryMode(hookPolicy({}), "claude-sonnet-5"), "full", "no override set");
  const custom = hookPolicy({ STEALTHLAB_HOOK_MODE: "lean", STEALTHLAB_HOOK_MODE_STRONG: "full", STEALTHLAB_HOOK_STRONG_MODELS: "^deepseek" });
  assert.equal(deliveryMode(custom, "deepseek-v3.2"), "full");
  assert.equal(deliveryMode(custom, "claude-sonnet-5"), "lean");
  assert.equal(hookPolicy({ STEALTHLAB_HOOK_MODE: "loud" }).mode, "full", "unknown values fall back to full");
  assert.equal(hookPolicy({ STEALTHLAB_HOOK_STRONG_MODELS: "([bad" }).strongModels.test("sonnet"), true, "bad regex -> default");
});

test("model detection: transcript, then ANTHROPIC_MODEL, then settings.json", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sl-model-"));
  const tr = path.join(dir, "t.jsonl");
  fs.writeFileSync(tr, JSON.stringify({ message: { model: "claude-haiku-4-5-20251001" } }) + "\n");
  fs.writeFileSync(path.join(dir, "settings.json"), JSON.stringify({ model: "opus" }));
  const env = { CLAUDE_CONFIG_DIR: dir };
  assert.equal(detectModel({ transcript_path: tr }, { ...env, ANTHROPIC_MODEL: "x" }), "claude-haiku-4-5-20251001");
  assert.equal(detectModel({}, { ...env, ANTHROPIC_MODEL: "claude-sonnet-5" }), "claude-sonnet-5");
  assert.equal(detectModel({}, env), "opus");
  assert.equal(detectModel({}, { CLAUDE_CONFIG_DIR: path.join(dir, "none") }), null);
});

test("mode off for a strong model skips the lookup entirely (no network call)", async () => {
  let called = 0;
  const env = { STEALTHLAB_HOOK_MODE_STRONG: "off", ANTHROPIC_MODEL: "claude-sonnet-5",
    STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "sl-mode-")) };
  await runPromptHook({ stdinText: JSON.stringify({ prompt: "fix the date parser so february 30 raises an error" }),
    settings: { url: "https://example.invalid/mcp" }, userAgent: "t", env,
    fetchImpl: async () => { called++; throw new Error("should not be called"); }, write: () => assert.fail(), log: () => {} });
  assert.equal(called, 0);
});
