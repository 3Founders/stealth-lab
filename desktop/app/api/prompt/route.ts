// POST { sessionID?, directory, text }: sends one prompt and streams the session's events as SSE until the turn ends
// (session.idle for it). Event payloads go through as-is; the UI maps types to bubbles, chips and the model strip.
import { engineStatus, clientFor } from "@/lib/engine"
import { pooledEngineConfig, ownKeyLane, walletRemaining, isNeedPlan } from "@/lib/pooled"
import { TOOL_RULES } from "@/lib/tools"

export const dynamic = "force-dynamic"

/** A failed turn's reason in one line: the engine's own message where it gave one, else what it sent. */
const reasonOf = (d: any): string => {
  const m = d?.error?.data?.message ?? d?.error?.message ?? d?.message ?? d?.error
  const s = typeof m === "string" ? m : JSON.stringify(d)
  return s.replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300) || "the model did not answer"
}

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return new Response("engine is down; open a project first", { status: 502 })
  try {
    const body = await req.json()
    const stream = new ReadableStream({
      async start(ctrl) {
        const enc = (obj: any) => ctrl.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(obj)}\n\n`))
        // The personal lane spends the person's own wallet: with nothing left, say "you need a plan" instead of letting the
        // gateway's refusal come back as a raw engine error. (A balance that cannot be read is left to the model call.)
        if (!pooledEngineConfig() && !ownKeyLane()) {
          const left = await walletRemaining()
          if (left !== null && left <= 0) { enc({ type: "need_plan" }); enc({ type: "done", outcome: "need_plan" }); ctrl.close(); return }
        }
        const client = await clientFor(e)                 // async: loads the ESM-only client via import()
        // Subscribe before prompting: otherwise the turn's session.idle can fire before the stream starts.
        const sessionID: string = body.sessionID ||
          (await client.session.create({ location: { directory: e.directory } })).id
        enc({ type: "session", sessionID })
        const pump = (async () => {
          try {
            for await (const ev of client.event.subscribe()) {
              const k = (ev as any).type as string
              if (!k.startsWith("session.") && k !== "permission.asked") continue   // a permission ask waits on the person
              const d = (ev as any).data ?? {}
              if (d.sessionID && d.sessionID !== sessionID) continue
              // the turn ends with execution.succeeded/failed (idle may not fire in serve mode)
              if (k === "session.execution.failed") {
                // the reason was dropped here, so a failed turn showed nothing at all (2026-11-06)
                const why = JSON.stringify(d)
                enc(isNeedPlan(why) ? { type: "need_plan" } : { type: "error", error: reasonOf(d) })
              }
              if (k === "session.execution.succeeded" || k === "session.execution.failed" || k === "session.idle") {
                enc({ type: "done", outcome: k })
                break
              }
              if (k.endsWith("failed") && isNeedPlan(JSON.stringify(d))) { enc({ type: "need_plan" }); enc({ type: "done", outcome: "need_plan" }); break }
              enc({ type: k, data: d })
            }
          } catch (err: any) {
            const msg = String(err?.message || err)
            enc(isNeedPlan(msg) ? { type: "need_plan" } : { type: "error", error: msg })
          }
          ctrl.close()
        })()
        // This app's rules for its engine (lib/tools.ts), as the session's instruction; kell's plugin adds the task lookup and the tool note
        // itself. An engine that refuses the entry still runs the prompt.
        const text = String(body.text || "")
        try {
          await client.experimental.session.instructions.entry.put({ sessionID, key: "kell-tools", value: TOOL_RULES } as any)
        } catch { /* the prompt still runs */ }
        await client.session.prompt({ sessionID, text })
        await pump
      },
    })
    return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-store" } })
  } catch (err: any) {
    return new Response(String(err?.message || err), { status: 502 })
  }
}
