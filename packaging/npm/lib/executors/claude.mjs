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
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  VERIFIED_DATE, detectBin, healthOf, lastJsonObject, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";
import { FOREIGN_CREDENTIAL_VARS, profileKey } from "./profile.mjs";

const BIN = "claude";
export const CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep";

// Open-model mode (a profile with anthropic_base_url): the same `claude -p`, but pointed at a third party's
// Anthropic-compatible endpoint with THAT provider's key (the pattern Z.ai documents:
// https://docs.z.ai/scenario-example/develop-tools/claude -- ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN,
// API_TIMEOUT_MS, ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL).
//
// Three safeguards, because this child talks to someone who is not Anthropic:
//  * every Anthropic / Claude / other-vendor credential is removed from the child's env, so the user's own keys
//    are never sent to the third party;
//  * CLAUDE_CONFIG_DIR points at an empty private directory, so the user's logged-in Claude session and their
//    hooks (including StealthLab's own) are neither presented to the third party nor re-entered by this run;
//  * all three model aliases are mapped to the open model, because Claude Code makes background calls on its
//    small-model alias and a provider that does not know that name would fail them.
// Claude Code itself is still the user's own installed binary. We do not bundle, wrap or redistribute it.
export function openModelEnv(profile, env = process.env, { configDir } = {}) {
  const out = {};
  for (const k of Object.keys(env)) {
    if (FOREIGN_CREDENTIAL_VARS.includes(k.toUpperCase()) || /^ANTHROPIC_/i.test(k) || /^CLAUDE_CODE_OAUTH/i.test(k)) out[k] = undefined;
  }
  const dir = configDir || fs.mkdtempSync(path.join(os.tmpdir(), "stealth-claude-cfg-"));
  return {
    ...out,
    ANTHROPIC_BASE_URL: profile.anthropic_base_url,
    ANTHROPIC_AUTH_TOKEN: profileKey(profile, env),
    ANTHROPIC_DEFAULT_HAIKU_MODEL: profile.model_id,
    ANTHROPIC_DEFAULT_SONNET_MODEL: profile.model_id,
    ANTHROPIC_DEFAULT_OPUS_MODEL: profile.model_id,
    API_TIMEOUT_MS: env.API_TIMEOUT_MS || "3000000",
    CLAUDE_CONFIG_DIR: dir,
  };
}

const adapter = {
  id: "claude",
  bin: BIN,
  VERIFIED_WITH: { version: "2.1.283", date: VERIFIED_DATE, source: "claude --help (2.1.283, installed binary); https://code.claude.com/docs/en/headless" },
  DOCS_SOURCE: "https://code.claude.com/docs/en/headless",
  AUTO_APPROVE_FLAG: `--permission-mode acceptEdits --allowedTools ${CLAUDE_ALLOWED_TOOLS} --permission-prompts none`,

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin, profile, configDir } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    if (profile && !profile.anthropic_base_url) {
      throw new Error(`profile ${profile.name} has no anthropic_base_url, so the claude executor cannot reach it (use opencode or stealth for its openai_base_url)`);
    }
    if (profile && Object.keys(profile.request_extras).length) {
      throw new Error(`profile ${profile.name} sets request_extras, which the claude executor cannot send; use the stealth executor for it`);
    }
    const args = [
      ...launch.prefix,
      "-p", safeTaskArg(task),
      "--output-format", "json",
      "--no-session-persistence",
      "--permission-mode", "acceptEdits",
      "--permission-prompts", "none",
    ];
    if (profile) args.push("--model", profile.model_id);
    else if (model) args.push("--model", String(model));
    args.push("--allowedTools", CLAUDE_ALLOWED_TOOLS); // variadic: kept last
    void worktree; void timeoutS; // cwd = worktree is set by the runtime; no timeout flag
    // With a profile the overlay both adds the open-model variables and blanks (undefined) the user's own vendor
    // credentials; the runtime's childEnv applies an overlay entry of undefined as a delete.
    return { cmd: launch.cmd, args, env: profile ? { ...scrubEnv(env), ...openModelEnv(profile, env, { configDir }) } : scrubEnv(env), stdinText: undefined };
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
