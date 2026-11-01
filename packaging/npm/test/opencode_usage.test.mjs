// The per-turn usage sent as kell_usage on OpenCode's experimental.idle (Stop): which messages the
// sum covers and which fields come out. Shapes: opencode 2.2.5 messages (type, model ref) and the
// older role/info wrapper, which the plugin must still understand.
import test from "node:test"
import assert from "node:assert/strict"
import { turnUsage } from "../lib/opencode/lib/usage.mjs"

// v2.2.5 shape: SessionMessageUser / SessionMessageAssistant from the client contract.
test("one turn: assistant messages after the last user message are summed (tokens, cost, model ref)", () => {
  const messages = [
    { type: "user", text: "fix the failing test" },
    { type: "assistant", model: { providerID: "gc", id: "minimax-m2.7" }, tokens: { input: 1200, output: 80, reasoning: 20, cache: { read: 300, write: 0 } }, cost: 0.002501 },
    { type: "synthetic", text: "Continue." },
    { type: "assistant", model: { providerID: "gc", id: "minimax-m2.7" }, tokens: { input: 1500, output: 100, reasoning: 0, cache: { read: 500, write: 0 } }, cost: 0.001 },
  ]
  const u = turnUsage(messages, "gc/minimax-m2.7")
  assert.equal(u.scaffold, "opencode")
  assert.equal(u.model, "gc/minimax-m2.7") // the real model of the turn's last assistant reply
  assert.equal(u.tokens_in, 2700)
  assert.equal(u.tokens_out, 200) // output + reasoning
  assert.equal(u.tokens_cached, 800)
  assert.equal(u.cost_usd, 0.003501)
})

test("usage before a user message is not counted in the current turn", () => {
  const messages = [
    { type: "user", text: "first" },
    { type: "assistant", tokens: { input: 100, output: 10, reasoning: 0, cache: { read: 0, write: 0 } } },
    { type: "user", text: "second" },
    { type: "assistant", tokens: { input: 400, output: 20, reasoning: 0, cache: { read: 0, write: 0 } } },
  ]
  const u = turnUsage(messages)
  assert.equal(u.tokens_in, 400)
  assert.equal(u.tokens_out, 20)
  assert.ok(!("tokens_cached" in u) || u.tokens_cached === 0)
})

test("no user message at all: the whole list sums (nothing is dropped)", () => {
  const u = turnUsage([{ type: "assistant", tokens: { input: 5, output: 1, reasoning: 0, cache: { read: 0, write: 0 } } }])
  assert.equal(u.tokens_in, 5)
})

test("legacy shape (role + info wrapper) still works; string model from state when messages carry none", () => {
  const messages = [
    { info: { role: "user" } },
    { info: { tokens: { input: 10, output: 2, reasoning: 0, cache: { read: 0 } }, cost: 0.5 } },
  ]
  const u = turnUsage(messages, "gc/deepseek-v3.2")
  assert.equal(u.model, "gc/deepseek-v3.2")
  assert.equal(u.tokens_in, 10)
  assert.equal(u.tokens_out, 2)
  assert.equal(u.cost_usd, 0.5)
})

test("missing tokens/cost are left out instead of guessed as zero", () => {
  const u = turnUsage([{ type: "user", text: "hi" }, { type: "assistant" }], "gc/minimax-m2.7")
  assert.ok(!("tokens_in" in u))
  assert.ok(!("cost_usd" in u))
  assert.equal(u.model, "gc/minimax-m2.7") // passed-in model kept when messages carry none
})

test("input that is not a list (the bug fixed for 2.2.5: context() returns an array) yields scaffold + model only", () => {
  for (const bad of [undefined, null, {}, { messages: [] }]) {
    const u = turnUsage(bad, "gc/minimax-m2.7")
    assert.deepEqual(u, { scaffold: "opencode", model: "gc/minimax-m2.7" })
  }
})
