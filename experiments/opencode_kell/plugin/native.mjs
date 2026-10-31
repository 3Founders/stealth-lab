// Layer 2: what hooks cannot do. Loaded by index.mjs when KELL_OC_NATIVE=on.
//  (a) triage + model: kell's own triage (POST <server>/triage, the call `kell hook-prompt` makes) picks the model per prompt;
//  (b) escalation: two failed test runs on the cheap model switch the session to the strong one;
//  (c) tools: the tool router's suggestions (POST <server>/tools) for the prompt's tool goal, remote servers only;
//  (d) recording: per turn, model, tokens, cost, tools used and the test result go to <server>/tools/outcome.
//
// What is a guess, not a measurement: the remote-server fields the tool router returns (see addableRemote).
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

const TEST_CMD = /\b(pytest|py\.test|unittest|npm (run )?test|pnpm (run )?test|yarn test|vitest|jest|go test|cargo test|mvn( -\w+)* test|gradle(w)? test|dotnet test|rspec|phpunit)\b/i

export const isTestCommand = (cmd) => TEST_CMD.test(String(cmd || ""))

export function readKellConfig(env = process.env) {
  try {
    const home = env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
    const c = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf8"))
    // the server the hook uses: STEALTHLAB_MCP_URL first, then the saved config
    return { url: env.STEALTHLAB_MCP_URL || env.STEALTHLAB_URL || c.url, token: c.token || env.STEALTHLAB_TOKEN }
  } catch { return { url: env.STEALTHLAB_MCP_URL || env.STEALTHLAB_URL, token: env.STEALTHLAB_TOKEN } }
}

const sibling = (mcpUrl, name) => {
  try { const u = new URL(mcpUrl); u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + name; u.search = ""; u.hash = ""; return u.toString() } catch { return null }
}

async function post(url, name, body, { token, timeoutMs = 8000, fetchImpl = globalThis.fetch } = {}) {
  const target = url && sibling(url, name)
  if (!target) return null
  try {
    const res = await fetchImpl(target, {
      method: "POST", signal: AbortSignal.timeout(timeoutMs), body: JSON.stringify(body),
      headers: { accept: "application/json", "content-type": "application/json", "user-agent": "kell-opencode-spike/0.0", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    })
    if (!res.ok) return null
    const parsed = JSON.parse(await res.text())
    return parsed && typeof parsed === "object" ? parsed : null
  } catch { return null }
}

/**
 * What /triage really returns (three live replies, 2026-10-31): {kind, work, confidence, provider, category, needs_retrieval,
 * reason?, tool?}. kind: repo_specific | reusable_task | knowledge_question | trivial_edit | conversation;
 * work: answer | change | conversation | unclear_change; category e.g. code_question, repo_change, writing, frontend. There is NO
 * difficulty field, and confidence is the judge's confidence in the label, not in the task being easy (a plain failing-test fix
 * came back at 0.42, a sweeping redesign at 0.98). So the only honest "send this to the strong model" signals are: no verdict,
 * or work === "unclear_change" (the request itself is not clear enough for a cheap model to act on). Everything else starts
 * cheap, and the failed-test escalation is what catches a task that was too hard.
 */
export function isHard(v) {
  return !v || v.work === "unclear_change"
}

/** "gc/minimax-m2.7" -> { providerID: "gc", id: "minimax-m2.7" } */
export function parseModel(ref) {
  const s = String(ref || "")
  const i = s.indexOf("/")
  return i > 0 ? { providerID: s.slice(0, i), id: s.slice(i + 1) } : null
}

/** Which model for this prompt: the strong one (the session's own) for a hard task or no verdict; the cheap one otherwise. */
export function chooseModel(verdict, { cheap, strong }) {
  if (!verdict || isHard(verdict)) return { which: "strong", ref: strong }
  return { which: "cheap", ref: cheap }
}

/** Two failed test runs on the cheap model are the signal to escalate; a pass resets the count. */
export function nextEscalation(s, { isTest, failed }, limit = 2) {
  if (!isTest) return { escalate: false }
  if (!failed) { s.testFails = 0; s.lastTest = "pass"; return { escalate: false } }
  s.testFails = (s.testFails || 0) + 1
  s.lastTest = "fail"
  return { escalate: s.which === "cheap" && s.testFails >= limit }
}

/** Remote MCP servers from the router's answer that can be added with no sign-in. Field names are an assumption. */
export function addableRemote(reply, installed = []) {
  const have = new Set(installed)
  return (Array.isArray(reply?.tools) ? reply.tools : []).filter((t) =>
    t && t.source !== "installed" && typeof t.url === "string" && /^https:\/\//.test(t.url) && /^[\w-]{1,40}$/.test(String(t.name || ""))
    && !t.requires_auth && !t.oauth && !t.needs_key && !have.has(t.name)).slice(0, 2)
}

export async function installNative(ctx, { cwd, state }) {
  const cfg = readKellConfig()
  const cheap = process.env.KELL_OC_CHEAP_MODEL || "gc/minimax-m2.7"
  const strong = process.env.KELL_OC_STRONG_MODEL || process.env.KELL_OC_DEFAULT_MODEL || null
  const addRemote = process.env.KELL_OC_ADD_REMOTE === "on"
  const log = (...a) => { if (process.env.KELL_OC_DEBUG === "1") try { fs.appendFileSync(path.join(process.env.KELL_OC_STATE_DIR || os.tmpdir(), "native.log"), `${new Date().toISOString()} ${a.join(" ")}\n`) } catch { /* best effort */ } }
  const turns = new Map()            // sessionID -> { which, strongRef, testFails, lastTest, tools: Set, goal, tokens, cost, model }
  const turn = (id) => { let t = turns.get(id); if (!t) { t = { which: "strong", testFails: 0, tools: new Set() }; turns.set(id, t) } return t }

  await ctx.session.hook("prompt", async (e) => {
    const t = turn(e.sessionID)
    Object.assign(t, { testFails: 0, lastTest: undefined, tools: new Set(), tokens: undefined, cost: undefined })
    const verdict = await post(cfg.url, "/triage", { query: String(e.prompt.text).slice(0, 6000) }, { token: cfg.token })
    t.goal = verdict?.tool && verdict.tool !== "-" ? String(verdict.tool) : undefined
    if (strong) t.strongRef = strong            // the session's own model, kept before the switch so escalation can go back to it
    const pick = chooseModel(verdict, { cheap, strong: strong || t.strongRef })
    t.which = pick.which
    const ref = parseModel(pick.ref)
    log("triage", JSON.stringify({ kind: verdict?.kind, work: verdict?.work, category: verdict?.category, tool: verdict?.tool }), "->", pick.which, pick.ref || "(session's own)")
    if (ref) { try { await ctx.session.switchModel({ sessionID: e.sessionID, model: ref }) } catch (err) { log("switchModel failed", String(err?.message || err)) } }
    if (t.goal && addRemote) {
      const reply = await post(cfg.url, "/tools", { need: String(e.prompt.text).slice(0, 4000), goal: t.goal, installed: [], k: 5 }, { token: cfg.token, timeoutMs: 25000 })
      const add = addableRemote(reply)
      if (add.length) {
        await ctx.mcp.transform((mcp) => { for (const x of add) mcp.set(x.name, { type: "remote", url: x.url }) })
        await ctx.mcp.reload()
        log("added remote servers", add.map((x) => x.name).join(","))
      }
    }
  })

  await ctx.session.hook("model.request", (e) => {
    if (e.kind !== "primary") return
    const t = turn(e.sessionID)
    if (!t.strongRef && t.which === "strong") t.strongRef = `${e.model.providerID}/${e.model.id}`
    t.model = `${e.model.providerID}/${e.model.id}`
  })

  await ctx.tool.hook("execute.after", async (e) => {
    const t = turn(e.sessionID)
    t.tools.add(e.tool)
    if (e.tool !== "shell") return
    const cmd = e.input?.command
    const exit = e.status === "completed" ? e.result?.output?.exit ?? e.result?.metadata?.exit : 1
    const verdict = nextEscalation(t, { isTest: isTestCommand(cmd), failed: Number(exit) !== 0 })
    if (verdict.escalate && t.strongRef) {
      const ref = parseModel(t.strongRef)
      t.which = "strong"
      log("escalate after", t.testFails, "failed test runs ->", t.strongRef)
      if (ref) await ctx.session.switchModel({ sessionID: e.sessionID, model: ref })
    }
  })

  // (d) one record per finished turn. Tokens and cost come from the assistant message when the host exposes it.
  await ctx.session.hook("experimental.idle", async (e) => {
    const t = turn(e.sessionID)
    let tokens, cost
    try {
      const ctxInfo = await ctx.session.context({ sessionID: e.sessionID })
      const last = [...(ctxInfo?.messages || [])].reverse().find((m) => m && m.tokens)
      tokens = last?.tokens; cost = last?.cost
    } catch { /* the message API is optional here */ }
    const record = { tool_goal: t.goal || null, suggested: [], used: [...t.tools], outcome: t.lastTest === "pass" ? "accepted" : t.lastTest === "fail" ? "rejected" : "unknown", check_kind: t.lastTest ? "tests" : null }
    log("record", JSON.stringify({ ...record, model: t.model, tokens, cost }))
    if (record.used.length || record.tool_goal) await post(cfg.url, "/tools/outcome", record, { token: cfg.token, timeoutMs: 15000 })
  })
}
