// Dispatch for Cursor: the cheap open models try the task BEFORE Cursor's own model runs, as the Claude Code
// prompt hook does (lib/hook.mjs + lib/dispatch.mjs). See docs/cursor_dispatch.md for what Cursor allows.
//
// Cursor's beforeSubmitPrompt hook can stop a prompt ({"continue": false}) and show the user a message
// ("user_message"), and its time limit is set per hook in hooks.json. So, when the local executor is installed
// (`install --client cursor --with-exec`: "dispatch": true in ~/.stealthlab/config.json and models in exec.json),
// the prompt hook:
//   1. looks the task up (find_ways with a model plan: my_model = Cursor's model, candidates = the open models),
//   2. runs dispatch() -- unchanged, the same function the Claude Code hook calls -- with a check,
//   3. a verified change applied: {"continue": false, "user_message": "Done by <model> ..."}; Cursor's model never runs;
//      otherwise: {"continue": true}, and what Kel knows (plus what was tried) reaches the agent at its first tool
//      call through the postToolUse hook, as without dispatch.
// Any error: {"continue": true} (fail open). Without the executor installed, the prompt hook behaves as before
// (an instant answer and a background lookup; lib/cursor_hooks.mjs).
//
// The check: STEALTHLAB_DISPATCH_CHECK, or the check of the library entry the lookup matched (dispatchCheck in
// lib/dispatch.mjs). No check, no dispatch -- finding the check automatically is a separate fix
// (docs/abtest_followups_2026-10.md, queued fix 1); this module picks it up when lib/dispatch.mjs does.
import fs from "node:fs";
import path from "node:path";
import { configDir, readConfig } from "./config.mjs";
import { callFindWays, callTriage, ensureDispatchRoot, execCandidates, formatKnowledge, hookPolicy, readClaims, routingArgs,
  shouldLookUp } from "./hook.mjs";
import { parseOverride, sessionTurns, turnsText } from "./brief.mjs";
import { rememberLookup } from "./capture_hook.mjs";
import { findStealthRoot, requestPayload, upsertRoutes } from "./library.mjs";
import { logHook } from "./subagent_hook.mjs";
import { routingTimer } from "./routing_timing.mjs";

const idOk = (id) => /^[A-Za-z0-9_-]{1,128}$/.test(String(id || ""));
const workspaceOf = (payload) => (Array.isArray(payload?.workspace_roots) && payload.workspace_roots[0]) || payload?.cwd || "";

// Dispatch runs in Cursor's prompt hook only when the executor is installed (its open models are plan candidates)
// and neither STEALTHLAB_DISPATCH nor STEALTHLAB_CURSOR_DISPATCH is "off".
export function cursorDispatchEnabled(env = process.env) {
  if ((env.STEALTHLAB_CURSOR_DISPATCH || "on").toLowerCase() === "off") return false;
  if ((env.STEALTHLAB_DISPATCH || "on").toLowerCase() === "off") return false;
  return execCandidates(env).length > 0;
}

// Cursor's model slug as the plan knows it. Cursor names Claude models "claude-4.5-sonnet[-thinking]"; the plan
// (and Claude Code, for the test writer) knows "claude-sonnet-4-5". "auto"/"default" is not a model: then
// STEALTHLAB_CURSOR_MODEL names the model to plan against, else there is no plan (and no dispatch).
export function cursorPlanModel(payload, env = process.env) {
  // the env var wins; otherwise the model `kell setup --cursor-model` saved (so no environment variable is needed)
  let saved = "";
  try { saved = String(readConfig(env).cursor_model || ""); } catch { /* an unreadable config: no saved model */ }
  const named = String(env.STEALTHLAB_CURSOR_MODEL || saved || "").trim();
  if (named) return named;
  const m = String(payload?.model || payload?.model_id || "").trim();
  if (!m || /^(auto|default)$/i.test(m)) return null;
  const c = /^claude-(\d+(?:\.\d+)?)-(opus|sonnet|haiku)(?:-thinking)?$/i.exec(m);
  return c ? `claude-${c[2].toLowerCase()}-${c[1].replace(/\./g, "-")}` : m;
}

function writeState(env, conversationId, state) {
  if (!idOk(conversationId)) return;
  const file = path.join(configDir(env), "hooks", "cursor", `${conversationId}.json`);
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(state), { mode: 0o600 });
}

// -> the hook's stdout object, or null when dispatch does not apply here (the caller then runs the plain hook).
// How long routing takes (lib/routing_timing.mjs; off unless STEALTHLAB_ROUTING_TIMING=1): one line per prompt.
export async function onBeforeSubmitPromptDispatch(payload, opts = {}) {
  const timer = routingTimer({ env: opts.env || process.env, prompt: String(payload?.prompt || ""), surface: "cursor" });
  try {
    return await cursorDispatchBody(payload, { ...opts, timer });
  } finally {
    timer.finish();
  }
}

async function cursorDispatchBody(payload, { env = process.env, settings, userAgent, fetchImpl,
                                             now = Date.now(), dispatchImpl, log = () => {}, timer }) {
  if (!cursorDispatchEnabled(env) || !settings?.url || !idOk(payload?.conversation_id)) return null;
  const policy = hookPolicy(env);
  if (!shouldLookUp(payload.prompt, policy)) return null;
  const model = cursorPlanModel(payload, env);
  if (!model) return null;
  const workspace = workspaceOf(payload);
  // dispatch needs .kell/ (routing, library): a git repo seen for the first time gets it now (as in Claude Code)
  const root = workspace ? (findStealthRoot(workspace) || await ensureDispatchRoot(workspace)) : null;
  if (!root) return null;                       // not a git repo: the plain hook instead
  // overrides (lib/brief.mjs): `!opus` leaves this prompt to Cursor's own model; `!cheap` forces a dispatch attempt.
  // Cursor cannot rewrite the prompt, so its model still sees the prefix.
  const override = parseOverride(String(payload.prompt), env);
  if (override.kind === "opus") return null;
  const prompt = String(override.kind === "cheap" ? override.prompt : payload.prompt).trim();
  const ids = { session_id: payload.conversation_id, prompt_id: payload.generation_id || null, cwd: workspace };
  const generation = payload.generation_id || null;
  // the plain hook's state for this conversation: a new prompt never inherits the last one's knowledge
  writeState(env, payload.conversation_id, { generation_id: generation, status: "empty", at: now, waited: true });

  let planOnly = false;
  // the server's judgment (JEV first): a lookup worth it? and, for dispatch, a change asked for now? (the earlier turns
  // are its context when Cursor names a transcript)
  const turns = sessionTurns({ transcriptPath: payload.transcript_path, sessionId: payload.conversation_id, current: prompt, env });
  timer.mark("triage_start");
  const verdict = await callTriage({ url: settings.url, token: settings.token, userAgent, query: prompt, context: turnsText(turns),
    timeoutMs: policy.triageTimeoutMs, fetchImpl });
  timer.mark("triage_end");
  timer.set("triage", verdict ? { work: verdict.work || null, needs_retrieval: verdict.needs_retrieval ?? null } : null);
  if (policy.triage) {
    if (verdict && verdict.needs_retrieval === false) {
      // no knowledge needed -- but a quick fix may still be a task for the cheap models: the lookup goes on, for the
      // model plan only, and nothing it returns reaches Cursor's context unless a dispatch happened (as in Claude Code)
      try { rememberLookup(ids, null, { env }); } catch { /* capture is best-effort */ }
      planOnly = true;
    }
  }
  let extra = {};
  try { extra = requestPayload(root, { env }); } catch { /* no library rows: a plain lookup */ }
  // the plan is over this machine's open models; Cursor's own model is the baseline and the last rung
  extra = { ...extra, ...routingArgs({ ...policy, candidates: execCandidates(env) }, model, "cursor") };
  timer.set("stealth_root", true);
  timer.mark("find_ways_start");
  let reply = await callFindWays({ url: settings.url, token: settings.token, userAgent, query: prompt.slice(0, 1500),
    repoClaims: readClaims(workspace, prompt), timeoutMs: policy.timeoutMs, extra, fetchImpl });
  timer.mark("find_ways_end");
  timer.set("plan_ms", Number.isFinite(reply?.model_plan?.plan_ms) ? reply.model_plan.plan_ms : null);
  timer.set("plan_status", reply?.model_plan?.status || null);
  try {
    if (Array.isArray(reply?.routing_rows) && reply.routing_rows.length) upsertRoutes(root, reply.routing_rows);
  } catch { /* routing.md is best-effort */ }

  const mine = `${model}|cursor`;
  const run = dispatchImpl || (await import("./dispatch.mjs")).dispatch;
  const { triedNote } = await import("./dispatch.mjs");
  timer.mark("dispatch_start");
  const d = await run({ payload: { prompt, cwd: workspace, ...(payload.transcript_path ? { transcript_path: payload.transcript_path } : {}) },
    reply, root, mine, env, fetchImpl, log, force: override.kind === "cheap", triage: verdict, timing: timer });
  timer.mark("dispatch_end");
  timer.set("outcome", d?.handled ? "dispatched: a cheap model passed" : d ? "dispatched: none passed" : "not dispatched");
  if (d?.handled) {
    try { rememberLookup(ids, null, { env }); } catch { /* capture is best-effort */ }
    log(`dispatch: handled by ${d.unit}`);
    return { continue: false, user_message: d.text };
  }
  if (planOnly && !d) return { continue: true };   // looked up for the plan only, and nothing was tried
  // Not handled: Cursor's own model does the task. As in Claude Code, the plan it is shown is itself only (no
  // hand-overs inside the session), and what Kel knows arrives at the agent's first tool call (postToolUse).
  let shown = reply;
  if (reply?.model_plan?.status === "ok") {
    reply = { ...reply, model_plan: { ...reply.model_plan, ladder: [mine] } };
    shown = { ...reply, model_plan: undefined };
  }
  try { rememberLookup(ids, reply, { env, mine }); } catch { /* capture is best-effort */ }
  const context = [triedNote(d), formatKnowledge(shown, policy.maxChars, { mode: policy.mode, root, mine })]
    .filter(Boolean).join("\n\n");
  writeState(env, payload.conversation_id, context
    ? { generation_id: generation, status: "ready", context, at: now, waited: true }
    : { generation_id: generation, status: "empty", at: now, waited: true });
  return { continue: true };
}

// The hook's entry for beforeSubmitPrompt with dispatch: never throws, never blocks on an error.
export async function runCursorDispatchPrompt(payload, opts = {}) {
  const env = opts.env || process.env;
  try {
    return await onBeforeSubmitPromptDispatch(payload, opts);
  } catch (err) {
    logHook(env, "cursor-dispatch", `skipped (${err.message})`);
    return { continue: true };
  }
}
