// One headless Claude Code call (`claude -p ...`, the prompt on stdin) under the user's own login -> {status, stdout,
// stderr}. Used for one-shot, tool-less calls: the test writer's Sonnet fallback (lib/dispatch.mjs) and the strong
// reviewer (lib/review.mjs). The prompt goes on stdin: it has many lines, and on Windows `claude` is a .cmd shim that
// would cut an argument at the first newline -- launchOrThrow runs Claude Code's own entry point, never through a shell.
import { spawn } from "node:child_process";
import { claudeChildEnv, launchOrThrow } from "./executors/common.mjs";

export function runClaudeAsync(env, args, input, signal, timeoutMs = 180000, cwd = undefined) {
  return new Promise((resolve) => {
    let launch;
    try { launch = launchOrThrow("claude", { env }); } catch (err) { resolve({ status: null, stdout: "", stderr: err.message }); return; }
    const child = spawn(launch.cmd, [...launch.prefix, ...args], { windowsHide: true, env: claudeChildEnv(env), ...(cwd ? { cwd } : {}) });
    let stdout = "", stderr = "";
    const timer = setTimeout(() => child.kill(), timeoutMs);
    const abort = () => child.kill();
    signal?.addEventListener?.("abort", abort);
    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("error", (err) => { stderr += err.message; });
    child.on("close", (status) => { clearTimeout(timer); signal?.removeEventListener?.("abort", abort); resolve({ status, stdout, stderr }); });
    child.stdin.end(input);
  });
}

// the lean, tool-less one-shot: a short system prompt of the caller's, no tools, no project settings, JSON output
export const leanArgs = (model, system) => ["-p", "--model", model, "--system-prompt", system,
  "--tools", "", "--setting-sources", "user", "--strict-mcp-config", "--output-format", "json"];

// the same, with read-only tools: a reviewer that has to look at the repository itself (a finding that nothing needs
// changing is checked against the code, not taken on its word -- lib/review.mjs reviewFinding). Run with cwd = the repo.
export const readOnlyArgs = (model, system) => ["-p", "--model", model, "--system-prompt", system,
  "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob", "--setting-sources", "user", "--strict-mcp-config",
  "--output-format", "json"];
