// kell's own small agent as an executor (lib/agent/step_agent.mjs). Unlike the other adapters it launches
// no third-party binary: the child is `node step_agent.mjs`, shipped in this package, so "installed" means only that
// the file is here. It needs a profile (exec.json "profiles") naming an OpenAI-compatible endpoint and the env var
// that holds the user's own key for it; without one it refuses to build a command.
//
// VERIFIED_WITH is this package's own agent (its flags and output are defined in step_agent.mjs and covered by
// test/step_agent.test.mjs), the same way the fake test executor defines its own.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { jsonLines, plainResult, safeTaskArg, scrubEnv, tailText, num } from "./common.mjs";
import { FOREIGN_CREDENTIAL_VARS, KEY_ENV_FOR_CHILD, profileKey } from "./profile.mjs";
import { earlyStopEnabled } from "../agent/stall.mjs";

export const STEP_AGENT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "agent", "step_agent.mjs");
const AGENT_VERSION = "1.0.0";

const adapter = {
  id: "stealth",
  bin: STEP_AGENT,
  VERIFIED_WITH: { version: AGENT_VERSION, date: "2026-10-09", source: "lib/agent/step_agent.mjs (this package; defines its own input and output)" },
  DOCS_SOURCE: "lib/agent/step_agent.mjs",
  AUTO_APPROVE_FLAG: "(none: the agent acts inside the throwaway worktree the runtime gives it)",

  async detect() {
    const installed = fs.existsSync(STEP_AGENT);
    return { installed, version: installed ? AGENT_VERSION : null, bin: installed ? STEP_AGENT : null };
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, profile, limits, transcriptPath } = {}) {
    if (!profile) throw new Error(`the stealth executor needs a profile for model "${model}" in ~/.stealthlab/exec.json (profiles.<model> with openai_base_url and key_env)`);
    if (!profile.openai_base_url) throw new Error(`profile ${profile.name} has no openai_base_url, which the stealth executor needs`);
    const key = profileKey(profile, env);
    const childEnv = { ...scrubEnv(env), [KEY_ENV_FOR_CHILD]: key };
    for (const k of Object.keys(env)) if (FOREIGN_CREDENTIAL_VARS.includes(k.toUpperCase())) childEnv[k] = undefined;
    const input = {
      task: safeTaskArg(task), base_url: profile.openai_base_url, model_id: profile.model_id,
      input_per_mtok: profile.input_per_mtok, output_per_mtok: profile.output_per_mtok,
      ...(profile.cached_input_per_mtok !== undefined ? { cached_input_per_mtok: profile.cached_input_per_mtok } : {}),
      ...(profile.temperature !== undefined ? { temperature: profile.temperature } : {}), ...(profile.top_p !== undefined ? { top_p: profile.top_p } : {}),
      request_extras: profile.request_extras, ...(profile.no_system_role ? { no_system_role: true } : {}),
      // the run stops itself just before its time limit, so its spend is reported (a killed run printed nothing)
      ...(Number(timeoutS) > 60 ? { max_seconds: Number(timeoutS) - 20 } : {}),
      // stop a stuck run early (lib/agent/stall.mjs); the child's env carries no STEALTHLAB_ variable, so it travels here
      early_stop: earlyStopEnabled(env),
      ...(Number(env.STEALTHLAB_EARLY_STOP_STEPS) > 0 ? { early_stop_steps: Number(env.STEALTHLAB_EARLY_STOP_STEPS) } : {}),
      ...(Number(env.STEALTHLAB_EARLY_STOP_ERRORS) > 0 ? { early_stop_errors: Number(env.STEALTHLAB_EARLY_STOP_ERRORS) } : {}),
      // package installs are refused while dependency links are in the worktree (lib/agent/installs.mjs); the child's env carries
      // no STEALTHLAB_ variable, so the switch travels here
      ...((env.STEALTHLAB_EXEC_BLOCK_INSTALLS || "on").toLowerCase() === "off" ? { block_installs: false } : {}),
      // the files the task names are read into the first turn (lib/agent/step_agent.mjs); STEALTHLAB_AGENT_PRELOAD=off turns it off
      ...((env.STEALTHLAB_AGENT_PRELOAD || "on").toLowerCase() === "off" ? { preload: false } : {}),
      // a model that can see (exec.json profile `vision: true`) gets the images the task names in its first turn
      ...(profile.vision ? { vision: true } : {}),
      // each tool call and its output, beside the attempt's logs (lib/exec/runtime.mjs gives the path)
      ...(transcriptPath ? { transcript: transcriptPath } : {}),
      // the history tool: past changes to the code being edited (lib/area_history.mjs); STEALTHLAB_AGENT_HISTORY=on
      ...((env.STEALTHLAB_AGENT_HISTORY || "off").toLowerCase() === "on" ? { history_tool: true } : {}),
      // reproduce, find the cause, fix it there, no finish without a change (lib/agent/step_agent.mjs); STEALTHLAB_AGENT_METHOD=root_cause
      ...((env.STEALTHLAB_AGENT_METHOD || "").toLowerCase() === "root_cause" ? { method: "root_cause" } : {}),
      ...(limits || {}),
    };
    void worktree; void timeoutS;
    return { cmd: process.execPath, args: [STEP_AGENT], env: childEnv, stdinText: JSON.stringify(input) };
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
      ...(f.stalled && typeof f.stalled === "object" ? { stalled: { rule: String(f.stalled.rule || ""), why: String(f.stalled.why || "").slice(0, 300) } } : {}),
    };
  },

  async health() {
    const d = await adapter.detect();
    return d.installed ? { healthy: true, detail: `stealth agent ${AGENT_VERSION} at ${STEP_AGENT}` } : { healthy: false, detail: "step_agent.mjs is missing from this install" };
  },
};

export default adapter;
