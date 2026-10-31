// kell inside OpenCode as a plugin (spike). Pinned to the V2 plugin API: @opencode/plugin 2.2.5.
//
// Layer 1 runs the hooks of a Claude Code settings.json (kell's `hook-prompt`, `hook capture-tool`, ... included) with the
// same JSON on stdin and the same stdout / exit-code rules, so one install of kell serves both agents.
// Layer 2 (native.mjs, KELL_OC_NATIVE=on) adds what hooks cannot do: pick the model per prompt, escalate when tests fail,
// put tool suggestions on the session and record each turn.
//
// Honest scope: OpenCode's prompt hook has no failure channel, so a blocked prompt is replaced by a note telling the model
// to stop and say why, not refused outright. MCP tool names are matched best-effort (see tool_names.mjs).
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { Plugin } from "@opencode/plugin"
import { loadHooks, selectHooks, settingsFiles } from "./lib/settings.mjs"
import { runHook, parseOutput, mergeOutputs } from "./lib/run_hook.mjs"
import { claudeName, claudeInput, openCodeInput, claudeResponse } from "./lib/tool_names.mjs"

const DEBUG = process.env.KELL_OC_DEBUG === "1"
const stateDir = () => path.join(process.env.KELL_OC_STATE_DIR || path.join(os.homedir(), ".stealthlab", "opencode"))
const debug = (...a) => { if (DEBUG) try { fs.appendFileSync(path.join(stateDir(), "plugin.log"), `${new Date().toISOString()} ${a.join(" ")}\n`) } catch { /* best effort */ } }

export default Plugin.define({
  id: "kell",
  setup: async (ctx) => {
    const cwd = String(ctx.location?.directory || process.cwd())
    const hooks = loadHooks(settingsFiles(cwd))
    const sessions = new Map()                       // sessionID -> { started, context: [], continued, model }
    const state = (id) => { let s = sessions.get(id); if (!s) { s = { started: false, context: [], continued: 0, model: undefined }; sessions.set(id, s) } return s }
    let mcpServers = []
    try { mcpServers = (await ctx.mcp.list()).map((x) => String(x.name ?? x.id ?? x[0] ?? "")).filter(Boolean) } catch { /* no MCP list: names pass through */ }

    const transcriptFile = (id) => {
      const dir = path.join(stateDir(), "transcripts")
      try { fs.mkdirSync(dir, { recursive: true }) } catch { /* best effort */ }
      return path.join(dir, `${id}.jsonl`)
    }
    // A transcript in Claude Code's JSONL shape (timestamped user and assistant lines): kell's hooks read the session start,
    // the earlier turns and the model from it. Without timestamps its answer path and change tracking did not run.
    const note = (id, entry) => { try { fs.appendFileSync(transcriptFile(id), JSON.stringify({ sessionId: id, cwd, timestamp: new Date().toISOString(), ...entry }) + "\n") } catch { /* best effort */ } }
    const unquote = (t) => String(t || "").replace(/^"([\s\S]*)"$/, "$1")      // the CLI passes the message wrapped in quotes
    const base = (id, event) => ({ session_id: id, transcript_path: transcriptFile(id), cwd, hook_event_name: event })
    const env = { ...process.env, CLAUDE_PROJECT_DIR: cwd }

    async function fire(event, subject, payload) {
      const todo = selectHooks(hooks, event, subject)
      if (!todo.length) return null
      const outs = await Promise.all(todo.map(async (h) => {
        const r = await runHook({ command: h.command, payload, timeoutMs: h.timeoutMs, cwd, env })
        debug(event, subject, `code=${r.code}`, `out=${r.stdout.length}b`, r.timedOut ? "TIMEOUT" : "")
        return parseOutput(event, r)
      }))
      return mergeOutputs(outs)
    }

    // --- SessionStart + UserPromptSubmit -> session.prompt; additionalContext is added to the session's system text.
    await ctx.session.hook("prompt", async (e) => {
      const s = state(e.sessionID)
      s.context = []
      s.continued = 0
      note(e.sessionID, { type: "user", message: { role: "user", content: unquote(e.prompt.text) } })
      if (!s.started) {
        s.started = true
        const start = await fire("SessionStart", "startup", { ...base(e.sessionID, "SessionStart"), source: "startup" })
        if (start) s.context.push(...start.additionalContext)
      }
      const merged = await fire("UserPromptSubmit", "", { ...base(e.sessionID, "UserPromptSubmit"), prompt: unquote(e.prompt.text) })
      if (!merged) return
      if (merged.decision === "deny") {
        // Claude Code shows a block's reason to the person and runs no model. Here a model must speak, so it is told to
        // pass the reason on word for word (kell uses this for answers a cheap model already wrote).
        e.prompt.text = merged.reason
          ? `Reply to the user with exactly the following text and nothing else. Do not use any tool.

${merged.reason}`
          : "A hook blocked this request. Tell the user it was blocked. Do nothing else."
        return
      }
      s.context.push(...merged.additionalContext)
    })
    await ctx.session.hook("context", (e) => {
      const s = sessions.get(e.sessionID)
      if (s) for (const text of s.context) e.system.push({ type: "text", text })
    })
    await ctx.session.hook("model.request", (e) => {
      if (e.kind !== "primary") return
      const s = state(e.sessionID)
      s.model = e.model.id
      note(e.sessionID, { type: "assistant", message: { role: "assistant", model: e.model.id } })
    })

    // --- PreToolUse -> tool.execute.before: deny throws (the call fails with the reason); updatedInput rewrites.
    await ctx.tool.hook("execute.before", async (e) => {
      const name = claudeName(e.tool, mcpServers)
      const merged = await fire("PreToolUse", name, { ...base(e.sessionID, "PreToolUse"), tool_name: name, tool_input: claudeInput(e.tool, e.input), tool_use_id: e.id })
      if (!merged) return
      if (merged.decision === "deny") throw new Error(merged.reason || "blocked by a PreToolUse hook")
      if (merged.updatedInput) e.input = openCodeInput(e.tool, e.input, merged.updatedInput)
      // "ask" has no counterpart here: OpenCode's own permission rules decide whether to ask.
    })

    // --- PostToolUse -> tool.execute.after: context and block feedback are appended to the tool's result.
    await ctx.tool.hook("execute.after", async (e) => {
      if (e.status !== "completed") return
      const name = claudeName(e.tool, mcpServers)
      const merged = await fire("PostToolUse", name, {
        ...base(e.sessionID, "PostToolUse"), tool_name: name, tool_input: claudeInput(e.tool, e.input), tool_use_id: e.id,
        tool_response: claudeResponse(e.tool, e.result),
      })
      if (!merged) return
      const extra = [...merged.additionalContext]
      if (merged.decision === "deny" && merged.reason) extra.push(`Hook feedback: ${merged.reason}`)
      if (!extra.length) return
      const add = extra.map((text) => ({ type: "text", text }))
      const c = e.result.content
      e.result.content = Array.isArray(c) ? [...c, ...add] : typeof c === "string" && c ? [{ type: "text", text: c }, ...add] : add
    })

    // --- Stop -> experimental.idle: a block keeps the session going with the reason as the next message (twice at most).
    await ctx.session.hook("experimental.idle", async (e) => {
      const s = state(e.sessionID)
      const merged = await fire("Stop", "", { ...base(e.sessionID, "Stop"), stop_hook_active: s.continued > 0 })
      if (!merged || merged.decision !== "deny" || s.continued >= 2) return
      s.continued += 1
      await ctx.session.synthetic({ sessionID: e.sessionID, text: merged.reason || "Continue.", delivery: "steer" })
    })

    if (process.env.KELL_OC_NATIVE === "on") {
      const { installNative } = await import("./native.mjs")
      await installNative(ctx, { cwd, state, base })
    }
  },
})
