// The coding engine's model provider on the personal lane: forwards /api/gateway/v1/... to kell's gateway with the current website
// session (lib/websession.ts), streaming the answer back as it comes. The engine only knows this local address and a placeholder key,
// so nothing it holds can expire. Loopback only: the Next server listens on 127.0.0.1.
import { gatewayOrigin } from "@/lib/pooled"
import { effortBody, readThinking } from "@/lib/thinking"
import { forgetWebToken, SIGN_IN_TO_RUN, webToken } from "@/lib/websession"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"

export const dynamic = "force-dynamic"

const ALLOWED = /^v1\/(models|chat\/completions|billing\/wallet)$/

const origin = () => {
  let cfg: any = null
  try { cfg = JSON.parse(fs.readFileSync(path.join(process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "config.json"), "utf8")) } catch { /* default */ }
  return gatewayOrigin(cfg)
}

const refuse = (status: number, message: string) =>
  new Response(JSON.stringify({ error: { message, type: "authentication_error" } }), { status, headers: { "content-type": "application/json" } })

async function forward(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const sub = (await ctx.params).path.join("/")
  if (!ALLOWED.test(sub)) return refuse(404, "not a model call")
  let token = await webToken()
  if (!token) return refuse(401, SIGN_IN_TO_RUN)
  let body: ArrayBuffer | string | undefined = req.method === "POST" ? await req.arrayBuffer() : undefined
  // The composer's thinking level rides on the model call itself (lib/thinking.ts): the engine sends the request, the
  // provider reads reasoning_effort, and nothing in the engine has to know about the setting.
  let applied = ""
  if (body && /chat\/completions$/.test(sub)) {
    try {
      const call = JSON.parse(new TextDecoder().decode(body))
      const levels = readThinking()
      const level = levels[String(call?.model || "")] || levels.default
      const extra = effortBody(String(call?.model || ""), level)
      if (Object.keys(extra).length) {
        // The person's pick wins over anything the engine put in the body: the engine's own default is what "Auto" is for.
        Object.assign(call, extra)
        body = JSON.stringify(call)
        applied = String(extra.reasoning_effort || "off")
      }
    } catch { /* not JSON: forward it untouched */ }
  }
  const headers = new Headers()
  for (const [k, v] of req.headers) if (/^(content-type|accept|x-stealthlab-)/i.test(k)) headers.set(k, v)
  const send = (t: string) => { headers.set("authorization", `Bearer ${t}`); return fetch(`${origin()}/${sub}`, { method: req.method, headers, body }) }
  let r = await send(token)
  if (r.status === 401) {                    // refreshed since it was read: ask once more
    forgetWebToken()
    token = await webToken()
    if (!token) return refuse(401, SIGN_IN_TO_RUN)
    r = await send(token)
  }
  const out = new Headers()
  for (const k of ["content-type", "cache-control"]) { const v = r.headers.get(k); if (v) out.set(k, v) }
  if (applied) out.set("x-stealthlab-thinking", applied)   // what this call was sent, for the app and for a bug report
  for (const [k, v] of r.headers) if (/^x-stealthlab-/i.test(k)) out.set(k, v)   // the model that answered, a fallback taken, the cost
  return new Response(r.body, { status: r.status, headers: out })
}

export const GET = forward
export const POST = forward
