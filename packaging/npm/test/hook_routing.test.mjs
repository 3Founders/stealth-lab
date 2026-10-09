import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { CLAUDE_CODE_CANDIDATES, execCandidates, formatKnowledge, hookPolicy, planPart, routingArgs } from "../lib/hook.mjs";

// A home with no ~/.claude and no ~/.stealthlab, so the user's own exec setup never leaks into a test.
const bareEnv = () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "hook-home-"));
  return { STEALTHLAB_TEST_HOME: home, STEALTHLAB_HOME: path.join(home, ".stealthlab") };
};
import { buildReport, lookupIdentity } from "../lib/capture_hook.mjs";

const PLAN = {
  status: "ok", basis: "prior", instance_key: "g1.ab12",
  ladder: ["claude-haiku-4-5|claude-code", "claude-sonnet-5-5|claude-code"],
  steps: [{ step: "*", ladder: [{ unit: "claude-haiku-4-5|claude-code", p_ok_mean: 0.71 },
                                { unit: "claude-sonnet-5-5|claude-code", p_ok_mean: 0.88 }] }],
};

test("routing is on by default: my_model plus the other Claude Code models", () => {
  const a = routingArgs(hookPolicy(bareEnv()), "claude-sonnet-5-5");
  assert.equal(a.my_model, "claude-sonnet-5-5|claude-code");
  assert.deepEqual(a.candidates, CLAUDE_CODE_CANDIDATES.filter((c) => c !== "claude-sonnet-5-5|claude-code"));
});

test("with the local executor installed, the models exec.json configures are candidates too", () => {
  const env = bareEnv();
  fs.mkdirSync(env.STEALTHLAB_HOME, { recursive: true });
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"),
    JSON.stringify({ executors: { stealth: { models: ["glm-5.3", "kimi-k2"] }, opencode: { models: ["glm-5.3"] } } }));
  assert.deepEqual(execCandidates(env), [], "no stealth-delegator agent: the executor is not installed");
  fs.mkdirSync(path.join(env.STEALTHLAB_TEST_HOME, ".claude", "agents"), { recursive: true });
  fs.writeFileSync(path.join(env.STEALTHLAB_TEST_HOME, ".claude", "agents", "stealth-delegator.md"), "x");
  assert.deepEqual(execCandidates(env), ["glm-5.3|stealth", "kimi-k2|stealth", "glm-5.3|opencode"]);
  const a = routingArgs(hookPolicy(env), "claude-sonnet-5-5");
  assert.ok(a.candidates.includes("glm-5.3|stealth") && a.candidates.includes("claude-haiku-4-5|claude-code"));
  assert.deepEqual(routingArgs(hookPolicy({ ...env, STEALTHLAB_HOOK_CANDIDATES: "x|y" }), "m1").candidates, ["x|y"]);
  fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), "{ not json");
  assert.deepEqual(execCandidates(env), []);
});

test("routing off, unknown model, or a custom list", () => {
  assert.deepEqual(routingArgs(hookPolicy({ STEALTHLAB_HOOK_ROUTING: "off" }), "claude-sonnet-5-5"), {});
  assert.deepEqual(routingArgs(hookPolicy({}), null), {});
  assert.deepEqual(routingArgs(hookPolicy({ STEALTHLAB_HOOK_CANDIDATES: "" }), "m1"),
    { my_model: "m1|claude-code", model_constraints: { reliability_baseline: "m1|claude-code" } });
  const custom = routingArgs(hookPolicy({ STEALTHLAB_HOOK_CANDIDATES: "gpt-oss-120b|kel, bad entry ,x|y" }), "m1");
  assert.deepEqual(custom.candidates, ["gpt-oss-120b|kel", "x|y"]);
});

test("the plan reaches the agent: first model, fallback, and report_result with the key", () => {
  const p = planPart(PLAN, {}, "claude-opus-5-5|claude-code");
  assert.match(p, /follow it: start by handing this task over, do not solve it yourself first: call the Agent tool with model: "haiku" and the whole task \(p_ok 0\.71\)/);
  assert.match(p, /If that fails its check, next call the Agent tool with model: "sonnet" and the whole task \(p_ok 0\.88\)/);
  assert.match(p, /report_result\(instance_key="g1\.ab12"/);
  const self = planPart(PLAN, {}, "claude-haiku-4-5|claude-code");
  assert.match(self, /^Model plan \(prior\): do it yourself \(p_ok 0\.71\)\. If that fails its check, next call the Agent tool with model: "sonnet"/);
  assert.doesNotMatch(self, /do not solve it yourself/);
  assert.equal(planPart({ status: "not_ready" }), "");
  assert.match(formatKnowledge({ outcome: "no_match", model_plan: PLAN }), /Model plan \(prior\)/);
});

test("with a plan, the captured outcome goes to report_result for that instance", () => {
  const reply = { outcome: "resolved", procedures: [{ procedure_id: "p1", goal_id: "g1" }], model_plan: PLAN };
  const lookup = lookupIdentity(reply);
  assert.equal(lookup.instance_key, "g1.ab12");
  const session = { lookup, prompt_key: "k", tests: [{ verdict: false }] };
  const r = buildReport(session, { sessionId: "s", model: "claude-haiku-4-5" });
  assert.deepEqual(r, { via: "report_result", instance_key: "g1.ab12", model: "claude-haiku-4-5",
                        scaffold: "claude-code", accepted: false, check_kind: "tests" });
  const plain = buildReport({ lookup: lookupIdentity({ ...reply, model_plan: undefined }), prompt_key: "k",
                              tests: [{ verdict: true }] }, { sessionId: "s", model: "m" });
  assert.equal(plain.via, undefined);
  assert.match(plain.instance_key, /^cc-/);
});

test("a plan on a virtual key (no global Goal) is still remembered and reported", () => {
  const reply = { outcome: "no_match", model_plan: { ...PLAN, goal_id: "v-goal", case: { kind: "repo" } } };
  const lookup = lookupIdentity(reply);
  assert.deepEqual(lookup, { outcome: "planned", goal_id: "v-goal", procedure_id: null, instance_key: "g1.ab12" });
  const r = buildReport({ lookup, prompt_key: "k", tests: [{ verdict: true }] }, { sessionId: "s", model: "m" });
  assert.equal(r.via, "report_result");
  assert.equal(lookupIdentity({ outcome: "no_match" }), null);
});

test("the reliability target is a setting the hook passes as model_constraints", () => {
  const a = routingArgs(hookPolicy({ STEALTHLAB_HOOK_RELIABILITY: "0.7", STEALTHLAB_HOOK_RELIABILITY_CONFIDENCE: "0.8" }), "m");
  assert.deepEqual(a.model_constraints, { reliability_target: 0.7, reliability_confidence: 0.8 });
  assert.equal(routingArgs(hookPolicy({ STEALTHLAB_HOOK_RELIABILITY: "2" }), "m").model_constraints, undefined);
});

test("by default the plan is matched to this session's own model: no less reliable than it alone, minus a tolerance", () => {
  assert.deepEqual(routingArgs(hookPolicy({}), "m").model_constraints, { reliability_baseline: "m|claude-code" });
  assert.deepEqual(routingArgs(hookPolicy({ STEALTHLAB_HOOK_RELIABILITY: "match:0.05" }), "m").model_constraints,
    { reliability_baseline: "m|claude-code", reliability_tolerance: 0.05 });
  assert.equal(routingArgs(hookPolicy({ STEALTHLAB_HOOK_RELIABILITY: "match:7" }), "m").model_constraints, undefined,
    "a malformed tolerance asks for no target at all (server default)");
});

test("lean delivery (strong session models) still carries the model plan", () => {
  const reply = { outcome: "no_match", model_plan: PLAN };
  const text = formatKnowledge(reply, 8000, { mode: "lean", mine: "claude-opus-5-5|claude-code" });
  assert.match(text, /Model plan \(prior\) -- follow it/);
  assert.match(text, /model: "haiku"/);
  assert.equal(formatKnowledge({ outcome: "no_match" }, 8000, { mode: "lean" }), "", "nothing to say: nothing shown");
});
