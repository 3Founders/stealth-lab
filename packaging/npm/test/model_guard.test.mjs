import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { rememberLookup } from "../lib/capture_hook.mjs";
import {
  ACHIEVE_TOOL, DELEGATOR, MAX_DENIALS, RUN_RESULT_TOOL, addRouteHooks, claudeAlias, currentStep, onClaudePreToolUse, onCursorPreToolUse, onPlanReport,
  removeRouteHooks, reportReply,
} from "../lib/model_guard.mjs";
import { addCursorHooks } from "../lib/cursor_hooks.mjs";
import { planPart } from "../lib/hook.mjs";

const PLAN = {
  status: "ok", basis: "prior", instance_key: "g1.ab12", goal_id: "g1",
  ladder: ["claude-haiku-4-5|claude-code", "claude-sonnet-4-5|claude-code"],
  steps: [{ step: "*", ladder: [{ unit: "claude-haiku-4-5|claude-code", p_ok_mean: 0.712 }] }],
};
const REPLY = { outcome: "resolved", procedures: [{ procedure_id: "p1", goal_id: "g1" }], model_plan: PLAN };

function envWithPlan(sessionId = "s1", reply = REPLY) {
  const env = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "guard-")) };
  assert.equal(rememberLookup({ session_id: sessionId, prompt_id: "p" }, reply, { env }), true);
  return env;
}

const agentCall = (model, extra = {}) => ({ session_id: "s1", tool_name: "Agent",
  tool_input: { description: "edit config", prompt: "do it", subagent_type: "general-purpose", ...(model ? { model } : {}), ...extra } });

test("claude aliases: Claude models map to haiku/sonnet/opus, others to nothing", () => {
  assert.equal(claudeAlias("claude-haiku-4-5|claude-code"), "haiku");
  assert.equal(claudeAlias("claude-opus-5-5|claude-code"), "opus");
  assert.equal(claudeAlias("gpt-oss-120b|kel"), null);
});

test("Claude Code: a subagent asking for another model is refused with the plan's model, then let through", () => {
  const env = envWithPlan();
  assert.equal(currentStep(env, "s1").plan.current, "claude-haiku-4-5|claude-code");
  const out = onClaudePreToolUse(agentCall(), { env });
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /model: "haiku"/);
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /p_ok 0\.71/);
  assert.equal(onClaudePreToolUse(agentCall("haiku"), { env }), null);              // the right model passes
  for (let i = 1; i < MAX_DENIALS; i++) assert.ok(onClaudePreToolUse(agentCall("sonnet"), { env }));
  assert.equal(onClaudePreToolUse(agentCall("sonnet"), { env }), null);             // never blocks work for good
});

test("Claude Code: rewrite mode sets the model in updatedInput, keeping every other field", () => {
  const env = { ...envWithPlan(), STEALTHLAB_MODEL_GUARD: "rewrite" };
  const out = onClaudePreToolUse(agentCall("opus"), { env });
  assert.equal(out.hookSpecificOutput.permissionDecision, "allow");
  assert.deepEqual(out.hookSpecificOutput.updatedInput, { ...agentCall().tool_input, model: "haiku" });
});

test("Claude Code: off, other tools, no plan, or a non-Claude step are left alone", () => {
  const env = envWithPlan();
  assert.equal(onClaudePreToolUse(agentCall(), { env: { ...env, STEALTHLAB_MODEL_GUARD: "off" } }), null);
  assert.equal(onClaudePreToolUse({ ...agentCall(), tool_name: "Bash" }, { env }), null);
  assert.equal(onClaudePreToolUse({ ...agentCall(), session_id: "other" }, { env }), null);
  const open = envWithPlan("s1", { ...REPLY, model_plan: { ...PLAN, ladder: ["gpt-oss-120b|kel"] } });
  assert.equal(onClaudePreToolUse(agentCall(), { env: open }), null);
});

test("report_result: a failure moves the guard to next_model; a pass ends it (direct call and use_tool)", () => {
  const env = envWithPlan();
  const failed = { session_id: "s1", tool_name: "mcp__stealthlab__report_result",
    tool_response: [{ type: "text", text: JSON.stringify({ status: "rejected", next_model: "claude-sonnet-4-5|claude-code" }) }] };
  assert.equal(onPlanReport(failed, { env }).current, "claude-sonnet-4-5|claude-code");
  assert.match(onClaudePreToolUse(agentCall("haiku"), { env }).hookSpecificOutput.permissionDecisionReason, /"sonnet"/);
  const passed = { session_id: "s1", tool_name: "mcp__stealthlab__use_tool", tool_input: { name: "report_result" },
    tool_response: { content: [{ type: "text", text: JSON.stringify({ status: "accepted" }) }] } };
  assert.equal(onPlanReport(passed, { env }).done, true);
  assert.equal(onClaudePreToolUse(agentCall("opus"), { env }), null);
  assert.equal(reportReply({ tool_name: "mcp__stealthlab__use_tool", tool_input: { name: "find_ways" } }), null);
});

test("Cursor: a Task naming another model is refused with agent_message; no model field is left alone", () => {
  const env = envWithPlan("c1");
  const task = (input) => ({ conversation_id: "c1", tool_name: "Task", tool_input: input });
  assert.deepEqual(onCursorPreToolUse(task({ prompt: "x" }), { env }), {});
  const out = onCursorPreToolUse(task({ prompt: "x", model: "gpt-5" }), { env });
  assert.equal(out.permission, "deny");
  assert.match(out.agent_message, /claude-haiku-4-5/);
  assert.deepEqual(onCursorPreToolUse(task({ prompt: "x", model: "claude-haiku-4-5" }), { env }), {});
  assert.deepEqual(onCursorPreToolUse({ ...task({ model: "gpt-5" }), tool_name: "Shell" }, { env }), {});
});

test("hook installation: route hooks added and removed; Cursor gets preToolUse", () => {
  const launch = { command: "node", args: ["/x/stealthlab-mcp.mjs"] };
  const doc = addRouteHooks({ hooks: { PostToolUse: [{ matcher: "Bash", hooks: [{ command: "other" }] }] } }, launch);
  assert.equal(doc.hooks.PreToolUse[0].matcher, "Agent|Task|mcp__stealthlab-exec__achieve|Edit|Write|MultiEdit|NotebookEdit");
  assert.match(doc.hooks.PostToolUse[1].matcher, /mcp__stealthlab-exec__run_result$/);
  assert.match(doc.hooks.PreToolUse[0].hooks[0].command, /hook route-subagent$/);
  assert.equal(doc.hooks.PostToolUse.length, 2);
  assert.ok(removeRouteHooks(doc));
  assert.deepEqual(doc.hooks.PostToolUse, [{ matcher: "Bash", hooks: [{ command: "other" }] }]);
  assert.equal(doc.hooks.PreToolUse, undefined);
  const cur = addCursorHooks({}, launch, "linux");
  assert.match(cur.hooks.preToolUse[0].command, /hook cursor-pretool$/);
});

// --- an open-model step: handed to the local executor through the delegator ---------------------

const OPEN_PLAN = { ...PLAN, instance_key: "g1.cd34", ladder: ["glm-5.3|stealth", "claude-sonnet-4-5|claude-code"],
  steps: [{ step: "*", ladder: [{ unit: "glm-5.3|stealth", p_ok_mean: 0.64 }] }] };

function envWithOpenPlan(execConfig = { executors: { stealth: { models: ["glm-5.3"] } } }) {
  const env = envWithPlan("s1", { ...REPLY, model_plan: OPEN_PLAN });
  if (execConfig) fs.writeFileSync(path.join(env.STEALTHLAB_HOME, "exec.json"), JSON.stringify(execConfig));
  return env;
}
const achieveCall = (extra = {}) => ({ session_id: "s1", tool_name: ACHIEVE_TOOL,
  tool_input: { repo_path: "/r", task: "t", checks: ["true"], scope: ["a"], ...extra } });
const runResult = (r) => ({ session_id: "s1", tool_name: RUN_RESULT_TOOL,
  tool_response: [{ type: "text", text: JSON.stringify(r) }] });

test("open-model step: a subagent call is sent to the delegator with the plan's executor, model and instance_key", () => {
  const env = envWithOpenPlan();
  assert.equal(currentStep(env, "s1").plan.instance_key, "g1.cd34");
  const out = onClaudePreToolUse(agentCall("haiku"), { env });
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  const why = out.hookSpecificOutput.permissionDecisionReason;
  assert.match(why, new RegExp(`subagent_type: "${DELEGATOR}"`));
  assert.match(why, /executor=stealth model=glm-5\.3 instance_key=g1\.cd34/);
  assert.match(why, /p_ok 0\.64/);
  const delegated = agentCall(null, { subagent_type: DELEGATOR, prompt: "Do N-1. Run it with executor=stealth model=glm-5.3 instance_key=g1.cd34." });
  assert.equal(onClaudePreToolUse(delegated, { env }), null);
});

test("open-model step: achieve must name the plan's unit and key (deny, or rewrite in rewrite mode)", () => {
  const env = envWithOpenPlan();
  const out = onClaudePreToolUse(achieveCall(), { env });
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /executor=stealth model=glm-5\.3 instance_key=g1\.cd34/);
  assert.equal(onClaudePreToolUse(achieveCall({ executor: "stealth", model: "glm-5.3", instance_key: "g1.cd34" }), { env }), null);
  const rw = onClaudePreToolUse(achieveCall({ escalate: 2 }), { env: { ...env, STEALTHLAB_MODEL_GUARD: "rewrite" } });
  assert.equal(rw.hookSpecificOutput.permissionDecision, "allow");
  assert.deepEqual(rw.hookSpecificOutput.updatedInput,
    { ...achieveCall().tool_input, escalate: 2, executor: "stealth", model: "glm-5.3", instance_key: "g1.cd34" });
});

test("open-model step: the guard stands aside with no local executor for the model, and on a Claude step's achieve", () => {
  assert.equal(onClaudePreToolUse(agentCall(), { env: envWithOpenPlan(null) }), null);
  assert.equal(onClaudePreToolUse(agentCall(), { env: envWithOpenPlan({ executors: { opencode: { models: ["kimi-k2"] } } }) }), null);
  assert.equal(onClaudePreToolUse(achieveCall(), { env: envWithPlan() }), null);
  // a scaffold that is not an executor id still runs on whichever executor exec.json configures for the model
  const env = envWithOpenPlan({ executors: { opencode: { models: ["glm-5.3"] } } });
  assert.match(onClaudePreToolUse(agentCall(), { env }).hookSpecificOutput.permissionDecisionReason, /executor=opencode/);
});

test("run_result moves the guard: failed -> next_model, verified -> done; another plan's run is ignored", () => {
  const env = envWithOpenPlan();
  assert.equal(onPlanReport(runResult({ state: "running", evidence: { instance_key: "g1.cd34" } }), { env }), null);
  assert.equal(onPlanReport(runResult({ state: "failed", evidence: { instance_key: "other.1" }, next_model: "x|y" }), { env }), null);
  const moved = onPlanReport(runResult({ state: "failed", evidence: { instance_key: "g1.cd34" },
    next_model: "claude-sonnet-4-5|claude-code" }), { env });
  assert.equal(moved.current, "claude-sonnet-4-5|claude-code");
  assert.match(onClaudePreToolUse(agentCall("haiku"), { env }).hookSpecificOutput.permissionDecisionReason, /"sonnet"/);
  const env2 = envWithOpenPlan();
  assert.equal(onPlanReport(runResult({ state: "verified", evidence: { instance_key: "g1.cd34" } }), { env: env2 }).done, true);
  assert.equal(onClaudePreToolUse(agentCall(), { env: env2 }), null);
});

test("the plan text sends an open-model step to the delegator, with its exact line, and no second report", () => {
  const env = envWithOpenPlan();
  const text = planPart(OPEN_PLAN, env);
  assert.match(text, /do not solve it yourself first: call the Agent tool with subagent_type: "stealth-delegator", the whole task/);
  assert.match(text, /glm-5\.3, an open model/);
  assert.match(text, /"Run it with executor=stealth model=glm-5\.3 instance_key=g1\.cd34\."/);
  assert.match(text, /no report_result for it/);
  assert.doesNotMatch(planPart(OPEN_PLAN, envWithOpenPlan(null)), /delegator/, "no local executor: nothing to say");
  assert.doesNotMatch(planPart(PLAN, env), /delegator/);
});

// --- the main session's own edits ------------------------------------------------------------

test("main session: an edit while the plan says another model is refused with the hand-over call; subagents pass", () => {
  const env = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "guard-")) };
  rememberLookup({ session_id: "m1", prompt_id: "p" }, REPLY, { env, mine: "claude-opus-5-5|claude-code" });
  const edit = (extra = {}) => ({ session_id: "m1", tool_name: "Write", tool_input: { file_path: "a.py", content: "x" }, ...extra });
  const out = onClaudePreToolUse(edit(), { env });
  assert.equal(out.hookSpecificOutput.permissionDecision, "deny");
  assert.match(out.hookSpecificOutput.permissionDecisionReason, /Hand it over instead of editing: call the Agent tool with model: "haiku"/);
  assert.equal(onClaudePreToolUse(edit({ agent_id: "sub-1" }), { env }), null, "a subagent's edit is never held");
  assert.equal(onClaudePreToolUse({ ...edit(), tool_name: "Read" }, { env }), null, "reads are free");
  for (let i = 1; i < MAX_DENIALS; i++) assert.ok(onClaudePreToolUse(edit(), { env }));
  assert.equal(onClaudePreToolUse(edit(), { env }), null, "never blocks work for good");
});

test("main session: a rung on the session's own model, a finished plan, or guard off lets edits through", () => {
  const env = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "guard-")) };
  rememberLookup({ session_id: "m2", prompt_id: "p" }, REPLY, { env, mine: "claude-haiku-4-5|claude-code" });
  const edit = { session_id: "m2", tool_name: "Edit", tool_input: {} };
  assert.equal(onClaudePreToolUse(edit, { env }), null, "the plan's rung is this session's own model");
  const env2 = { STEALTHLAB_HOME: fs.mkdtempSync(path.join(os.tmpdir(), "guard-")) };
  rememberLookup({ session_id: "m2", prompt_id: "p" }, REPLY, { env: env2, mine: "claude-opus-5-5|claude-code" });
  assert.ok(onClaudePreToolUse(edit, { env: env2 }));
  assert.equal(onClaudePreToolUse(edit, { env: { ...env2, STEALTHLAB_MODEL_GUARD: "off" } }), null);
  onPlanReport({ session_id: "m2", tool_name: "mcp__stealthlab__report_result",
    tool_response: [{ type: "text", text: JSON.stringify({ status: "accepted" }) }] }, { env: env2 });
  assert.equal(onClaudePreToolUse(edit, { env: env2 }), null, "plan finished");
  const legacy = envWithPlan("m3");                                 // no `mine` recorded: never held
  assert.equal(onClaudePreToolUse({ ...edit, session_id: "m3" }, { env: legacy }), null);
});
