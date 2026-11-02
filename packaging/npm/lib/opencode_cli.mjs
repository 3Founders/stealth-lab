// `kell opencode [args...]`: OpenCode's CLI with kell's saved sign-in token refreshed first.
//
// The plugin refreshes the token at its own start (lib/opencode/lib/mcp_token.mjs), but a connect that is already in
// flight fails with 401 while it does: a session started about an hour after the sign-in failed its first turn at
// kell's MCP connect, and only a rerun worked. Running `kell opencode` instead of `opencode` means the token is fresh
// (and OpenCode's config header realigned) before OpenCode even boots, so the first turn does not fail.
// Part C of docs/new_user_setup.md uses it.
import { spawnSync } from "node:child_process"
import { refreshMcpHeader } from "./opencode/lib/mcp_token.mjs"

/** Refresh kell's token, then run `opencode` with the same arguments. Returns opencode's exit code (1 if it did not
 *  start). The refresh never blocks or fails the run: any error is ignored and OpenCode runs either way. */
export async function runOpenCodeCli(argv, { env = process.env, spawn = spawnSync, stderr = process.stderr } = {}) {
  try {
    const r = await refreshMcpHeader({ env })
    if (r?.changed) stderr.write(`kell: sign-in token fresh (${r.reason}); starting OpenCode\n`)
  } catch { /* never blocks OpenCode */ }
  const r = spawn("opencode", [...(argv || [])], { stdio: "inherit", shell: process.platform === "win32" })
  return r?.status ?? 1
}
