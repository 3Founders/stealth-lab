import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { rememberLookup } from "../lib/capture_hook.mjs";
import {
  MAX_DENIALS, addRouteHooks, claudeAlias, currentStep, onClaudePreToolUse, onCursorPreToolUse, onPlanReport,
  removeRouteHooks, reportReply,
} from "../lib/model_guard.mjs";
import { addCursorHooks } from "../lib/cursor_hooks.mjs";

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
  assert.equal(doc.hooks.PreToolUse[0].matcher, "Agent|Task");
  assert.match(doc.hooks.PreToolUse[0].hooks[0].command, /hook route-subagent$/);
  assert.equal(doc.hooks.PostToolUse.length, 2);
  assert.ok(removeRouteHooks(doc));
  assert.deepEqual(doc.hooks.PostToolUse, [{ matcher: "Bash", hooks: [{ command: "other" }] }]);
  assert.equal(doc.hooks.PreToolUse, undefined);
  const cur = addCursorHooks({}, launch, "linux");
  assert.match(cur.hooks.preToolUse[0].command, /hook cursor-pretool$/);
});
