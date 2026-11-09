// Layer 2: what hooks cannot do. Loaded by index.mjs when KELL_OC_NATIVE=on.
//  (a) triage + model: kell's own triage (POST <server>/triage, the call `kell hook-prompt` makes) picks the model per prompt;
//  (b) escalation: two failed test runs on the cheap model switch the session to the strong one;
//  (b2) fallback: a provider failure (the model never answered) is retried once on the same model, then the session walks
//       to the next rung of the ladder (config.json's fallbacks, else its fallback, default gc/minimax-m2.7). A model the
//       account may not use (an allowlist, no access, an unknown model) moves at once; no rung is tried twice in a turn;
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
      headers: { accept: "application/json", "content-type": "application/json", "user-agent": "kell-opencode/0.1", ...(token ? { authorization: `Bearer ${token}` } : {}) },
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

// A refusal that names the MODEL, not the request: the organization's allowlist, the provider account, or a model
// that does not exist. OpenCode reports these as AI.Error.Authentication with only the sentence, so there is no HTTP
// status to read; another rung of the ladder may answer.
export const MODEL_DENIED = /not on this organization'?s allowlist|no connection available to you offers|model_not_(allowed|found)|\bmodel\b[^.\n]{0,140}\b(not found|does not exist|doesn'?t exist|not available|is not available|not supported|is not supported|not enabled|is not enabled|deactivated|deprecated|retired|not permitted|not allowed|is not allowed)\b|(no|not have|don'?t have|do not have|lacks?) (access|permission) to (the )?model|not (authorized|permitted|allowed) to (use|access|call) (the )?model|(unknown|invalid|unsupported|unrecognized) model/i

export function isModelDenial(err) {
  if (!err) return false
  let text = ""
  try { text = JSON.stringify(err?.error ?? err) } catch { text = "" }
  text += " " + String(err?.message || err?.name || "")
  return MODEL_DENIED.test(text)
}

/**
 * Provider-side failures (the model never answered) told apart from every other error. OpenCode's retry hook hands over a
 * SessionError; classify on the text, because the shapes vary (message strings, HTTP status objects, AbortError...).
 * Covers the failures seen live: "Provider request failed with HTTP 403", "Streaming request failed" (a 400 body),
 * 5xx, timeouts, and a model the account may not use (isModelDenial). Not covered on purpose: everything a model or a
 * tool decided by itself.
 */
export function isProviderFailure(err) {
  if (!err) return false
  let text = ""
  try { text = JSON.stringify(err?.error ?? err) } catch { text = "" }
  text += " " + String(err?.message || err?.name || "")
  return isModelDenial(err) || /streaming request failed|http \d{3}|status[: ]?\d{3}|timeout|timed ?out|abort|econn|socket hang up|fetch failed|network/i.test(text)
}

/**
 * What to do after a provider failure, from OpenCode's retry hook. An ordinary outage is tolerated once (the same model
 * is retried); the next failure moves to the next rung. A model the account may not use moves at once: retrying it
 * cannot help. `fallbacks` is the whole ladder, in order; `fallback` is the older single-rung setting. A rung already
 * tried this turn is skipped, so the ladder walks down once and never loops.
 */
export function nextFallback(s, { attempt, model, fallback, fallbacks, failing, denied }) {
  const ladder = (Array.isArray(fallbacks) && fallbacks.length ? fallbacks : fallback ? [fallback] : [])
    .map(String).filter((m) => parseModel(m))
  if (!failing) return { switch: false, reason: "not a provider failure" }
  if (!ladder.length) return { switch: false, reason: "no fallback model" }
  const tried = s.tried instanceof Set ? s.tried : (s.tried = new Set())
  const next = ladder.find((m) => m !== model && !tried.has(m))
  if (!next) return { switch: false, reason: "the ladder is exhausted" }
  if (!denied && Number(attempt || 0) < 2) return { switch: false, reason: "first failure: the same model is retried once" }
  return { switch: true, to: next }
}

/** Remote MCP servers from the router's answer that can be added with no sign-in. Field names are an assumption. */
export function addableRemote(reply, installed = []) {
  const have = new Set(installed)
  return (Array.isArray(reply?.tools) ? reply.tools : []).filter((t) =>
    t && t.source !== "installed" && typeof t.url === "string" && /^https:\/\//.test(t.url) && /^[\w-]{1,40}$/.test(String(t.name || ""))
    && !t.requires_auth && !t.oauth && !t.needs_key && !have.has(t.name)).slice(0, 2)
}

export async function installNative(ctx, { cwd, state, cheap: cheapCfg, strong: strongCfg, fallback: fallbackCfg, fallbacks: fallbackCfgs } = {}) {
  // read on each call, not once: the saved sign-in is refreshed about hourly (the hooks and the desktop app do it), and a session
  // that kept the first one sent every triage and outcome with an expired token (2026-11-08)
  const cfg = { get url() { return readKellConfig().url }, get token() { return readKellConfig().token } }
  const cheap = process.env.KELL_OC_CHEAP_MODEL || cheapCfg || "gc/minimax-m2.7"
  const strong = process.env.KELL_OC_STRONG_MODEL || process.env.KELL_OC_DEFAULT_MODEL || strongCfg || null
  const fallback = process.env.KELL_OC_FALLBACK_MODEL || fallbackCfg || "gc/minimax-m2.7"
  // The whole ladder, in order: KELL_OC_FALLBACK_MODELS, then config.json's fallbacks, then the older single fallback.
  const envFallbacks = String(process.env.KELL_OC_FALLBACK_MODELS || "").split(",").map((m) => m.trim()).filter(Boolean)
  const fallbacks = envFallbacks.length ? envFallbacks
    : Array.isArray(fallbackCfgs) && fallbackCfgs.length ? fallbackCfgs.map(String).filter(Boolean)
      : [fallback]
  const addRemote = process.env.KELL_OC_ADD_REMOTE === "on"
  const log = (...a) => { if (process.env.KELL_OC_DEBUG === "1") try { fs.appendFileSync(path.join(process.env.KELL_OC_STATE_DIR || os.tmpdir(), "native.log"), `${new Date().toISOString()} ${a.join(" ")}\n`) } catch { /* best effort */ } }
  const turns = new Map()            // sessionID -> { which, strongRef, testFails, lastTest, tools: Set, goal, tokens, cost, model, fallbackDone }
  const turn = (id) => { let t = turns.get(id); if (!t) { t = { which: "strong", testFails: 0, tools: new Set() }; turns.set(id, t) } return t }

  await ctx.session.hook("prompt", async (e) => {
    const t = turn(e.sessionID)
    Object.assign(t, { testFails: 0, lastTest: undefined, tools: new Set(), tokens: undefined, cost: undefined, tried: new Set() })
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

  // (b2) Provider failures. OpenCode retries a failed model request by itself and hands each attempt to the retry hook;
  // kell lets the first ordinary failure retry on the same model, and on the next one walks to the next rung of the
  // ladder. A model the account may not use (an allowlist, no access, an unknown model) moves at once: retrying it cannot
  // help. Rungs already tried this turn are skipped, so the ladder walks down once and never loops.
  await ctx.session.hook("retry", async (e) => {
    const t = turn(e.sessionID)
    const model = `${e.model?.providerID ?? "?"}/${e.model?.id ?? "?"}`
    const denied = isModelDenial(e.error)
    const failing = isProviderFailure(e.error)
    const verdict = nextFallback(t, { attempt: e.attempt, model, fallback, fallbacks, failing, denied })
    if (!verdict.switch) {
      if (failing) log("provider failure on", model, "attempt", e.attempt, "-", verdict.reason)
      return
    }
    const ref = parseModel(verdict.to)
    if (!ref) return
    t.tried.add(model)
    t.tried.add(verdict.to)
    log(denied ? "model denied on" : "provider failure on", model, "attempt", e.attempt, "-> fallback", verdict.to)
    try {
      await ctx.session.switchModel({ sessionID: e.sessionID, model: ref })
      e.decision = { retry: true, delay: denied ? 0 : 250 }      // the retry now goes out on the next rung
    } catch (err) { log("switchModel to the fallback failed:", String(err?.message || err)) }
  })

  // (d) one record per finished turn. Tokens and cost come from the assistant message when the host exposes it.
  await ctx.session.hook("experimental.idle", async (e) => {
    const t = turn(e.sessionID)
    let tokens, cost
    try {
      const ctxInfo = await ctx.session.context({ sessionID: e.sessionID })
      // opencode 2.2.5 returns the messages array itself; older builds returned { messages } (index.mjs reads it the same way)
      const last = [...(Array.isArray(ctxInfo) ? ctxInfo : ctxInfo?.messages || [])].reverse().find((m) => m && m.tokens)
      tokens = last?.tokens; cost = last?.cost
    } catch { /* the message API is optional here */ }
    const record = { tool_goal: t.goal || null, suggested: [], used: [...t.tools], outcome: t.lastTest === "pass" ? "accepted" : t.lastTest === "fail" ? "rejected" : "unknown", check_kind: t.lastTest ? "tests" : null }
    log("record", JSON.stringify({ ...record, model: t.model, tokens, cost }))
    if (record.used.length || record.tool_goal) await post(cfg.url, "/tools/outcome", record, { token: cfg.token, timeoutMs: 15000 })
  })
}
