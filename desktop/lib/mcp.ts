// One kell MCP tool call from the desktop's own server routes. The app is already signed in to the hosted MCP server
// (~/.stealthlab/config.json); this speaks MCP's streamable HTTP directly so a route can ask `find_ways` before a prompt,
// the same way the engine's plugin does. The token is refreshed first (lib/signin.ts), because an expired one answers 401.
//
// A fresh MCP session per call: initialize -> notifications/initialized -> tools/call. Two round trips, no shared state,
// and nothing to expire between prompts.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { freshSignIn } from "./signin"
import { webToken } from "./websession"

const config = () => {
  const home = process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
  try {
    const c = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf8")) || {}
    return { url: process.env.STEALTHLAB_MCP_URL || process.env.STEALTHLAB_URL || c.url, token: c.token }
  } catch { return { url: process.env.STEALTHLAB_MCP_URL || process.env.STEALTHLAB_URL, token: undefined } }
}

type Rpc = { status: number; sid?: string | null; json: any; text: string }

async function rpc(url: string, token: string, body: unknown, sid?: string | null, timeoutMs = 8000): Promise<Rpc> {
  const r = await fetch(url, {
    method: "POST", signal: AbortSignal.timeout(timeoutMs),
    headers: {
      "content-type": "application/json", accept: "application/json, text/event-stream",
      ...(token ? { authorization: `Bearer ${token}` } : {}), ...(sid ? { "mcp-session-id": sid } : {}),
    },
    body: JSON.stringify(body),
  })
  const session = r.headers.get("mcp-session-id") || sid || null
  const text = await r.text()
  let json: any = null
  for (const line of text.split("\n")) {
    if (line.startsWith("data: ")) { try { json = JSON.parse(line.slice(6)) } catch { /* keep scanning */ } }
  }
  if (!json) { try { json = JSON.parse(text) } catch { /* not JSON */ } }
  return { status: r.status, sid: session, json, text: json ? "" : text.slice(0, 300) }
}

/** The tool's answer as data: kell's tools return a JSON string in their text content. */
function payload(reply: Rpc): any {
  if (reply.status >= 400) throw Object.assign(new Error(`kell's MCP server answered ${reply.status}`), { status: 502 })
  if (reply.json?.error) throw Object.assign(new Error(String(reply.json.error.message || reply.json.error)), { status: 502 })
  const content = reply.json?.result?.content
  const text = Array.isArray(content) ? content.map((c: any) => String(c?.text || "")).join("") : ""
  if (reply.json?.result?.isError) {
    throw Object.assign(new Error(text.replace(/^REFUSED:\s*/i, "").slice(0, 300) || "the tool failed"), { status: 502 })
  }
  if (!text) return reply.json?.result ?? null
  try { return JSON.parse(text) } catch { return text }
}

/** A kell REST route (not an MCP tool): `/procedure`, `/triage`. The configured `url` is the MCP endpoint
 *  (`https://…/mcp`); these routes live at the host root. Same sign-in and bearer as a tool call.
 *  `method` is POST unless the route is a read: `callKellRoute(path, undefined, t, "GET")` sends no body and no
 *  content-type, which is what the submission routes' GET handlers expect. */
export async function callKellRoute(path: string, body: unknown, timeoutMs = 8000, method: "GET" | "POST" | "DELETE" = "POST"): Promise<any> {
  try { return await routeOnce(path, body, timeoutMs, method) } catch (err: any) {
    if (err?.status !== 401) throw err
    // A 401 with a saved refresh token is a token that went stale, not a person who signed out: refresh it and ask once more.
    await freshSignIn(process.env, fetch, true)
    return await routeOnce(path, body, timeoutMs, method)
  }
}

async function routeOnce(path: string, body: unknown, timeoutMs: number, method: "GET" | "POST" | "DELETE" = "POST"): Promise<any> {
  await freshSignIn()
  const { url, token } = config()
  if (!url) throw Object.assign(new Error("kell is not signed in on this machine"), { status: 401 })
  return rest(url, path, body, timeoutMs, method, token, "kell's sign-in expired; sign in again")
}

/** The message a website-API call without a Rooms session gets. The sign-in lives in the Rooms pane, not in Settings. */
export const SIGN_IN_TO_WEBSITE = "Sign in to kell (Rooms) to use your submissions."

/**
 * A kell route on the WEBSITE API (`/v1/goals/...`: submissions and runs). It takes the person's website session, not
 * the coding sign-in: the MCP-app token is refused there on purpose ("this token was issued to a connected MCP app"),
 * which is what made "My submissions" answer 401 and the panel report a broken sign-in (2026-11-19). The website
 * session is the Rooms sign-in, read from the app's own loopback (lib/websession.ts); it is never written to disk.
 */
export async function callWebRoute(path: string, body: unknown, timeoutMs = 8000, method: "GET" | "POST" | "DELETE" = "POST"): Promise<any> {
  const { url } = config()
  if (!url) throw Object.assign(new Error(SIGN_IN_TO_WEBSITE), { status: 401 })
  const token = await webToken()
  if (!token) throw Object.assign(new Error(SIGN_IN_TO_WEBSITE), { status: 401 })
  return rest(url, path, body, timeoutMs, method, token, SIGN_IN_TO_WEBSITE)
}

/** One REST call to the kell host: the MCP endpoint's origin, the route's path, a bearer, and JSON in and out. */
async function rest(url: string, path: string, body: unknown, timeoutMs: number, method: string,
                    token: string | undefined, unauthorized: string): Promise<any> {
  const base = String(url).replace(/\/mcp\/?$/, "").replace(/\/+$/, "")
  const sendBody = method !== "GET" && body !== undefined
  let r: Response
  try {
    r = await fetch(`${base}${path}`, {
      method, signal: AbortSignal.timeout(timeoutMs),
      headers: {
        accept: "application/json",
        ...(sendBody ? { "content-type": "application/json" } : {}),
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      ...(sendBody ? { body: JSON.stringify(body) } : {}),
    })
  } catch {
    throw Object.assign(new Error("kell's server could not be reached"), { status: 502 })
  }
  if (r.status === 401) throw Object.assign(new Error(unauthorized), { status: 401 })
  const text = await r.text()
  let json: any = null
  try { json = JSON.parse(text) } catch { /* not JSON */ }
  if (r.status >= 400) {
    throw Object.assign(new Error(String(json?.error || `kell's server answered ${r.status}`)), { status: r.status })
  }
  return json ?? text
}

export async function callMcpTool(name: string, args: Record<string, unknown>, timeoutMs = 12_000): Promise<any> {
  let lastErr: any
  // A hosted edge can terminate a long semantic lookup (the transport says "terminated"). One retry on a fresh MCP
  // session is cheap and makes the plan reliable; a 401 is final (the sign-in is gone).
  let refreshed = false
  for (let attempt = 0; attempt < 2; attempt++) {
    try { return await once(name, args, timeoutMs) } catch (err: any) {
      lastErr = err
      if (err?.status === 400) break
      // A 401 is a stale token, not a signed-out person, while a refresh token is saved: refresh it once and try again
      // ("kell's sign-in expired" on My submissions while the saved sign-in could still be renewed, 2026-11-18).
      if (err?.status === 401) {
        if (refreshed) break
        refreshed = true
        await freshSignIn(process.env, fetch, true)
      }
    }
  }
  throw lastErr
}

async function once(name: string, args: Record<string, unknown>, timeoutMs: number): Promise<any> {
  await freshSignIn()
  const { url, token } = config()
  if (!url) throw Object.assign(new Error("kell is not signed in on this machine"), { status: 401 })
  const init = await rpc(String(url), String(token || ""), {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "kell-desktop", version: "0" } },
  }, null, timeoutMs)
  if (init.status === 401) throw Object.assign(new Error("kell's sign-in expired; sign in again"), { status: 401 })
  if (init.status >= 400) throw Object.assign(new Error(`kell's MCP server answered ${init.status}`), { status: 502 })
  await rpc(String(url), String(token || ""), { jsonrpc: "2.0", method: "notifications/initialized" }, init.sid, timeoutMs).catch(() => undefined)
  return payload(await rpc(String(url), String(token || ""), {
    jsonrpc: "2.0", id: 2, method: "tools/call", params: { name, arguments: args },
  }, init.sid, timeoutMs))
}
