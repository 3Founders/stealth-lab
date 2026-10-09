// StealthLab's own small agent as an executor (lib/agent/step_agent.mjs). Unlike the other adapters it launches
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

  buildCommand({ task, model, worktree, timeoutS, env = process.env, profile, limits } = {}) {
    if (!profile) throw new Error(`the stealth executor needs a profile for model "${model}" in ~/.stealthlab/exec.json (profiles.<model> with openai_base_url and key_env)`);
    if (!profile.openai_base_url) throw new Error(`profile ${profile.name} has no openai_base_url, which the stealth executor needs`);
    const key = profileKey(profile, env);
    const childEnv = { ...scrubEnv(env), [KEY_ENV_FOR_CHILD]: key };
    for (const k of Object.keys(env)) if (FOREIGN_CREDENTIAL_VARS.includes(k.toUpperCase())) childEnv[k] = undefined;
    const input = {
      task: safeTaskArg(task), base_url: profile.openai_base_url, model_id: profile.model_id,
      input_per_mtok: profile.input_per_mtok, output_per_mtok: profile.output_per_mtok,
      request_extras: profile.request_extras, ...(profile.no_system_role ? { no_system_role: true } : {}), ...(limits || {}),
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
    };
  },

  async health() {
    const d = await adapter.detect();
    return d.installed ? { healthy: true, detail: `stealth agent ${AGENT_VERSION} at ${STEP_AGENT}` } : { healthy: false, detail: "step_agent.mjs is missing from this install" };
  },
};

export default adapter;
