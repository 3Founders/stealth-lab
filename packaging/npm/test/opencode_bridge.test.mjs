import test from "node:test"
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { loadHooks, selectHooks } from "../lib/opencode/lib/settings.mjs"
import { runHook, parseOutput, mergeOutputs } from "../lib/opencode/lib/run_hook.mjs"
import { claudeName, claudeInput, openCodeInput, claudeResponse } from "../lib/opencode/lib/tool_names.mjs"

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "kell-oc-"))

test("hooks from several settings files are added together; matchers select by Claude Code tool name", () => {
  const d = tmp()
  const a = path.join(d, "a.json"), b = path.join(d, "b.json")
  fs.writeFileSync(a, JSON.stringify({ hooks: { PostToolUse: [{ matcher: "Bash|PowerShell", hooks: [{ type: "command", command: "one", timeout: 15 }] }] } }))
  fs.writeFileSync(b, JSON.stringify({ hooks: { PostToolUse: [{ hooks: [{ type: "command", command: "two" }] }], Stop: [{ hooks: [{ type: "command", command: "s" }] }] } }))
  const h = loadHooks([a, path.join(d, "missing.json"), b])
  assert.deepEqual(selectHooks(h, "PostToolUse", "Bash").map((x) => x.command), ["one", "two"])
  assert.deepEqual(selectHooks(h, "PostToolUse", "Read").map((x) => x.command), ["two"])
  assert.equal(selectHooks(h, "PostToolUse", "Bash")[0].timeoutMs, 15000)
  assert.equal(selectHooks(h, "Stop").length, 1)
  assert.equal(selectHooks(h, "PreToolUse", "Bash").length, 0)
})

test("a hook gets the event on stdin and its stdout is read back", async () => {
  const r = await runHook({ command: `node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.stringify({got:JSON.parse(s).prompt})))"`, payload: { prompt: "hi" } })
  assert.equal(r.code, 0)
  assert.deepEqual(JSON.parse(r.stdout), { got: "hi" })
})

test("exit 2 blocks with stderr as the reason; other failures and timeouts are ignored", async () => {
  const blocked = parseOutput("PreToolUse", await runHook({ command: `node -e "console.error('no');process.exit(2)"`, payload: {} }))
  assert.equal(blocked.decision, "deny"); assert.equal(blocked.reason, "no")
  const failed = parseOutput("PreToolUse", await runHook({ command: `node -e "process.exit(1)"`, payload: {} }))
  assert.equal(failed.decision, "allow")
  const slow = parseOutput("PreToolUse", await runHook({ command: `node -e "setTimeout(()=>{},5000)"`, payload: {}, timeoutMs: 200 }))
  assert.equal(slow.decision, "allow")
})

test("JSON output: permission deny, block, additionalContext, updatedInput; another event's output is dropped", () => {
  const ok = (stdout) => ({ code: 0, stdout, stderr: "" })
  const deny = parseOutput("PreToolUse", ok(JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: "nope" } })))
  assert.equal(deny.decision, "deny"); assert.equal(deny.reason, "nope")
  assert.equal(parseOutput("Stop", ok(JSON.stringify({ decision: "block", reason: "keep going" }))).decision, "deny")
  const ctxOut = parseOutput("UserPromptSubmit", ok(JSON.stringify({ hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "note" } })))
  assert.deepEqual(ctxOut.additionalContext, ["note"])
  const upd = parseOutput("PreToolUse", ok(JSON.stringify({ hookSpecificOutput: { hookEventName: "PreToolUse", updatedInput: { command: "ls" } } })))
  assert.deepEqual(upd.updatedInput, { command: "ls" })
  assert.deepEqual(parseOutput("PreToolUse", ok(JSON.stringify({ hookSpecificOutput: { hookEventName: "Stop", additionalContext: "x" } }))).additionalContext, [])
  assert.deepEqual(parseOutput("UserPromptSubmit", ok("plain text")).additionalContext, ["plain text"])
  assert.deepEqual(parseOutput("PostToolUse", ok("plain text")).additionalContext, [])
})

test("merging: the most restrictive decision wins and contexts add up", () => {
  const m = mergeOutputs([
    { decision: "allow", additionalContext: ["a"], stop: false },
    { decision: "deny", reason: "r", additionalContext: ["b"], stop: false },
    { decision: "ask", additionalContext: [], stop: false },
  ])
  assert.equal(m.decision, "deny"); assert.equal(m.reason, "r"); assert.deepEqual(m.additionalContext, ["a", "b"])
})

test("tool names and arguments map both ways", () => {
  assert.equal(claudeName("shell"), "Bash")
  assert.equal(claudeName("stealthlab_find_ways", ["stealthlab"]), "mcp__stealthlab__find_ways")
  assert.equal(claudeName("something_else", ["stealthlab"]), "something_else")
  assert.deepEqual(claudeInput("edit", { path: "a.py", oldString: "x", newString: "y" }), { file_path: "a.py", old_string: "x", new_string: "y" })
  assert.deepEqual(openCodeInput("edit", { path: "a.py", oldString: "x", newString: "y" }, { new_string: "z" }), { path: "a.py", oldString: "x", newString: "z" })
  assert.deepEqual(claudeResponse("shell", { output: { exit: 1, output: "boom\n" }, content: [{ type: "text", text: "boom\n" }] }), { stdout: "boom\n", stderr: "", interrupted: false, exit_code: 1 })
  assert.deepEqual(claudeResponse("read", { content: [{ type: "text", text: "a" }, { type: "text", text: "b" }] }), { text: "a\nb" })
})

test("turnUsage sums the last turn's assistant tokens and cost", async () => {
  const { turnUsage } = await import("../lib/opencode/lib/usage.mjs")
  const msgs = [
    { role: "user" }, { role: "assistant", tokens: { input: 50, output: 5 }, cost: 0.1 },
    { role: "user" },
    { role: "assistant", tokens: { input: 1000, output: 200, reasoning: 50, cache: { read: 400 } }, cost: 0.0003 },
    { role: "assistant", tokens: { input: 1100, output: 100 }, cost: 0.0002 },
  ]
  assert.deepEqual(turnUsage(msgs, "gc/minimax-m2.7"),
    { scaffold: "opencode", model: "gc/minimax-m2.7", tokens_in: 2100, tokens_out: 350, tokens_cached: 400, cost_usd: 0.0005 })
  assert.deepEqual(turnUsage([], null), { scaffold: "opencode" })
})
