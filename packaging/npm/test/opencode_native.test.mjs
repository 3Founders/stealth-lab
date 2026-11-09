import test from "node:test"
import assert from "node:assert/strict"
import { isTestCommand, isHard, parseModel, chooseModel, nextEscalation, addableRemote, isProviderFailure, isModelDenial, nextFallback, installNative } from "../lib/opencode/native.mjs"

test("test commands are told apart from other shell commands", () => {
  for (const c of ["python -m pytest -q", "npm test", "npm run test -- --watch=false", "go test ./...", "cargo test", "vitest run", "mvn -q test"]) assert.ok(isTestCommand(c), c)
  for (const c of ["echo hi", "git status", "npm install", "ls tests"]) assert.ok(!isTestCommand(c), c)
})

test("model choice: cheap by default, the session's own model for a hard task or no verdict", () => {
  const m = { cheap: "gc/minimax-m2.7", strong: "gc/gemma-4-31B-it" }
  assert.deepEqual(chooseModel({ work: "change", category: "bugfix" }, m), { which: "cheap", ref: "gc/minimax-m2.7" })
  assert.equal(chooseModel({ work: "unclear_change", kind: "repo_specific" }, m).which, "strong")
  assert.equal(chooseModel({ work: "answer", kind: "repo_specific", confidence: 0.85 }, m).which, "cheap")
  assert.equal(chooseModel({ work: "change", kind: "reusable_task", confidence: 0.98, category: "repo_change" }, m).which, "cheap")
  assert.equal(chooseModel(null, m).which, "strong")
  assert.equal(isHard({ work: "change" }), false)
})

test("model refs split at the first slash", () => {
  assert.deepEqual(parseModel("gc/minimax-m2.7"), { providerID: "gc", id: "minimax-m2.7" })
  assert.deepEqual(parseModel("openrouter/anthropic/claude"), { providerID: "openrouter", id: "anthropic/claude" })
  assert.equal(parseModel("nomodel"), null)
})

test("escalation: the second failed test run on the cheap model escalates; a pass resets; non-test commands do nothing", () => {
  const s = { which: "cheap" }
  assert.equal(nextEscalation(s, { isTest: false, failed: true }).escalate, false)
  assert.equal(nextEscalation(s, { isTest: true, failed: true }).escalate, false)
  assert.equal(nextEscalation(s, { isTest: true, failed: false }).escalate, false)
  assert.equal(s.lastTest, "pass")
  assert.equal(nextEscalation(s, { isTest: true, failed: true }).escalate, false)
  assert.equal(nextEscalation(s, { isTest: true, failed: true }).escalate, true)
  const strong = { which: "strong" }
  nextEscalation(strong, { isTest: true, failed: true })
  assert.equal(nextEscalation(strong, { isTest: true, failed: true }).escalate, false)
})

test("only https remote servers with no sign-in are added, never an installed one", () => {
  const reply = { tools: [
    { source: "installed", name: "playwright", url: "https://x.example/mcp" },
    { source: "catalog", name: "plain", url: "https://plain.example/mcp" },
    { source: "catalog", name: "needskey", url: "https://k.example/mcp", needs_key: true },
    { source: "catalog", name: "http-only", url: "http://insecure.example/mcp" },
    { source: "catalog", name: "bad name!", url: "https://b.example/mcp" },
    { source: "catalog", name: "stdio-only", install: "claude mcp add stdio-only -- npx x" },
  ] }
  assert.deepEqual(addableRemote(reply, ["playwright"]).map((t) => t.name), ["plain"])
})

test("provider failures (the model never answered) are told apart from other errors", () => {
  for (const e of [
    "Provider request failed with HTTP 403",
    { message: "Streaming request failed" },
    { error: { message: "request to https://api.generalcompute.com/v1 failed with HTTP 502" } },
    new Error("The operation was aborted due to timeout"),
    { name: "AbortError", message: "This operation was aborted" },
    "fetch failed",
  ]) assert.ok(isProviderFailure(e), JSON.stringify(String(e?.message || e)))
  for (const e of [
    new Error("blocked by a PreToolUse hook"),
    { message: "mean requires at least one value" },
    { message: "3 of 4 tests failed" },
    undefined,
  ]) assert.equal(isProviderFailure(e), false, JSON.stringify(String(e?.message || e)))
})

test("a model the account may not use is a denial: no HTTP status needed", () => {
  for (const e of [
    "AI.Error: model 'kimi-k3' is not on this organization's allowlist",
    { message: "model 'x' is not on this organization's allowlist" },
    { error: { message: "The model `x` does not exist or you do not have access to it" } },
    { message: "no connection available to you offers 'kimi-k3|direct'" },
    { message: "unknown model: foo" },
  ]) assert.ok(isModelDenial(e), JSON.stringify(String(e?.message || e)))
  for (const e of [
    new Error("blocked by a PreToolUse hook"),
    { message: "Streaming request failed" },
    { message: "3 of 4 tests failed" },
    undefined,
  ]) assert.equal(isModelDenial(e), false, JSON.stringify(String(e?.message || e)))
})

test("provider failure fallback: one retry on the same model, then the next rung, never a loop", () => {
  const s = {}
  assert.equal(nextFallback(s, { attempt: 1, model: "gc/deepseek-v3.2", fallback: "gc/minimax-m2.7", failing: true }).switch, false)
  const second = nextFallback(s, { attempt: 2, model: "gc/deepseek-v3.2", fallbacks: ["gc/minimax-m2.7", "gc/gemma-4"], failing: true })
  assert.equal(second.switch, true)
  assert.equal(second.to, "gc/minimax-m2.7")
  s.tried = new Set(["gc/deepseek-v3.2", "gc/minimax-m2.7"])
  const third = nextFallback(s, { attempt: 2, model: "gc/minimax-m2.7", fallbacks: ["gc/minimax-m2.7", "gc/gemma-4"], failing: true })
  assert.equal(third.switch, true)
  assert.equal(third.to, "gc/gemma-4")
  s.tried.add("gc/gemma-4")
  assert.equal(nextFallback(s, { attempt: 2, model: "gc/gemma-4", fallbacks: ["gc/minimax-m2.7", "gc/gemma-4"], failing: true }).switch, false)
  assert.equal(nextFallback({}, { attempt: 5, model: "gc/minimax-m2.7", fallback: "gc/minimax-m2.7", failing: true }).switch, false)
  assert.equal(nextFallback({}, { attempt: 5, model: "gc/minimax-m2.7", fallback: "gc/minimax-m2.7", failing: false }).switch, false)
  assert.equal(nextFallback({}, { attempt: 5, model: "x/y", fallback: undefined, failing: true }).switch, false)
})

test("a denied model moves on at the first attempt; an ordinary outage waits for the retry", () => {
  const s = {}
  const denied = nextFallback(s, { attempt: 1, model: "kell/kimi-k3", fallbacks: ["kell/kimi-k3", "kell/glm-5.3-flash"],
                                   failing: true, denied: true })
  assert.deepEqual(denied, { switch: true, to: "kell/glm-5.3-flash" })
  const outage = nextFallback({}, { attempt: 1, model: "kell/kimi-k3", fallbacks: ["kell/glm-5.3-flash"], failing: true })
  assert.equal(outage.switch, false)
})

test("the retry hook: an ordinary failure retries once, then walks the ladder; a denial moves at once", async () => {
  const hooks = {}
  const switches = []
  const ctx = {
    session: { hook: async (name, cb) => { hooks[name] = cb }, switchModel: async (x) => { switches.push(x) } },
    tool: { hook: async () => {} },
    mcp: {},
  }
  await installNative(ctx, { fallbacks: ["gc/deepseek-v3.2", "gc/minimax-m2.7", "gc/gemma-4"] })
  const ev = (attempt, error = { message: "Streaming request failed" }) => ({ sessionID: "s1", attempt, model: { providerID: "gc", id: "deepseek-v3.2" },
                             error, decision: undefined })
  const e1 = ev(1)
  await hooks.retry(e1)
  assert.equal(switches.length, 0)
  assert.equal(e1.decision, undefined)         // the host's own retry policy stands
  const e2 = ev(2)
  await hooks.retry(e2)
  assert.deepEqual(switches, [{ sessionID: "s1", model: { providerID: "gc", id: "minimax-m2.7" } }])
  assert.deepEqual(e2.decision, { retry: true, delay: 250 })
  const e3 = ev(2)
  e3.model = { providerID: "gc", id: "minimax-m2.7" }
  await hooks.retry(e3)
  assert.deepEqual(switches.at(-1), { sessionID: "s1", model: { providerID: "gc", id: "gemma-4" } })
  const e4 = ev(2)
  e4.model = { providerID: "gc", id: "gemma-4" }
  await hooks.retry(e4)
  assert.equal(switches.length, 2)             // every rung was tried once: no loop
  const e5 = ev(2)
  e5.error = { message: "blocked by a PreToolUse hook" }
  await hooks.retry(e5)
  assert.equal(switches.length, 2)             // a non-provider error changes nothing
})

test("the retry hook switches away from a model the organization does not allow, with no delay", async () => {
  const hooks = {}
  const switches = []
  const ctx = {
    session: { hook: async (name, cb) => { hooks[name] = cb }, switchModel: async (x) => { switches.push(x) } },
    tool: { hook: async () => {} },
    mcp: {},
  }
  await installNative(ctx, { fallbacks: ["kell/kimi-k3", "kell/glm-5.3-flash", "kell/deepseek-v4.1-flash"] })
  const e = { sessionID: "s1", attempt: 1, model: { providerID: "kell", id: "kimi-k3" },
              error: { message: "AI.Error: model 'kimi-k3' is not on this organization's allowlist" }, decision: undefined }
  await hooks.retry(e)
  assert.deepEqual(switches, [{ sessionID: "s1", model: { providerID: "kell", id: "glm-5.3-flash" } }])
  assert.deepEqual(e.decision, { retry: true, delay: 0 })
})
