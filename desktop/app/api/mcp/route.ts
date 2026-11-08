// The engine's kell MCP server: forwards MCP's streamable HTTP to the hosted server with the current kell sign-in, streaming the answer
// back as it comes (an SSE reply included). The engine started with a token in its config, and once that expired (about an hour) every
// kell tool answered 401 and stayed gone until the app restarted (2026-11-08, the e2e test). Here the token is read, and refreshed when
// due, on each request, the way /api/gateway does for model calls. Loopback only: the Next server listens on 127.0.0.1.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { freshSignIn } from "@/lib/signin"

export const dynamic = "force-dynamic"

const saved = (): { url?: string; token?: string } => {
  try { return JSON.parse(fs.readFileSync(path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "config.json"), "utf8")) || {} }
  catch { return {} }
}

// what MCP's transport sends and answers with; nothing else crosses
const UP = /^(content-type|accept|mcp-session-id|mcp-protocol-version|last-event-id)$/i
const DOWN = ["content-type", "cache-control", "mcp-session-id", "mcp-protocol-version", "www-authenticate"]

async function forward(req: Request): Promise<Response> {
  await freshSignIn()
  let cfg = saved()
  if (!cfg.url) return new Response("kell is not signed in on this machine", { status: 401 })
  const body = req.method === "POST" ? await req.arrayBuffer() : undefined
  const headers = new Headers()
  for (const [k, v] of req.headers) if (UP.test(k)) headers.set(k, v)
  const send = () => {
    if (cfg.token) headers.set("authorization", `Bearer ${cfg.token}`)
    return fetch(String(cfg.url), { method: req.method, headers, body, signal: req.signal })
  }
  let r = await send()
  if (r.status === 401) {                    // refreshed by another process since it was read: read it again, once
    await freshSignIn()
    cfg = saved()
    r = await send()
  }
  const out = new Headers()
  for (const k of DOWN) { const v = r.headers.get(k); if (v) out.set(k, v) }
  return new Response(r.body, { status: r.status, headers: out })
}

export const GET = forward
export const POST = forward
export const DELETE = forward
