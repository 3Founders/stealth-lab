// OpenCode tool names and arguments <-> Claude Code's, so settings written for Claude Code match (`Bash`, `Edit|Write`,
// `mcp__server__tool`) and the payloads carry the fields its hooks read (`command`, `file_path`, ...).
const NAMES = {
  shell: "Bash", read: "Read", edit: "Edit", write: "Write", patch: "Edit", glob: "Glob", grep: "Grep",
  webfetch: "WebFetch", websearch: "WebSearch", subagent: "Task", todo: "TodoWrite", skill: "Skill", question: "AskUserQuestion",
}
const IN = {                 // OpenCode argument -> Claude Code argument, per tool
  Bash: { command: "command", timeout: "timeout", description: "description" },
  Read: { path: "file_path", offset: "offset", limit: "limit" },
  Edit: { path: "file_path", oldString: "old_string", newString: "new_string", replaceAll: "replace_all" },
  Write: { path: "file_path", content: "content" },
}

/** mcpServers: names of the MCP servers the session has, to turn `server_tool` into `mcp__server__tool`. */
export function claudeName(name, mcpServers = []) {
  if (NAMES[name]) return NAMES[name]
  for (const s of mcpServers) if (name.startsWith(`${s}_`)) return `mcp__${s}__${name.slice(s.length + 1)}`
  return name
}

export function claudeInput(name, input) {
  const map = IN[claudeName(name)]
  if (!map || !input || typeof input !== "object") return input
  const out = {}
  for (const [k, v] of Object.entries(input)) out[map[k] || k] = v
  return out
}

/** The reverse, for a hook's updatedInput: only the keys the hook returned replace the original's. */
export function openCodeInput(name, original, updated) {
  const map = IN[claudeName(name)]
  const back = map ? Object.fromEntries(Object.entries(map).map(([a, b]) => [b, a])) : {}
  const out = { ...(original && typeof original === "object" ? original : {}) }
  for (const [k, v] of Object.entries(updated || {})) out[back[k] || k] = v
  return out
}

/** A PostToolUse `tool_response` in the shape Claude Code's hooks read: {stdout, stderr} for Bash, {text} otherwise. */
export function claudeResponse(name, result) {
  const text = Array.isArray(result?.content) ? result.content.map((c) => (c && typeof c.text === "string" ? c.text : "")).join("\n")
    : typeof result?.content === "string" ? result.content : ""
  if (claudeName(name) === "Bash") {
    const o = result?.output
    return { stdout: typeof o?.output === "string" ? o.output : text, stderr: "", interrupted: false, exit_code: o?.exit ?? result?.metadata?.exit ?? 0 }
  }
  return { text }
}
