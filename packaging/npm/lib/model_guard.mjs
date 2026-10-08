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
// A plan step whose model is not a Claude model (an open model through call_model or the executor) is never
// enforced on a Claude Code subagent: the Agent tool cannot run it. STEALTHLAB_MODEL_GUARD=off disables it all.
// State lives in the capture session file (lib/capture_hook.mjs): the plan's ladder and the current step.
import fs from "node:fs";
import { logHook } from "./subagent_hook.mjs";
import { sessionFile } from "./capture_hook.mjs";

export const MAX_DENIALS = 2;
export const SUBAGENT_TOOLS = new Set(["Agent", "Task"]);
export const ROUTE_HOOKS = {
  PreToolUse: { matcher: "Agent|Task", mark: "hook route-subagent" },
  PostToolUse: { matcher: "mcp__stealthlab__report_result|mcp__stealthlab__use_tool", mark: "hook route-report" },
};

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

export function onClaudePreToolUse(payload, { env = process.env } = {}) {
  const mode = guardMode(env);
  if (mode === "off" || !SUBAGENT_TOOLS.has(payload?.tool_name)) return null;
  const step = currentStep(env, payload.session_id);
  if (!step) return null;
  const alias = claudeAlias(step.plan.current);
  const input = payload.tool_input && typeof payload.tool_input === "object" ? payload.tool_input : {};
  if (!alias || String(input.model || "").toLowerCase() === alias) return null;
  if (mode === "rewrite") {
    logHook(env, "route-subagent", `rewrite model -> ${alias}`);
    return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "allow",
                                   updatedInput: { ...input, model: alias } } };
  }
  const denials = Number(step.plan.denials || 0);
  if (denials >= MAX_DENIALS) {
    logHook(env, "route-subagent", `let through after ${denials} refusals`);
    return null;
  }
  step.plan.denials = denials + 1;
  writeJson(step.file, step.session);
  logHook(env, "route-subagent", `refused: asked ${input.model || "default"}, plan says ${alias}`);
  return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny",
    permissionDecisionReason: `StealthLab's model plan for this task runs this step on ${alias}` +
      (step.plan.p_ok?.[step.plan.current] !== undefined ? ` (p_ok ${step.plan.p_ok[step.plan.current]})` : "") +
      `. Call the ${payload.tool_name} tool again with the same arguments and model: "${alias}".` } };
}

// The JSON a report_result reply carries, from a PostToolUse payload (direct call or use_tool), or null.
export function reportReply(payload) {
  const name = String(payload?.tool_name || "");
  const viaUseTool = name.endsWith("__use_tool");
  if (viaUseTool && payload?.tool_input?.name !== "report_result") return null;
  if (!viaUseTool && !name.endsWith("__report_result")) return null;
  const raw = payload?.tool_response ?? payload?.tool_output;
  const texts = [];
  const walk = (v) => {
    if (typeof v === "string") texts.push(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") { if (typeof v.text === "string") texts.push(v.text); else Object.values(v).forEach(walk); }
  };
  walk(raw);
  for (const t of texts) {
    try {
      const j = JSON.parse(t);
      if (j && typeof j === "object" && ("next_model" in j || "status" in j)) return j;
    } catch { /* not JSON */ }
  }
  return null;
}

// After report_result: follow the ladder (next_model), or stop guarding once a result passed.
export function onPlanReport(payload, { env = process.env } = {}) {
  const reply = reportReply(payload);
  if (!reply) return null;
  const step = currentStep(env, payload.session_id);
  if (!step) return null;
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
