// Runs one command hook the way Claude Code does: the event as JSON on stdin; stdout, stderr and the exit code read back.
import { spawn } from "node:child_process"

export function runHook({ command, payload, timeoutMs = 60_000, cwd, env = process.env }) {
  return new Promise((resolve) => {
    let stdout = "", stderr = "", done = false, timedOut = false
    const child = spawn(command, { shell: true, cwd, env, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] })
    const finish = (code) => { if (!done) { done = true; clearTimeout(timer); resolve({ code, stdout, stderr, timedOut }) } }
    const timer = setTimeout(() => { timedOut = true; try { child.kill() } catch { /* already gone */ } finish(null) }, timeoutMs)
    child.stdout.on("data", (d) => { stdout += d })
    child.stderr.on("data", (d) => { stderr += d })
    child.on("error", () => finish(null))
    child.on("close", (code) => finish(code))
    child.stdin.on("error", () => { /* the hook did not read its input */ })
    child.stdin.end(JSON.stringify(payload) + "\n")
  })
}

/**
 * Claude Code's output rules for one finished hook:
 *  exit 2 -> block, stderr is the reason; other non-zero exits and timeouts -> ignored (non-blocking error);
 *  exit 0 -> JSON on stdout is read for decisions; plain text counts as context for UserPromptSubmit and SessionStart.
 */
export function parseOutput(event, { code, stdout = "", stderr = "", timedOut = false }) {
  const out = { decision: "allow", reason: undefined, additionalContext: [], updatedInput: undefined, systemMessage: undefined, stop: false }
  if (timedOut || code === null) return out
  if (code === 2) { out.decision = "deny"; out.reason = stderr.trim() || "blocked by hook"; return out }
  if (code !== 0) return out
  const text = stdout.trim()
  if (!text) return out
  if (text.startsWith("{")) {
    let j
    try { j = JSON.parse(text) } catch { j = undefined }
    if (j && typeof j === "object") {
      const h = j.hookSpecificOutput && typeof j.hookSpecificOutput === "object" ? j.hookSpecificOutput : {}
      if (h.hookEventName && h.hookEventName !== event) return out                    // output for another event: ignored
      if (typeof h.additionalContext === "string" && h.additionalContext) out.additionalContext.push(h.additionalContext)
      if (h.permissionDecision === "deny") { out.decision = "deny"; out.reason = h.permissionDecisionReason || j.reason }
      else if (h.permissionDecision === "ask") { out.decision = "ask"; out.reason = h.permissionDecisionReason }
      if (j.decision === "block") { out.decision = "deny"; out.reason = j.reason || out.reason }
      if (h.updatedInput && typeof h.updatedInput === "object") out.updatedInput = h.updatedInput
      if (typeof j.systemMessage === "string") out.systemMessage = j.systemMessage
      if (j.continue === false) out.stop = true
      return out
    }
  }
  if (event === "UserPromptSubmit" || event === "SessionStart") out.additionalContext.push(text)
  return out
}

/** Several hooks for one event: the most restrictive decision wins, contexts add up, the last updatedInput wins. */
export function mergeOutputs(outputs) {
  const rank = { allow: 0, ask: 1, deny: 2 }
  const m = { decision: "allow", reason: undefined, additionalContext: [], updatedInput: undefined, systemMessage: undefined, stop: false }
  for (const o of outputs) {
    if (rank[o.decision] > rank[m.decision]) { m.decision = o.decision; m.reason = o.reason }
    m.additionalContext.push(...o.additionalContext)
    if (o.updatedInput !== undefined) m.updatedInput = o.updatedInput
    if (o.systemMessage) m.systemMessage = o.systemMessage
    if (o.stop) m.stop = true
  }
  return m
}
