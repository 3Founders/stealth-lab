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
  VERIFIED_DATE, detectBin, healthOf, lastJsonObject, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, claudeChildEnv, tailText, extractLearned,
} from "./common.mjs";
import { FOREIGN_CREDENTIAL_VARS, profileKey } from "./profile.mjs";

const BIN = "claude";
export const CLAUDE_ALLOWED_TOOLS = "Read,Edit,Write,Bash,Glob,Grep";

// the system prompt for an open model driven by Claude Code (lean mode): how to work on a task in a repository, short
export const LEAN_SYSTEM = [
  "You are a software engineer working alone in a git repository, in the current directory. Do the task you are given,",
  "completely, by changing the repository's files; nobody will answer questions. Work this way: find the relevant code",
  "first (Grep, Glob, Read only what you need -- use offset and limit for long files); understand the cause before",
  "editing; make the smallest change that does what the task asks, in the style of the surrounding code; then run the",
  "relevant tests or a quick check with Bash and fix what fails (run them from this directory, as the project does: `python",
  "-I` ignores this directory and runs the installed copy, not your change). Do not add new files unless the task needs them, do not",
  "reformat unrelated code, and never edit a test to hide a failure your change causes. When the task changes a behaviour",
  "that an existing test asserts, the test is out of date: update it to the new behaviour. Use the tools",
  "directly; several independent reads or searches may go in one turn. Check with your reproduction and the tests of the",
  "module you changed; run the whole test suite at most once. As soon as those pass, stop: do not look for linters or",
  "other tools that are not installed, and do not re-run checks that already passed (a run that kept checking a finished",
  "fix ran out of time). When done, reply with two or three sentences on what you changed and how you checked it.",
].join(" ");

// The root-cause method (lib/agent/step_agent.mjs's, for the same failures: cheap models edited the right file and patched the
// symptom, or never edited): added to LEAN_SYSTEM with STEALTHLAB_AGENT_METHOD=root_cause.
export const LEAN_METHOD = [
  "Method: reproduce the wrong behaviour first with a tiny script or command and run it. Find the cause, not the symptom:",
  "follow the wrong value back to the first place it goes wrong and fix it there, even when the error is raised elsewhere.",
  "Fix it the way this code already does things (its helpers and patterns; `git log -p -L` or `git log -p -- <file>` shows",
  "how this code was changed before). Do not hide the problem: no try/except or early return that only silences it, no",
  "special case for the example's values. Rerun the reproduction, then the module's tests. A reproduction script alone is",
  "not a fix: change the project's own files. Once you have a likely cause, edit it instead of re-reading the same code.",
].join(" ");
export const leanSystem = (env = process.env) =>
  (String(env.STEALTHLAB_AGENT_METHOD || "").toLowerCase() === "root_cause" ? `${LEAN_SYSTEM} ${LEAN_METHOD}` : LEAN_SYSTEM);

// Open-model mode (a profile with anthropic_base_url): the same `claude -p`, but pointed at a third party's
// Anthropic-compatible endpoint with THAT provider's key (the pattern Z.ai documents:
// https://docs.z.ai/scenario-example/develop-tools/claude -- ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN,
// API_TIMEOUT_MS, ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL).
//
// Three safeguards, because this child talks to someone who is not Anthropic:
//  * every Anthropic / Claude / other-vendor credential is removed from the child's env, so the user's own keys
//    are never sent to the third party;
//  * CLAUDE_CONFIG_DIR points at an empty private directory, so the user's logged-in Claude session and their
//    hooks (including kell's own) are neither presented to the third party nor re-entered by this run;
//  * all three model aliases are mapped to the open model, because Claude Code makes background calls on its
//    small-model alias and a provider that does not know that name would fail them.
// Claude Code itself is still the user's own installed binary. We do not bundle, wrap or redistribute it.
// token: what the child sends as its key -- the provider's key (direct), or the bridge's per-process secret (bridged:
// the provider key never enters the child's environment)
export function openModelEnv(profile, env = process.env, { configDir, token } = {}) {
  const out = {};
  for (const k of Object.keys(env)) {
    if (FOREIGN_CREDENTIAL_VARS.includes(k.toUpperCase()) || /^ANTHROPIC_/i.test(k) || /^CLAUDE_CODE_OAUTH/i.test(k)) out[k] = undefined;
  }
  const dir = configDir || fs.mkdtempSync(path.join(os.tmpdir(), "stealth-claude-cfg-"));
  return {
    ...out,
    ANTHROPIC_BASE_URL: profile.anthropic_base_url,
    ANTHROPIC_AUTH_TOKEN: token || profileKey(profile, env),
    ANTHROPIC_DEFAULT_HAIKU_MODEL: profile.model_id,
    ANTHROPIC_DEFAULT_SONNET_MODEL: profile.model_id,
    ANTHROPIC_DEFAULT_OPUS_MODEL: profile.model_id,
    API_TIMEOUT_MS: env.API_TIMEOUT_MS || "3000000",
    CLAUDE_CONFIG_DIR: dir,
    // Anthropic's gateway guide (https://code.claude.com/docs/en/llm-gateway-connect.md): with a custom base URL Claude
    // Code still sends background traffic (updates, telemetry) to Anthropic, so turn it off; and a provider that
    // rejects unrecognised request fields needs the experimental betas off.
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1",
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

  buildCommand(opts = {}) {
    const { profile, env = process.env } = opts;
    // an open model Claude Code cannot reach as it is -- no Anthropic endpoint (General Compute), or request_extras it
    // cannot send (GLM's reasoning_effort) -- goes through the local bridge (lib/anthropic_bridge.mjs) to its OpenAI
    // endpoint: async, only in this case. STEALTHLAB_CLAUDE_BRIDGE=off: the direct behaviour below.
    const extras = profile && Object.keys(profile.request_extras || {}).length;
    if (profile?.openai_base_url && (!profile.anthropic_base_url || extras) && (env.STEALTHLAB_CLAUDE_BRIDGE || "on").toLowerCase() !== "off") {
      return this.buildBridged(opts);
    }
    return this.buildDirect(opts);
  },

  async buildBridged({ task, worktree, timeoutS, env = process.env, bin, profile, configDir, runTag = null } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    profileKey(profile, env);                       // the provider key must be set (it stays in this process)
    const { bridgeFor } = await import("../anthropic_bridge.mjs");
    const b = await bridgeFor(profile, { env, runTag });
    const overlay = openModelEnv({ ...profile, anthropic_base_url: b.baseUrl }, env, { configDir, token: b.secret });
    overlay[profile.key_env] = undefined;           // the provider key's own variable stays out of the child too
    // lean (default): a short system prompt of our own and only the tools it may use defined -- Claude Code's own
    // prompt and its full tool list cost ~15k input tokens on EVERY call, paid at the open model's price
    // (STEALTHLAB_CLAUDE_LEAN=off: Claude Code's own prompt and tools)
    const lean = (env.STEALTHLAB_CLAUDE_LEAN || "on").toLowerCase() !== "off";
    const args = [...launch.prefix, "-p", safeTaskArg(task), "--output-format", "stream-json", "--verbose", "--no-session-persistence",
      "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--setting-sources", "user", "--strict-mcp-config",
      ...(lean ? ["--system-prompt", leanSystem(env), "--tools", CLAUDE_ALLOWED_TOOLS] : []),
      "--model", profile.model_id, "--allowedTools", CLAUDE_ALLOWED_TOOLS];
    void worktree; void timeoutS;
    return { cmd: launch.cmd, args, env: { ...scrubEnv(env), ...overlay }, stdinText: undefined };
  },

  buildDirect({ task, model, worktree, timeoutS, env = process.env, bin, profile, configDir } = {}) {
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
      "--output-format", "stream-json", "--verbose",
      "--no-session-persistence",
      "--permission-mode", "acceptEdits",
      "--permission-prompts", "none",
      // the worktree is a copy of the project, kell hooks included: project settings must not load here, or
      // the run would look the task up and dispatch it again (and no MCP server is needed for one step's work)
      "--setting-sources", "user",
      "--strict-mcp-config",
    ];
    if (profile) args.push("--model", profile.model_id);
    else if (model) args.push("--model", String(model));
    args.push("--allowedTools", CLAUDE_ALLOWED_TOOLS); // variadic: kept last
    void worktree; void timeoutS; // cwd = worktree is set by the runtime; no timeout flag
    // With a profile the overlay both adds the open-model variables and blanks (undefined) the user's own vendor
    // credentials; the runtime's childEnv applies an overlay entry of undefined as a delete.
    return { cmd: launch.cmd, args, env: profile ? { ...scrubEnv(env), ...openModelEnv(profile, env, { configDir }), STEALTHLAB_INNER_CLAUDE: "1" } : claudeChildEnv(env), stdinText: undefined };
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
