// Test-only executor. Runs `node test/fixtures/fake_agent.mjs`, which acts out
// the scenario given as JSON in STEALTHLAB_FAKE_SCENARIO (edits, sleepMs,
// silentMs, exitCode, finalMessage, learned, tokens, spawnGrandchild,
// printSecret, pidFile). getAdapter() only returns this adapter when
// env.STEALTHLAB_EXEC_ALLOW_FAKE === "1", and the fixture lives under test/,
// which is not in package.json `files`, so a published install reports it as
// not installed.
//
// Output format (defined by the fixture, so fully verified): free text lines,
// then one final line {"type":"final","message","learned":[...],"tokens":{in,out}}.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { jsonLines, plainResult, safeTaskArg, scrubEnv, tailText, num } from "./common.mjs";

export const FAKE_AGENT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "test", "fixtures", "fake_agent.mjs");
const FAKE_VERSION = "1.0.0";

const adapter = {
  id: "fake",
  bin: FAKE_AGENT,
  VERIFIED_WITH: { version: FAKE_VERSION, date: "2026-09-27", source: "test/fixtures/fake_agent.mjs (test fixture; defines its own flags)" },
  DOCS_SOURCE: "test/fixtures/fake_agent.mjs",
  AUTO_APPROVE_FLAG: "(none: the fixture never prompts)",

  async detect() {
    const installed = fs.existsSync(FAKE_AGENT);
    return { installed, version: installed ? FAKE_VERSION : null, bin: installed ? FAKE_AGENT : null };
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env } = {}) {
    const childEnv = scrubEnv(env);
    // The scenario is the one STEALTHLAB_* variable the fake needs; re-add it
    // after scrubbing (it carries no secret).
    childEnv.STEALTHLAB_FAKE_SCENARIO = env.STEALTHLAB_FAKE_SCENARIO ?? "{}";
    // Set by `node --test` for its children; not ours to pass on. undefined (not delete) so a
    // runtime that overlays this env onto the parent env also drops it.
    childEnv.NODE_TEST_CONTEXT = undefined;
    const args = [FAKE_AGENT];
    if (model) args.push("--model", String(model));
    void worktree; void timeoutS;
    return { cmd: process.execPath, args, env: childEnv, stdinText: safeTaskArg(task) };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const finals = jsonLines(stdout).filter((l) => l.type === "final");
    if (!finals.length) return plainResult(stdout, stderr);
    const f = finals[finals.length - 1];
    const t = f.tokens && typeof f.tokens === "object" ? f.tokens : null;
    return {
      finalMessage: tailText(typeof f.message === "string" ? f.message : ""),
      learned: Array.isArray(f.learned) ? f.learned.filter((s) => typeof s === "string").slice(0, 5) : [],
      tokens: t ? { in: num(t.in), out: num(t.out) } : null,
      costUsd: num(f.costUsd),
    };
  },

  async health() {
    const d = await adapter.detect();
    return d.installed ? { healthy: true, detail: `fake ${FAKE_VERSION} at ${FAKE_AGENT}` } : { healthy: false, detail: "fake agent fixture not present (not shipped)" };
  },
};

export default adapter;
