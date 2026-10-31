// A stand-in Claude Code hook: reads the event on stdin, appends it to fake-hook.log, answers by event.
import fs from "node:fs"
let s = ""
process.stdin.on("data", (d) => (s += d)).on("end", () => {
  const e = JSON.parse(s)
  fs.appendFileSync(new URL("./fake-hook.log", import.meta.url), JSON.stringify({ ev: e.hook_event_name, tool: e.tool_name, input: e.tool_input, resp: e.tool_response, prompt: e.prompt, sid: !!e.session_id, tp: !!e.transcript_path, cwd: e.cwd }).slice(0, 600) + "\n")
  const out = (o) => console.log(JSON.stringify(o))
  if (e.hook_event_name === "UserPromptSubmit") out({ hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: "The secret word for this task is PINEAPPLE. Say it in your final answer." } })
  if (e.hook_event_name === "PreToolUse" && /forbidden/.test(JSON.stringify(e.tool_input))) out({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: "forbidden word blocked by test hook" } })
  if (e.hook_event_name === "PostToolUse" && e.tool_name === "Bash") out({ hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: "PostToolUse hook saw your Bash result." } })
})
