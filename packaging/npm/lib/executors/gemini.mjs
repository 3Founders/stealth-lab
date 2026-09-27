// Gemini CLI adapter. UNVERIFIED: `gemini` is not installed on the machine this
// was built on (checked 2026-09-27: `where gemini` finds nothing), so the flags
// below come from the official docs only and VERIFIED_WITH is null. The runtime's
// gate (common.mjs runnable()) therefore refuses to run it. To enable: install
// gemini, compare `gemini --help` with the flags below, and fill VERIFIED_WITH.
//
// Docs: https://geminicli.com/docs/cli/cli-reference/ (fetched 2026-09-27):
//   --prompt | -p | string | Prompt text. Appended to stdin input if provided. Forces non-interactive mode.
//   --model | -m | string | auto | Model to use.
//   --output-format | -o | string | text | Choices: text, json, stream-json
//   --approval-mode | - | string | default | Choices: default, auto_edit, yolo, plan
//   --yolo | -y | **Deprecated.** ... Use --approval-mode=yolo instead.
// https://geminicli.com/docs/cli/headless/ JSON output: a single object with
//   response (string, the final answer), stats (token usage and latency), error (optional).
// The exact stats layout is not given in the docs; we read stats.models.<name>.tokens
// {prompt|input, candidates|output} defensively and report null when absent.
//
// ToS boundary: user's own install and own Google login only (see common.mjs).
import {
  detectBin, healthOf, lastJsonObject, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "gemini";

const adapter = {
  id: "gemini",
  bin: BIN,
  VERIFIED_WITH: null,
  DOCS_SOURCE: "https://geminicli.com/docs/cli/cli-reference/ ; https://geminicli.com/docs/cli/headless/",
  AUTO_APPROVE_FLAG: "--approval-mode yolo",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const args = [...launch.prefix, "--output-format", "json", "--approval-mode", "yolo"];
    if (model) args.push("--model", String(model));
    args.push("--prompt", safeTaskArg(task));
    void worktree; void timeoutS;
    return { cmd: launch.cmd, args, env: scrubEnv(env), stdinText: undefined };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const o = lastJsonObject(stdout);
    if (!o || (typeof o.response !== "string" && !o.error)) return plainResult(stdout, stderr);
    const finalMessage = tailText(typeof o.response === "string" ? o.response : `error: ${(o.error && o.error.message) || "failed"}`);
    let tin = 0, tout = 0, saw = false;
    const models = o.stats && o.stats.models && typeof o.stats.models === "object" ? Object.values(o.stats.models) : [];
    for (const m of models) {
      const t = (m && m.tokens) || {};
      const i = num(t.prompt) ?? num(t.input);
      const out = num(t.candidates) ?? num(t.output);
      if (i !== null || out !== null) { saw = true; tin += i || 0; tout += out || 0; }
    }
    return { finalMessage, learned: extractLearned(finalMessage), tokens: saw ? { in: tin, out: tout } : null, costUsd: null };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
