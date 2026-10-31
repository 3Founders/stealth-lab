// kell inside OpenCode as a plugin (installed by `kell setup --client opencode`, see lib/opencode_setup.mjs). Pinned to the V2 plugin API: @opencode/plugin 2.2.5.
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
import { turnUsage } from "./lib/usage.mjs"

const DEBUG = process.env.KELL_OC_DEBUG === "1"
const stateDir = () => path.join(process.env.KELL_OC_STATE_DIR || path.join(os.homedir(), ".stealthlab", "opencode"))
// The settings `kell setup --client opencode` writes (~/.stealthlab/opencode/config.json): mode, cheap, strong, settings (hook
// files). Environment variables still win, for experiments.
const ocConfig = (() => {
  try {
    const file = process.env.KELL_OC_CONFIG || path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "opencode", "config.json")
    return JSON.parse(fs.readFileSync(file, "utf8")) || {}
  } catch { return {} }
})()
const debug = (...a) => { if (DEBUG) try { fs.appendFileSync(path.join(stateDir(), "plugin.log"), `${new Date().toISOString()} ${a.join(" ")}\n`) } catch { /* best effort */ } }

export default Plugin.define({
  id: "kell",
  setup: async (ctx) => {
    debug("setup: start")
    debug("env names:", Object.keys(process.env).filter((k) => /CLAUDE|ANTHROPIC|OPENCODE|XDG|^NODE|STEALTH/i.test(k)).sort().join(","))
    for (const dom of [ctx.session, ctx.tool]) {      // timestamps around each registration, to see where a stalled start waits
      const orig = dom.hook.bind(dom)
      dom.hook = async (name, cb, ...rest) => { debug("setup: registering", name); const r = await orig(name, cb, ...rest); debug("setup: registered", name); return r }
    }
    const cwd = String(ctx.location?.directory || process.cwd())
    const hooks = loadHooks(!process.env.KELL_OC_SETTINGS && Array.isArray(ocConfig.settings) && ocConfig.settings.length ? ocConfig.settings : settingsFiles(cwd))
    const sessions = new Map()                       // sessionID -> { started, context: [], continued, model }
    const state = (id) => { let s = sessions.get(id); if (!s) { s = { started: false, context: [], continued: 0, model: undefined }; sessions.set(id, s) } return s }
    // Not awaited in setup: asking the host for its MCP servers while plugins are still loading never returned (the cold-start
    // hang, found 2026-10-31: runs with this plugin stalled with no output, the same runs without it took 14 s). Looked up on the
    // first tool call instead, with a time limit; names pass through when it does not answer.
    let mcpServers = [], mcpAsked = false
    const servers = async () => {
      if (!mcpAsked) {
        mcpAsked = true
        try {
          const list = await Promise.race([ctx.mcp.list(), new Promise((_, no) => setTimeout(() => no(new Error("mcp.list timed out")), 2000))])
          mcpServers = list.map((x) => String(x.name ?? x.id ?? x[0] ?? "")).filter(Boolean)
        } catch { /* no MCP list: names pass through */ }
      }
      return mcpServers
    }

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
    // Which mode a session uses is a setting, not code: "session" (default) runs the whole session on the cheap model chosen by
    // kell's triage and escalates on failed tests (native.mjs); "dispatch" keeps kell's own flow (the hook's dispatch and answer
    // paths try cheap models in worktrees and the session's model takes over).  plugin option `mode`, or KELL_OC_MODE.
    const mode = String(ctx.options?.mode || process.env.KELL_OC_MODE || ocConfig.mode || "session").toLowerCase() === "dispatch" ? "dispatch" : "session"
    debug("mode:", mode)
    const env = { ...process.env, CLAUDE_PROJECT_DIR: cwd, ...(mode === "session" ? { STEALTHLAB_DISPATCH: "off", STEALTHLAB_ANSWERS: "off" } : {}) }

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
      s.model = `${e.model.providerID}/${e.model.id}`
      note(e.sessionID, { type: "assistant", message: { role: "assistant", model: e.model.id } })
    })

    // --- PreToolUse -> tool.execute.before: deny throws (the call fails with the reason); updatedInput rewrites.
    await ctx.tool.hook("execute.before", async (e) => {
      const name = claudeName(e.tool, await servers())
      const merged = await fire("PreToolUse", name, { ...base(e.sessionID, "PreToolUse"), tool_name: name, tool_input: claudeInput(e.tool, e.input), tool_use_id: e.id })
      if (!merged) return
      if (merged.decision === "deny") throw new Error(merged.reason || "blocked by a PreToolUse hook")
      if (merged.updatedInput) e.input = openCodeInput(e.tool, e.input, merged.updatedInput)
      // "ask" has no counterpart here: OpenCode's own permission rules decide whether to ask.
    })

    // --- PostToolUse -> tool.execute.after: context and block feedback are appended to the tool's result.
    await ctx.tool.hook("execute.after", async (e) => {
      if (e.status !== "completed") return
      const name = claudeName(e.tool, await servers())
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
    // The turn's model, tokens and cost go along as kell_usage (kell's capture-stop puts them in report_model_run).
    await ctx.session.hook("experimental.idle", async (e) => {
      const s = state(e.sessionID)
      let messages = []
      try { messages = (await Promise.race([ctx.session.context({ sessionID: e.sessionID }), new Promise((ok) => setTimeout(() => ok(null), 2000))]))?.messages || [] } catch { /* optional */ }
      const kell_usage = turnUsage(messages, s.model)
      debug("usage", JSON.stringify(kell_usage))
      const merged = await fire("Stop", "", { ...base(e.sessionID, "Stop"), stop_hook_active: s.continued > 0, kell_usage })
      if (!merged || merged.decision !== "deny" || s.continued >= 2) return
      s.continued += 1
      await ctx.session.synthetic({ sessionID: e.sessionID, text: merged.reason || "Continue.", delivery: "steer" })
    })

    if (mode === "session") {
      const { installNative } = await import("./native.mjs")
      await installNative(ctx, { cwd, state, base, cheap: ocConfig.cheap, strong: ocConfig.strong })
    }
  },
})
