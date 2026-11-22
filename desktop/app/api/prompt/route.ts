// POST { sessionID?, directory, text }: sends one prompt and streams the session's events as SSE until the turn ends
// (session.idle for it). Event payloads go through as-is; the UI maps types to bubbles, chips and the model strip.
import path from "node:path"
import { pathToFileURL } from "node:url"
import { engineStatus, clientFor } from "@/lib/engine"
import { contractOf, type Delegation } from "@/lib/delegate"
import { pooledEngineConfig, ownKeyLane, walletRemaining, isNeedPlan } from "@/lib/pooled"
import { rulesFor } from "@/lib/tools"
import { isHome } from "@/lib/home"
import { appendMessage, ensureSession, getAssistantMessages, getSession } from "@/lib/localdb"
import { summarize } from "@/lib/summarize"
import { freshSignIn } from "@/lib/signin"
import { pinModel } from "@/lib/kellplugin"

export const dynamic = "force-dynamic"

/** A Way this long on less than this balance is likely to stop partway: the person hears it before the run starts. */
const LONG_WAY_STEPS = 5
const LOW_FOR_LONG_WAY_USD = 0.5

type InFile = { name?: string; path?: string; dataUrl?: string; mime?: string; rel?: string; size?: number }
const TEXT_EXT = /\.(txt|md|markdown|json|jsonl|ya?ml|toml|ini|cfg|conf|env|csv|tsv|xml|html?|css|scss|less|m?js|cjs|jsx|tsx?|py|rb|go|rs|java|kt|swift|c|h|cpp|hpp|cs|php|sh|bash|zsh|ps1|sql|graphql|gql|vue|svelte|astro|lua|r|scala|clj|exs?|dart|tf|hcl|properties|lock|log)$/i
const isText = (f: InFile) => /^text\//.test(String(f.mime || "")) || /(json|xml|yaml|javascript|typescript|x-sh|x-python)/.test(String(f.mime || "")) || TEXT_EXT.test(String(f.name || ""))

/** Files the composer attached: project text files become a path reference (cheapest); everything else is attached. */
function filesOf(raw: unknown): { text: string; files: any[] } {
  const list: InFile[] = Array.isArray(raw) ? raw.filter((f: any) => f && typeof f === "object").slice(0, 10) : []
  const refs: string[] = []
  const files: any[] = []
  for (const f of list) {
    const name = String(f.name || "attachment")
    // A text file inside the project is already readable by the agent: send its path, never its bytes.
    if (f.rel && isText(f)) { refs.push(`- ${f.rel}`); continue }
    if (f.path) {
      const uri = pathToFileURL(path.resolve(f.path)).href
      files.push({ uri, source: { type: "uri", uri }, name })
      continue
    }
    if (f.dataUrl && String(f.dataUrl).length <= 12_000_000) files.push({ uri: String(f.dataUrl), source: { type: "inline" }, name })
  }
  return { text: refs.length ? `\n\nAttached files (read them if needed):\n${refs.join("\n")}` : "", files }
}

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
    // Stop (2026-11-18): the app stops listening (it aborts this request) or the engine reports the turn interrupted. Either
    // way this stream ends, and an abort also interrupts the engine, so a turn whose session id the app did not know yet
    // (a new chat's first message) stops too.
    let stopped = false
    let turnSession = ""
    let closeStream: (() => void) | null = null
    const stream = new ReadableStream({
      async cancel() {
        stopped = true
        closeStream?.()
        if (turnSession) { try { await (await clientFor(e)).session.interrupt({ sessionID: turnSession }) } catch { /* already over */ } }
      },
      async start(ctrl) {
        let closed = false
        const close = () => { if (closed) return; closed = true; try { ctrl.close() } catch { /* already closed */ } }
        closeStream = close
        const enc = (obj: any) => { if (closed) return; try { ctrl.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(obj)}\n\n`)) } catch { closed = true } }
        // The personal lane spends the person's own wallet: with nothing left, say "you need a plan" instead of letting the
        // gateway's refusal come back as a raw engine error. (A balance that cannot be read is left to the model call.)
        const way = body.plan && typeof body.plan === "object" ? (body.plan as Delegation) : null
        if (!pooledEngineConfig() && !ownKeyLane()) {
          const left = await walletRemaining()
          if (left !== null && left <= 0) { enc({ type: "need_plan" }); enc({ type: "done", outcome: "need_plan" }); close(); return }
          // A Way of many steps on a nearly empty wallet stops partway (2026-11-15: a 16-step research Way stopped at
          // step 6). Say so before it starts; the run is kept, so it can go on after a top-up.
          if (left !== null && way?.way && way.steps.length >= LONG_WAY_STEPS && left < LOW_FOR_LONG_WAY_USD)
            enc({ type: "low_balance", left, steps: way.steps.length })
        }
        // kell's plugin in the engine reads the saved sign-in for this prompt's triage; an expired one made every triage fail,
        // and a failed triage meant the strong model (2026-11-18). Refreshing here costs nothing while the sign-in is fresh.
        await freshSignIn()
        const client = await clientFor(e)                 // async: loads the ESM-only client via import()
        // Subscribe before prompting: otherwise the turn's session.idle can fire before the stream starts.
        // A chat the engine no longer has (its session was removed, or an app restart lost it) is made again under the
        // SAME id, so the app's own row and the engine's session stay one chat. The local transcript is handed to the
        // model as prior context below, so the conversation can go on (2026-11-20: a restored chat had its transcript
        // but no engine session).
        let sessionID = String(body.sessionID || "")
        let restored = false
        if (sessionID) {
          try { await client.session.get({ sessionID } as any) }
          catch {
            restored = true
            try {
              await client.session.create({ id: sessionID, location: { directory: e.directory }, metadata: { kell: true } } as any)
            } catch {
              // An engine that refuses the id still gets a session; the chat goes on under the engine's own id.
              sessionID = (await client.session.create({ location: { directory: e.directory }, metadata: { kell: true } })).id
            }
          }
        } else {
          sessionID = (await client.session.create({ location: { directory: e.directory }, metadata: { kell: true } })).id
        }
        turnSession = sessionID
        if (stopped) { try { await client.session.interrupt({ sessionID }) } catch { /* nothing ran */ } close(); return }
        enc({ type: "session", sessionID })
        // Write the person's message to the app's own store before the engine runs. History is an addition: a local
        // write failure must never stop the turn.
        try {
          ensureSession({ id: sessionID, engineId: sessionID, source: "kell", directory: e.directory })
          // Keep attachment metadata only: a data: URL belongs in the engine request, not in the history row.
          const meta = Array.isArray(body.files)
            ? body.files.map(({ dataUrl: _drop, ...f }: any) => f)
            : undefined
          appendMessage(sessionID, { role: "user", text: String(body.text || ""), attachments: meta })
        } catch (err) {
          // The turn still runs without the local row, but a silent failure is why a chat could vanish from the
          // app's own store with no trace (2026-11-12): say it in the server log.
          console.error("local history write failed:", err)
        }
        let assistant = ""
        const pump = (async () => {
          try {
            for await (const ev of client.event.subscribe()) {
              if (stopped) break
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
              // interrupted: the person pressed Stop. It ends the turn like the others (it used to be ignored, so the turn
              // never ended: "kell is thinking" stayed and the next message waited forever, 2026-11-18).
              if (k === "session.execution.succeeded" || k === "session.execution.failed" || k === "session.execution.interrupted" || k === "session.idle") {
                enc({ type: "done", outcome: k })
                break
              }
              if (k.endsWith("failed") && isNeedPlan(JSON.stringify(d))) { enc({ type: "need_plan" }); enc({ type: "done", outcome: "need_plan" }); break }
              if (k === "session.text.delta") assistant += String(d.delta ?? d.text ?? "")
              enc({ type: k, data: d })
            }
          } catch (err: any) {
            const msg = String(err?.message || err)
            if (!stopped) enc(isNeedPlan(msg) ? { type: "need_plan" } : { type: "error", error: msg })
          }
          close()
        })()
        // This app's rules for its engine (lib/tools.ts), as the session's instruction; kell's plugin adds the task lookup and the tool note
        // itself. An engine that refuses the entry still runs the prompt.
        const attached = filesOf(body.files)
        // Imported Claude/Codex transcripts keep their full replies in the local store for the UI. The engine never
        // received those assistant turns (its import only materialises user messages), so give it a bounded summary
        // instead of the full text: context without shipping the whole transcript to a provider.
        let prior = ""
        try {
          const local = getSession(sessionID)
          // A restored chat (this session's engine record was gone, or the row says it was restored) gets the same
          // compressed prior as an imported chat: the model must know what was already said.
          let flagged = false
          try { flagged = JSON.parse(local?.metadata || "{}")?.restored === true } catch { /* not JSON */ }
          if (local && (local.source !== "kell" || restored || flagged)) {
            const bits: string[] = []
            let used = 0
            for (const m of getAssistantMessages(sessionID, 12)) {
              const b = m.summary || summarize(m.text)
              if (!b || used + b.length > 3000) continue
              bits.push(b); used += b.length
            }
            if (bits.length) prior = `\n\nPrior assistant work (compressed; full transcript is in the local app):\n${bits.map((b) => `- ${b}`).join("\n")}`
          }
        } catch { /* history is an addition */ }
        const text = String(body.text || "") + attached.text + prior
        // The routed Way for this turn: when it is bound to a tool, that tool does the work and this turn may not redo
        // it (lib/delegate.ts contractOf). It goes into the request and the session instruction, so the model reads it
        // even when the plugin's advisory tool note was skipped. A turn with no plan is an ordinary coding turn.
        const plan = body.plan && typeof body.plan === "object" ? (body.plan as Delegation) : null
        const contract = contractOf(plan)
        // The routed model for this turn (lib/delegate.ts): the chat model the person is talking to is the one the
        // procedure needs. A missing or unreadable override falls back to the engine's own choice.
        const model = body.model && typeof body.model.id === "string" && typeof body.model.providerID === "string"
          ? { id: String(body.model.id), providerID: String(body.model.providerID) }
          : plan?.model && typeof plan.model.id === "string" && typeof plan.model.providerID === "string"
            ? { id: plan.model.id, providerID: plan.model.providerID }
            : undefined
        const promptText = contract ? `${text}\n\n${contract}` : text
        // The composer's mode: "agent" adds the research rules (lib/tools.ts AGENT_RULES), so the model knows it may run
        // several web searches through kell's tools instead of one.
        const mode = String(body.mode || "")
        // The person's own pick (body.pinned: the composer's model) is pinned for kell's plugin, which otherwise re-picks the model
        // from its triage on every prompt, so the picker was ignored (2026-11-18). Back on Auto, the pin is lifted.
        const pinned = !!body.pinned && !!model
        pinModel(sessionID, pinned && model ? `${model.providerID}/${model.id}` : null)
        try {
          await client.experimental.session.instructions.entry.put({ sessionID, key: "kell-tools",
            value: contract ? `${rulesFor(isHome(e.directory), mode)}\n\n${contract}` : rulesFor(isHome(e.directory), mode) } as any)
        } catch { /* the prompt still runs */ }
        try {
          if (stopped) { close(); return }
          await client.session.prompt({ sessionID, text: promptText, ...(model ? { model } : {}), ...(attached.files.length ? { files: attached.files } : {}) } as any)
          // A model the person named is also the session's own, so every later turn of this chat (a reply typed while the
          // engine restarts, a queued prompt) starts from it rather than from whatever the session last used. kell's own rung
          // (a Way's strong model) is for this turn only: kept as the session's, it ran every later turn on it too.
          if (model && pinned) { try { await client.session.switchModel({ sessionID, model } as any) } catch { /* per-turn only */ } }
          await pump
        } finally {
          // One assistant row per turn, assembled from the stream. A failed turn keeps the user row and any text received.
          try { if (assistant.trim()) appendMessage(sessionID, { role: "assistant", text: assistant, summary: summarize(assistant) }) }
          catch (err) { console.error("local history write failed (assistant):", err) }
        }
      },
    })
    return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-store" } })
  } catch (err: any) {
    return new Response(String(err?.message || err), { status: 502 })
  }
}
