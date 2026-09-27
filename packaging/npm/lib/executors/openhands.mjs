// OpenHands CLI adapter. UNVERIFIED: `openhands` is not installed on the machine
// this was built on (checked 2026-09-27: `where openhands` finds nothing), so the
// flags come from the official docs only and VERIFIED_WITH is null. The runtime's
// gate (common.mjs runnable()) therefore refuses to run it.
//
// Docs (fetched 2026-09-27):
//   https://docs.openhands.dev/openhands/usage/cli/headless
//     openhands --headless -t "Your task here"
//     --json   Enables structured JSONL output streaming
//     "Headless mode always runs in `always-approve` mode. The agent will execute
//      all actions without any confirmation."   <- so worktree (or container) is mandatory
//     JSONL lines like {"type": "action", "action": "write", "path": "app.py", ...}
//                      {"type": "observation", "content": "File created successfully", ...}
//   https://docs.openhands.dev/openhands/usage/cli/command-reference
//     model: env LLM_MODEL (and LLM_API_KEY) applied with --override-with-envs.
// There is no documented final-message event, so parseOutput takes a "finish"
// action's message if present, else the last event carrying message/content text.
//
// ToS boundary: user's own install and own LLM key only (see common.mjs). We set
// LLM_MODEL only when the caller names a model; we never set or read LLM_API_KEY.
import {
  detectBin, healthOf, jsonLines, launchOrThrow, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "openhands";

const adapter = {
  id: "openhands",
  bin: BIN,
  VERIFIED_WITH: null,
  DOCS_SOURCE: "https://docs.openhands.dev/openhands/usage/cli/headless ; https://docs.openhands.dev/openhands/usage/cli/command-reference",
  AUTO_APPROVE_FLAG: "(implicit: --headless always approves)",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const childEnv = scrubEnv(env);
    const args = [...launch.prefix, "--headless", "--json"];
    if (model) {
      childEnv.LLM_MODEL = String(model);
      args.push("--override-with-envs");
    }
    args.push("--task", safeTaskArg(task));
    void worktree; void timeoutS;
    return { cmd: launch.cmd, args, env: childEnv, stdinText: undefined };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const events = jsonLines(stdout);
    if (!events.length) return plainResult(stdout, stderr);
    let finalMessage = "";
    for (const e of events) {
      const text = [e.message, e.final_thought, e.content, e.text].find((v) => typeof v === "string" && v.trim());
      if (e.action === "finish" || e.type === "finish") { if (text) finalMessage = text; continue; }
      if (text && (e.type === "message" || e.source === "agent" || e.type === "observation" || e.type === "action")) finalMessage = text;
    }
    finalMessage = tailText(finalMessage);
    return { finalMessage, learned: extractLearned(finalMessage), tokens: null, costUsd: null };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
