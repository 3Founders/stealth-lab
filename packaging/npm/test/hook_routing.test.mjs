import { test } from "node:test";
import assert from "node:assert/strict";
import { CLAUDE_CODE_CANDIDATES, formatKnowledge, hookPolicy, planPart, routingArgs } from "../lib/hook.mjs";
import { buildReport, lookupIdentity } from "../lib/capture_hook.mjs";

const PLAN = {
  status: "ok", basis: "prior", instance_key: "g1.ab12",
  ladder: ["claude-haiku-4-5|claude-code", "claude-sonnet-5-5|claude-code"],
  steps: [{ step: "*", ladder: [{ unit: "claude-haiku-4-5|claude-code", p_ok_mean: 0.71 },
                                { unit: "claude-sonnet-5-5|claude-code", p_ok_mean: 0.88 }] }],
};

test("routing is on by default: my_model plus the other Claude Code models", () => {
  const a = routingArgs(hookPolicy({}), "claude-sonnet-5-5");
  assert.equal(a.my_model, "claude-sonnet-5-5|claude-code");
  assert.deepEqual(a.candidates, CLAUDE_CODE_CANDIDATES.filter((c) => c !== "claude-sonnet-5-5|claude-code"));
});

test("routing off, unknown model, or a custom list", () => {
  assert.deepEqual(routingArgs(hookPolicy({ STEALTHLAB_HOOK_ROUTING: "off" }), "claude-sonnet-5-5"), {});
  assert.deepEqual(routingArgs(hookPolicy({}), null), {});
  assert.deepEqual(routingArgs(hookPolicy({ STEALTHLAB_HOOK_CANDIDATES: "" }), "m1"), { my_model: "m1|claude-code" });
  const custom = routingArgs(hookPolicy({ STEALTHLAB_HOOK_CANDIDATES: "gpt-oss-120b|kel, bad entry ,x|y" }), "m1");
  assert.deepEqual(custom.candidates, ["gpt-oss-120b|kel", "x|y"]);
});

test("the plan reaches the agent: first model, fallback, and report_result with the key", () => {
  const p = planPart(PLAN);
  assert.match(p, /do this with claude-haiku-4-5 \(p_ok 0\.71\); if its check fails, claude-sonnet-5-5 \(p_ok 0\.88\)/);
  assert.match(p, /report_result\(instance_key="g1\.ab12"/);
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
