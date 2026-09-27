// Claude Code adapter. Drives the user's own locally installed `claude` under the
// user's own Claude plan (ToS boundary: see common.mjs). Note: this saves no
// quota; it is a valid scaffold for evidence, not a cost lever. Tool
// pre-approval is only used because the runtime spawns this inside a throwaway
// git worktree (spec constraint 4).
//
// Verified 2026-09-27 against the installed binary (2.1.283 (Claude Code),
// C:\Users\...\.local\bin\claude.exe), `claude --help`, excerpt:
//   -p, --print                   Print response and exit (useful for pipes). ...
//   --output-format <format>      Output format (only works with --print): "text" (default),
//                                 "json" (single result), or "stream-json" (realtime streaming)
//   --model <model>               Model for the current session. Provide an alias ... or a
//                                 model's full name
//   --permission-mode <mode>      (choices: "acceptEdits", "auto", "bypassPermissions",
//                                 "manual", "dontAsk", "plan")
//   --allowedTools, --allowed-tools <tools...>  Comma or space-separated list of tool names to allow
//   --permission-prompts <target> ... "none" (nobody: anything that would prompt is denied
//                                 automatically; the permission mode still decides everything else)
//   --no-session-persistence      Disable session persistence ... (only works with --print)
// Docs: https://code.claude.com/docs/en/headless ("claude -p \"Find and fix the bug in auth.py\"
//   --allowedTools \"Read,Edit,Bash\""; "--permission-mode acceptEdits"; "--permission-prompts none").
//
// The prompt is placed immediately after -p, BEFORE the variadic --allowedTools,
// exactly as in the docs example, so the option parser cannot swallow it.
// We deliberately do NOT use --dangerously-skip-permissions / bypassPermissions.
//
// JSON output (--output-format json): one object; docs: "the text result in the
// `result` field", "the response payload includes `total_cost_usd`", plus
// `session_id` and usage metadata. Parsed fields: result, is_error,
// total_cost_usd, usage.{input_tokens,output_tokens,cache_read_input_tokens,
// cache_creation_input_tokens}. The fixture test/fixtures/executors/claude.json
// is a documented-shape example, not a captured run (a run would spend tokens).
import {
  VERIFIED_DATE, detectBin, healthOf, lastJsonObject, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "claude";
export const CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep";

const adapter = {
  id: "claude",
  bin: BIN,
  VERIFIED_WITH: { version: "2.1.283", date: VERIFIED_DATE, source: "claude --help (2.1.283, installed binary); https://code.claude.com/docs/en/headless" },
  DOCS_SOURCE: "https://code.claude.com/docs/en/headless",
  AUTO_APPROVE_FLAG: `--permission-mode acceptEdits --allowedTools ${CLAUDE_ALLOWED_TOOLS} --permission-prompts none`,

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const args = [
      ...launch.prefix,
      "-p", safeTaskArg(task),
      "--output-format", "json",
      "--no-session-persistence",
      "--permission-mode", "acceptEdits",
      "--permission-prompts", "none",
    ];
    if (model) args.push("--model", String(model));
    args.push("--allowedTools", CLAUDE_ALLOWED_TOOLS); // variadic: kept last
    void worktree; void timeoutS; // cwd = worktree is set by the runtime; no timeout flag
    return { cmd: launch.cmd, args, env: scrubEnv(env), stdinText: undefined };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const o = lastJsonObject(stdout);
    if (!o || (typeof o.result !== "string" && o.type !== "result")) return plainResult(stdout, stderr);
    const finalMessage = tailText(typeof o.result === "string" ? o.result : (o.is_error ? `error: ${o.subtype || "failed"}` : ""));
    const u = o.usage || null;
    const tokens = u && (num(u.input_tokens) !== null || num(u.output_tokens) !== null)
      ? { in: (num(u.input_tokens) || 0) + (num(u.cache_read_input_tokens) || 0) + (num(u.cache_creation_input_tokens) || 0), out: num(u.output_tokens) || 0 }
      : null;
    return { finalMessage, learned: extractLearned(finalMessage), tokens, costUsd: num(o.total_cost_usd) };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
