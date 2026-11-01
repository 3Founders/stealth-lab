// The MCP header refresh the OpenCode plugin does at start (lib/opencode/lib/mcp_token.mjs): which token lands in
// OpenCode's opencode.json, when the file is left alone, and that nothing else in it is touched.
import test from "node:test"
import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { refreshMcpHeader, opencodeConfigFilePath } from "../lib/opencode/lib/mcp_token.mjs"

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "kell-mcptok-"))
const NOW = 1_000_000_000

function world({ header = "Bearer at-setup", owned = ["mcp.kell"], oauth = true, expiresAt, extraServer = true } = {}) {
  const home = tmp(), dir = tmp()
  fs.writeFileSync(path.join(home, "config.json"), JSON.stringify({
    ...(oauth ? { oauth: { client_id: "cid", token_endpoint: "https://issuer.test/token", issuer: "https://issuer.test" }, refresh_token: "rt-old" } : {}),
    ...(header !== false ? { token: "at-old" } : {}),
    ...(expiresAt !== false ? { expires_at: expiresAt === undefined ? NOW + 3_600_000 : expiresAt } : {}),
  }))
  fs.mkdirSync(path.join(home, "opencode"))
  fs.writeFileSync(path.join(home, "opencode", "config.json"), JSON.stringify({ mode: "session", cheap: "gc/minimax-m2.7", strong: "gc/deepseek-v3.2", settings: ["x/hooks.json"], owned }))
  const oc = { "$schema": "https://opencode.ai/v2/config.json", "other-key": true,
               mcp: { servers: { ...(extraServer ? { other: { type: "remote", url: "https://x.test/mcp" } } : {}),
                                 ...(header === false ? {} : { kell: { type: "remote", url: "https://mcp.kell.org.in/mcp", codemode: false, headers: { Authorization: header } } }) } } }
  fs.writeFileSync(path.join(dir, "opencode.json"), JSON.stringify(oc, null, 2) + "\n")
  return { env: { STEALTHLAB_HOME: home, OPENCODE_CONFIG_DIR: dir }, home, dir }
}

const granting = (out) => (url, init) => {
  const body = new URLSearchParams(init.body) // the module sends the stringified form, like lib/oauth.mjs
  assert.equal(body.get("grant_type"), "refresh_token")
  assert.equal(body.get("refresh_token"), "rt-old")
  assert.equal(body.get("client_id"), "cid")
  return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(out) })
}

test("a fresh saved token with a matching header: nothing is written and no refresh is asked for", async () => {
  const w = world({ header: "Bearer at-old" }) // header === the saved token
  let called = 0
  const r = await refreshMcpHeader({ env: w.env, now: () => NOW, fetchImpl: () => { called += 1; return granting({ access_token: "x" })(undefined, { body: "" }) } })
  assert.equal(called, 0)
  assert.deepEqual(r, { changed: false, reason: "same" })
  assert.equal(JSON.parse(fs.readFileSync(path.join(w.dir, "opencode.json"), "utf8")).mcp.servers.kell.headers.Authorization, "Bearer at-old")
})

test("a fresh saved token with an older header is realigned without a network call; the rest of the file is preserved", async () => {
  const w = world({ header: "Bearer at-setup" })
  const r = await refreshMcpHeader({ env: w.env, now: () => NOW, fetchImpl: () => { throw new Error("must not be called") } })
  const { expires_at, ...out } = r
  assert.deepEqual(out, { changed: true, reason: "realign" })
  assert.equal(expires_at, NOW + 3_600_000)
  const oc = JSON.parse(fs.readFileSync(path.join(w.dir, "opencode.json"), "utf8"))
  assert.equal(oc.mcp.servers.kell.headers.Authorization, "Bearer at-old")
  assert.equal(oc.mcp.servers.other.url, "https://x.test/mcp")
  assert.equal(oc["other-key"], true)
  assert.equal(oc["$schema"], "https://opencode.ai/v2/config.json")
})

test("an expired token goes through the refresh-token grant, rotates the saved pair and rewrites the header", async () => {
  const w = world({ header: "Bearer at-setup", expiresAt: NOW - 60_000 })
  const seen = []
  const r = await refreshMcpHeader({ env: w.env, now: () => NOW, fetchImpl: granting({ access_token: "at-new", refresh_token: "rt-new", expires_in: 3600 }), log: (...m) => seen.push(m.join(" ")) })
  assert.deepEqual(seen, []) // the happy path is quiet
  const { expires_at, ...out } = r
  assert.deepEqual(out, { changed: true, reason: "refreshed" })
  assert.equal(expires_at, NOW + 3_600_000)
  const star = JSON.parse(fs.readFileSync(path.join(w.home, "config.json"), "utf8"))
  assert.equal(star.token, "at-new")
  assert.equal(star.refresh_token, "rt-new")
  const oc = JSON.parse(fs.readFileSync(path.join(w.dir, "opencode.json"), "utf8"))
  assert.equal(oc.mcp.servers.kell.headers.Authorization, "Bearer at-new")
})

test("a failed refresh leaves every file exactly as it was", async () => {
  const w = world({ header: "Bearer at-setup", expiresAt: NOW - 60_000 })
  const fail = () => Promise.resolve({ ok: false, status: 400, text: async () => JSON.stringify({ error: "invalid_grant" }) })
  const r = await refreshMcpHeader({ env: w.env, now: () => NOW, fetchImpl: fail })
  assert.deepEqual(r, { changed: false, reason: "refresh-failed" })
  const oc = JSON.parse(fs.readFileSync(path.join(w.dir, "opencode.json"), "utf8"))
  assert.equal(oc.mcp.servers.kell.headers.Authorization, "Bearer at-setup")
  assert.equal(JSON.parse(fs.readFileSync(path.join(w.home, "config.json"), "utf8")).token, "at-old")
})

test("never rewrites an entry it did not set up or cannot refresh", async () => {
  const a = world({ owned: [], header: "Bearer at-setup", oauth: true, expiresAt: undefined })
  assert.equal((await refreshMcpHeader({ env: a.env, now: () => NOW, fetchImpl: () => { throw new Error("x") } })).reason, "not-owned")

  const b = world({ owned: ["mcp.kell"], oauth: false, header: "Bearer at-setup", expiresAt: NOW - 60_000 })
  assert.equal((await refreshMcpHeader({ env: b.env, now: () => NOW, fetchImpl: () => { throw new Error("x") } })).reason, "no-refresh-state")

  const c = world({ header: false })
  assert.equal((await refreshMcpHeader({ env: c.env, now: () => NOW, fetchImpl: () => { throw new Error("x") } })).reason, "no-kell-entry")

  const d = world({ header: "Bearer at-setup" })
  const r = await refreshMcpHeader({ env: { ...d.env, STEALTHLAB_MCP_TOKEN: "env-token" }, now: () => NOW, fetchImpl: () => { throw new Error("x") } })
  assert.equal(r.reason, "explicit-env-token")
})

test("a custom (non-Bearer) Authorization header is left alone", async () => {
  const w = world({ header: "Basic dXNlcjpwYXNz", expiresAt: NOW - 60_000 })
  const r = await refreshMcpHeader({ env: w.env, now: () => NOW, fetchImpl: () => { throw new Error("x") } })
  assert.equal(r.changed, false)
})

test("no sign-in state at all", async () => {
  const home = tmp(), dir = tmp()
  fs.writeFileSync(path.join(dir, "opencode.json"), "{}")
  const r = await refreshMcpHeader({ env: { STEALTHLAB_HOME: home, OPENCODE_CONFIG_DIR: dir }, now: () => NOW, fetchImpl: () => { throw new Error("x") } })
  assert.deepEqual(r, { changed: false, reason: "no-signin" })
})

test("the config file resolution matches lib/opencode_setup.mjs (OPENCODE_CONFIG_DIR wins over XDG, no default-home writes)", () => {
  assert.equal(opencodeConfigFilePath({ OPENCODE_CONFIG_DIR: "d", XDG_CONFIG_HOME: "x" }), path.join("d", "opencode.json"))
  assert.equal(opencodeConfigFilePath({ XDG_CONFIG_HOME: "x" }), path.join("x", "opencode", "opencode.json"))
  assert.equal(opencodeConfigFilePath({ STEALTHLAB_TEST_HOME: "h" }), path.join("h", ".config", "opencode", "opencode.json"))
})
