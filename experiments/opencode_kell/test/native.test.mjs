import test from "node:test"
import assert from "node:assert/strict"
import { isTestCommand, isHard, parseModel, chooseModel, nextEscalation, addableRemote } from "../plugin/native.mjs"

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
