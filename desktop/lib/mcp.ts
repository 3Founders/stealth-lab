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

export async function callMcpTool(name: string, args: Record<string, unknown>, timeoutMs = 12_000): Promise<any> {
  let lastErr: any
  // A hosted edge can terminate a long semantic lookup (the transport says "terminated"). One retry on a fresh MCP
  // session is cheap and makes the plan reliable; a 401 is final (the sign-in is gone).
  for (let attempt = 0; attempt < 2; attempt++) {
    try { return await once(name, args, timeoutMs) } catch (err: any) {
      lastErr = err
      if (err?.status === 401 || err?.status === 400) break
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
