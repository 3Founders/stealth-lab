// Model guard: makes the model plan bind when the agent hands work to a subagent, in Claude Code and Cursor.
//
// The knowledge hook (lib/hook.mjs) asks find_ways for a model plan and shows it to the agent; until now
// following it was up to the agent. Neither client lets a hook switch the MAIN session's model, but both route
// delegated work through one tool -- Claude Code's `Agent` (formerly `Task`), Cursor's `Task` -- and both let a
// pre-tool hook see that call:
//
//   Claude Code  PreToolUse on Agent|Task (`hook route-subagent`). The Agent tool takes an optional `model`
//                ("haiku" | "sonnet" | "opus"). If the call does not ask for the plan's current model:
//                  mode "deny" (default): the call is refused with the reason "use model: <alias>", and the agent
//                    calls again with it. Reported 2026-04 (anthropics/claude-code #39814, #44412): `updatedInput`
//                    was silently ignored for the Agent tool, while an explicit `model` argument is honoured -- so
//                    refusing is the way that works on every version;
//                  mode "rewrite" (STEALTHLAB_MODEL_GUARD=rewrite): `updatedInput` with the model set, for
//                    versions that apply it to the Agent tool.
//                At most MAX_DENIALS refusals per plan step, then the call goes through as asked: the guard never
//                stops work.
//                PostToolUse on report_result (`hook route-report`): a failed attempt's reply names the next model
//                (`next_model`); the guard follows the ladder from then on, and stops once a result passed.
//   Cursor       preToolUse on Task (`hook cursor-pretool`): Cursor has no documented `model` argument on Task and
//                ignores updated_input for it (forum report 2026-02), so the guard can only refuse a Task whose
//                input names a different model, with agent_message saying which to use. With no model in the
//                input it lets the call through: there is nothing it can set. Cursor routes only when
//                STEALTHLAB_CURSOR_CANDIDATES names models to plan over (its hook sends my_model as well).
//
// A plan step whose model is NOT a Claude model (GLM, DeepSeek, ...) cannot run as a Claude Code subagent. When the
// local executor is installed (`install --with-exec`) and ~/.stealthlab/exec.json configures an executor for that
// model, the guard hands the step to it:
//   PreToolUse on Agent|Task: refuse a call that is not the `stealth-delegator` subagent carrying the plan's
//                instance_key, with the exact arguments to use (executor, model, instance_key);
//   PreToolUse on the executor's `achieve`: the same refusal (or, in rewrite mode, updatedInput) when the call
//                does not name the plan's unit and instance_key -- the delegator is a subagent, and Claude Code
//                hooks see its tool calls under the same session;
//   PostToolUse on `run_result`: the runtime has already reported each attempt with report_result (lib/exec);
//                a verified run ends the plan, a failed one moves the guard to the reply's next_model.
// With no local executor for the model the guard stands aside, as before. STEALTHLAB_MODEL_GUARD=off disables it all.
// State lives in the capture session file (lib/capture_hook.mjs): the plan's ladder, current step and instance_key.
//
// Verified live 2026-10-09, Claude Code 2.1.292 (headless, seeded plan "sonnet first", main session on Haiku): the
// deny reached the agent, which called Agent again with model "sonnet"; the subagent ran on claude-sonnet-5-5.
// Hooks see a subagent's own tool calls under the PARENT session_id (plus agent_id / agent_type), which is what the
// achieve / run_result hooks rely on. Not yet run live: rewrite mode, report_result, the executor path.
import fs from "node:fs";
import { logHook } from "./subagent_hook.mjs";
import { sessionFile } from "./capture_hook.mjs";
import { localUnit, readExecConfig } from "./exec/store.mjs";

export const MAX_DENIALS = 2;
export const SUBAGENT_TOOLS = new Set(["Agent", "Task"]);
export const DELEGATOR = "stealth-delegator";
export const ACHIEVE_TOOL = "mcp__stealthlab-exec__achieve";
export const RUN_RESULT_TOOL = "mcp__stealthlab-exec__run_result";
export const ROUTE_HOOKS = {
  PreToolUse: { matcher: `Agent|Task|${ACHIEVE_TOOL}`, mark: "hook route-subagent" },
  PostToolUse: { matcher: `mcp__stealthlab__report_result|mcp__stealthlab__use_tool|${RUN_RESULT_TOOL}`, mark: "hook route-report" },
};

// The local {executor, model} for a plan unit, or null (no exec.json, malformed, or nothing configured for it).
export function executorFor(unit, env = process.env) {
  try { return localUnit(unit, readExecConfig(env)); } catch { return null; }
}

export function guardMode(env = process.env) {
  const v = String(env.STEALTHLAB_MODEL_GUARD || "deny").toLowerCase();
  return ["off", "deny", "rewrite"].includes(v) ? v : "deny";
}

// "claude-haiku-4-5|claude-code" -> "haiku"; a non-Claude model -> null (a Claude Code subagent cannot run it).
export function claudeAlias(unit) {
  const model = String(unit || "").split("|")[0].toLowerCase();
  if (!model.includes("claude")) return null;
  for (const a of ["haiku", "sonnet", "opus"]) if (model.includes(a)) return a;
  return null;
}

const readJson = (file) => { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; } };
const writeJson = (file, obj) => { try { fs.writeFileSync(file, JSON.stringify(obj), { mode: 0o600 }); } catch { /* best effort */ } };

// The plan step in force for this session, or null (no plan, plan finished, guard off).
export function currentStep(env, sessionId) {
  const file = sessionFile(env, sessionId);
  const s = file && readJson(file);
  const plan = s?.plan;
  if (!plan || plan.done || !Array.isArray(plan.ladder) || !plan.current) return null;
  return { file, session: s, plan };
}

// --- Claude Code ------------------------------------------------------------------------------

function deny(step, reason, env, what) {
  const denials = Number(step.plan.denials || 0);
  if (denials >= MAX_DENIALS) {
    logHook(env, "route-subagent", `let through after ${denials} refusals`);
    return null;
  }
  step.plan.denials = denials + 1;
  writeJson(step.file, step.session);
  logHook(env, "route-subagent", `refused: ${what}`);
  return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: reason } };
}

const pOkNote = (plan) => (plan.p_ok?.[plan.current] !== undefined ? ` (p_ok ${plan.p_ok[plan.current]})` : "");

// A non-Claude step with a local executor: the work goes to the delegator, which calls achieve with these.
function onOpenModelStep(payload, step, local, input, mode, env) {
  const key = step.plan.instance_key || "";
  const args = `executor=${local.executor} model=${local.model}${key ? ` instance_key=${key}` : ""}`;
  if (payload.tool_name === ACHIEVE_TOOL) {
    if (input.executor === local.executor && input.model === local.model && (!key || input.instance_key === key)) return null;
    if (mode === "rewrite") {
      logHook(env, "route-subagent", `rewrite achieve -> ${local.model}|${local.executor}`);
      return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow",
        updatedInput: { ...input, executor: local.executor, model: local.model, ...(key ? { instance_key: key } : {}) } } };
    }
    return deny(step, `StealthLab's model plan runs this step on ${local.model} through the local ${local.executor} ` +
      `executor${pOkNote(step.plan)}. Call achieve again with the same arguments plus ${args}.`, env,
      `achieve without the plan's unit (${local.model}|${local.executor})`);
  }
  const prompt = String(input.prompt || "");
  if (input.subagent_type === DELEGATOR && (!key || prompt.includes(key))) return null;
  return deny(step, `StealthLab's model plan runs this step on ${local.model}, an open model a Claude subagent cannot ` +
    `run${pOkNote(step.plan)}. Call the ${payload.tool_name} tool with subagent_type: "${DELEGATOR}" and the same ` +
    `task, adding this line to the prompt: "Run it with ${args}." It runs in a worktree, is verified by the node's ` +
    `check and reports to the plan; apply the verified run afterwards.`, env, `asked ${input.subagent_type || "default"}, plan says ${local.model}|${local.executor}`);
}

export function onClaudePreToolUse(payload, { env = process.env } = {}) {
  const mode = guardMode(env);
  const tool = payload?.tool_name;
  if (mode === "off" || !(SUBAGENT_TOOLS.has(tool) || tool === ACHIEVE_TOOL)) return null;
  const step = currentStep(env, payload.session_id);
  if (!step) return null;
  const alias = claudeAlias(step.plan.current);
  const input = payload.tool_input && typeof payload.tool_input === "object" ? payload.tool_input : {};
  if (!alias) {
    const local = executorFor(step.plan.current, env);
    return local ? onOpenModelStep(payload, step, local, input, mode, env) : null;   // nothing local can run it
  }
  if (tool === ACHIEVE_TOOL) return null;                           // a Claude step: achieve is the caller's own choice
  if (String(input.model || "").toLowerCase() === alias) return null;
  if (mode === "rewrite") {
    logHook(env, "route-subagent", `rewrite model -> ${alias}`);
    return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow",
                                   updatedInput: { ...input, model: alias } } };
  }
  return deny(step, `StealthLab's model plan for this task runs this step on ${alias}${pOkNote(step.plan)}. ` +
    `Call the ${tool} tool again with the same arguments and model: "${alias}".`, env,
    `asked ${input.model || "default"}, plan says ${alias}`);
}

function jsonTexts(raw) {
  const texts = [];
  const walk = (v) => {
    if (typeof v === "string") texts.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") { if (typeof v.text === "string") texts.push(v.text); else Object.values(v).forEach(walk); }
  };
  walk(raw);
  const out = [];
  for (const t of texts) {
    try {
      const j = JSON.parse(t);
      if (j && typeof j === "object") out.push(j);
    } catch { /* not JSON */ }
  }
  return out;
}

// The JSON a report_result reply carries, from a PostToolUse payload (direct call or use_tool), or null.
export function reportReply(payload) {
  const name = String(payload?.tool_name || "");
  const viaUseTool = name.endsWith("__use_tool");
  if (viaUseTool && payload?.tool_input?.name !== "report_result") return null;
  if (!viaUseTool && !name.endsWith("__report_result")) return null;
  return jsonTexts(payload?.tool_response ?? payload?.tool_output).find((j) => "next_model" in j || "status" in j) || null;
}

// A finished executor run of the plan's step (run_result), as a report_result-shaped reply, or null. The runtime
// already reported every attempt; this only moves the guard. A run still going, or another plan's run, is ignored.
export function runReply(payload, plan) {
  if (payload?.tool_name !== RUN_RESULT_TOOL) return null;
  const r = jsonTexts(payload?.tool_response ?? payload?.tool_output).find((j) => "state" in j);
  if (!r || !["verified", "failed", "timed_out"].includes(r.state)) return null;
  if (!plan?.instance_key || r.evidence?.instance_key !== plan.instance_key) return null;
  if (r.state === "verified") return { status: "accepted" };
  return typeof r.next_model === "string" ? { status: "rejected", next_model: r.next_model } : { status: "rejected" };
}

// After report_result: follow the ladder (next_model), or stop guarding once a result passed.
export function onPlanReport(payload, { env = process.env } = {}) {
  const step = currentStep(env, payload?.session_id);
  if (!step) return null;
  const reply = reportReply(payload) || runReply(payload, step.plan);
  if (!reply) return null;
  if (reply.status === "accepted") step.plan.done = true;
  else if (typeof reply.next_model === "string") {
    step.plan.current = reply.next_model;
    step.plan.denials = 0;
  } else if (reply.status === "rejected") step.plan.done = true;    // no further rung recommended
  writeJson(step.file, step.session);
  logHook(env, "route-report", step.plan.done ? "plan finished" : `next ${step.plan.current}`);
  return step.plan;
}

// --- Cursor -----------------------------------------------------------------------------------

export function onCursorPreToolUse(payload, { env = process.env } = {}) {
  if (guardMode(env) === "off" || payload?.tool_name !== "Task") return {};
  const step = currentStep(env, payload.conversation_id);
  if (!step) return {};
  const input = payload.tool_input && typeof payload.tool_input === "object" ? payload.tool_input : {};
  if (!("model" in input)) return {};                               // nothing a hook can set
  const want = String(step.plan.current).split("|")[0];
  const asked = String(input.model || "");
  if (asked && (asked === want || asked.includes(want) || want.includes(asked))) return {};
  const denials = Number(step.plan.denials || 0);
  if (denials >= MAX_DENIALS) return {};
  step.plan.denials = denials + 1;
  writeJson(step.file, step.session);
  return { permission: "deny", agent_message: `StealthLab's model plan runs this step on ${want}. Start the Task again with model "${want}".` };
}

// --- settings.json (Claude Code) ------------------------------------------------------------

const isOursFor = (mark) => (group) => (group?.hooks || []).some((h) => {
  const cmd = String(h?.command || "").trimEnd();
  return cmd.includes("stealthlab-mcp") && cmd.endsWith(mark);
});

function shellJoin(spec) {
  return [spec.command, ...spec.args].map((a) => (/[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a)).join(" ");
}

export function addRouteHooks(doc, launch, timeoutSec = 10) {
  doc.hooks = doc.hooks || {};
  for (const [event, { matcher, mark }] of Object.entries(ROUTE_HOOKS)) {
    const groups = (doc.hooks[event] || []).filter((g) => !isOursFor(mark)(g));
    groups.push({ matcher, hooks: [{ type: "command", command: shellJoin({ command: launch.command, args: [...launch.args, ...mark.split(" ")] }), timeout: timeoutSec }] });
    doc.hooks[event] = groups;
  }
  return doc;
}

export function removeRouteHooks(doc) {
  let changed = false;
  for (const [event, { mark }] of Object.entries(ROUTE_HOOKS)) {
    const before = doc?.hooks?.[event];
    if (!Array.isArray(before)) continue;
    const after = before.filter((g) => !isOursFor(mark)(g));
    if (after.length === before.length) continue;
    changed = true;
    if (after.length) doc.hooks[event] = after;
    else delete doc.hooks[event];
  }
  return changed;
}

// CLI entry: `hook route-subagent` (prints the decision JSON, or nothing) and `hook route-report`.
export function runRouteHook(event, { stdinText, env = process.env } = {}) {
  let payload;
  try { payload = JSON.parse(stdinText || "{}"); } catch { return null; }
  try {
    if (event === "route-subagent") return onClaudePreToolUse(payload, { env });
    if (event === "route-report") { onPlanReport(payload, { env }); return null; }
  } catch (err) {
    logHook(env, event, err.message);
  }
  return null;
}
