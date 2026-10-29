import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { cheapCandidates, dispatchPolicy, testingNote } from "../lib/dispatch.mjs";

test("the whole dispatch fits inside the prompt hook's timeout, with time kept for checking", () => {
  const p = dispatchPolicy({});
  assert.equal(p.totalS, 1110, "1200 s hook timeout minus 90");
  assert.equal(p.checkReserveS, 300);
  assert.equal(dispatchPolicy({ STEALTHLAB_PROMPT_HOOK_TIMEOUT_S: "600" }).totalS, 510);
  assert.equal(dispatchPolicy({ STEALTHLAB_DISPATCH_TOTAL_S: "400" }).totalS, 400);
});

test("one try per model: a model the plan and exec.json name two ways takes one slot", () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cands-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({
    executors: { stealth: { models: ["glm-5.3-flash", "gemma-4-31b-it", "minimax-m2-7"] } },
    profiles: Object.fromEntries(["glm-5.3-flash", "gemma-4-31b-it", "minimax-m2-7"].map((m) => [m, {
      model_id: m, key_env: "K", openai_base_url: "https://example.invalid/v1", input_per_mtok: 0.1, output_per_mtok: 0.2 }])) }));
  const env = { STEALTHLAB_HOME: home };
  const units = cheapCandidates({ ladder: ["glm-5.3-flash|stealth", "claude-opus-5-5|claude-code"] }, "claude-opus-5-5|claude-code", env, { max: 3 })
    .map((r) => r.model);
  assert.deepEqual(units, ["glm-5.3-flash", "gemma-4-31b-it", "minimax-m2-7"]);
  assert.equal(new Set(units).size, units.length);
});

test("the agent is told to run the nearby tests, not the whole suite, and that old failures are not its own", () => {
  const n = testingNote("python -m pytest -q", "tests/test_util.py");
  assert.match(n, /run `tests\/test_util\.py`/);
  assert.match(n, /Do not run the whole suite \(`python -m pytest -q`\)/);
  assert.match(n, /already fail on code you did not touch/);
  assert.equal(testingNote("", "x"), "", "no check: nothing to say");
  assert.match(testingNote("npm test", null), /the tests of the module you change/);
});
