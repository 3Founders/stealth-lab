// The goal evaluator: one fresh model call after each turn, judging a completion condition against what the turn
// actually showed (the same idea as Claude Code's /goal, code.claude.com/docs/en/goal).
//
// Why a separate call: the model doing the work is the worst judge of whether it is done. This route asks the cheap
// model, on the same kell gateway and wallet the engine uses, for one of three verdicts. It never runs a tool and never
// reads the project; it judges the transcript the app sends.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { NextResponse } from "next/server"
import { gatewayOrigin, ladderOrder, personalEngineConfig, personalModelIds } from "@/lib/pooled"
import { modelPlan } from "@/lib/kellplugin"
import { webToken } from "@/lib/websession"

export const dynamic = "force-dynamic"

const kellHome = () => process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
const readJson = (file: string) => { try { return JSON.parse(fs.readFileSync(file, "utf8")) } catch { return null } }

type Verdict = { verdict: "met" | "not_yet" | "impossible"; reason: string }

const SYSTEM = `You are the goal evaluator for a coding agent. You do not run tools and you cannot read files.
Judge the completion condition only against what the conversation transcript shows. Be strict: "met" requires the
transcript to demonstrate the condition (for example, the test command ran and exited 0). "impossible" is only for a
condition that can never be satisfied. Otherwise answer "not_yet" with one concrete next check.
Answer with JSON only: {"verdict":"met"|"not_yet"|"impossible","reason":"one or two sentences"}.`

function parseVerdict(text: string): Verdict {
  const raw = text.trim()
  const json = raw.match(/\{[\s\S]*\}/)?.[0]
  if (json) {
    try {
      const j = JSON.parse(json)
      const v = String(j?.verdict || "").toLowerCase().replace(/[\s-]+/g, "_")
      if (v === "met" || v === "not_yet" || v === "impossible") {
        return { verdict: v as Verdict["verdict"], reason: String(j?.reason || "").slice(0, 800) }
      }
    } catch { /* the model wrapped it in prose: fall through */ }
  }
  const low = raw.toLowerCase()
  if (/\bimpossible\b|cannot be (met|satisfied)|never be (met|satisfied)/.test(low)) return { verdict: "impossible", reason: raw.slice(0, 800) }
  if (/\b(not yet met|not met|does not hold|doesn't hold|still failing|not satisfied)\b/.test(low)) return { verdict: "not_yet", reason: raw.slice(0, 800) }
  if (/\bmet\b|condition (is )?(satisfied|holds)|tests? pass/.test(low)) return { verdict: "met", reason: raw.slice(0, 800) }
  return { verdict: "not_yet", reason: raw.slice(0, 800) || "The evaluator gave no reason." }
}

export async function POST(req: Request) {
  let body: any
  try { body = await req.json() } catch { return NextResponse.json({ error: "JSON body" }, { status: 400 }) }
  const condition = typeof body?.condition === "string" ? body.condition.trim().slice(0, 4_000) : ""
  const transcript = typeof body?.transcript === "string" ? body.transcript.slice(-30_000) : ""
  if (!condition) return NextResponse.json({ error: "a goal condition is required" }, { status: 400 })

  const token = await webToken()
  if (!token) return NextResponse.json({ error: "Sign in to run models on your kell plan." }, { status: 401 })

  const cfg = readJson(path.join(kellHome(), "config.json")) || {}
  const oc = readJson(path.join(kellHome(), "opencode", "config.json")) || {}
  let model = typeof oc.cheap === "string" ? oc.cheap : ""
  if (!model) {
    // The engine has not written its plan yet: ask the gateway for the lane's models, then take the cheap rung.
    const lane = await personalEngineConfig()
    if (!lane.ok) return NextResponse.json({ error: lane.message }, { status: 401 })
    model = modelPlan(ladderOrder(personalModelIds())).cheap
  }

  let res: Response
  try {
    res = await fetch(`${gatewayOrigin(cfg)}/v1/chat/completions`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "x-stealthlab-data-class": process.env.KELL_OC_DATA_CLASS || "USER_PRIVATE",
      },
      body: JSON.stringify({
        model, stream: false, temperature: 0, max_tokens: 300,
        messages: [{ role: "system", content: SYSTEM }, { role: "user", content: `Condition:\n${condition}\n\nConversation:\n${transcript}` }],
      }),
      signal: AbortSignal.timeout(60_000),
    })
  } catch {
    return NextResponse.json({ error: "The evaluator could not reach kell. The goal is paused; try again." }, { status: 502 })
  }
  const text = await res.text()
  let parsed: any = null
  try { parsed = JSON.parse(text) } catch { /* not JSON */ }
  if (!res.ok) {
    const why = parsed?.error?.message || parsed?.error || text.slice(0, 300)
    return NextResponse.json({ error: `The evaluator was refused: ${why}` }, { status: 502 })
  }
  const content = String(parsed?.choices?.[0]?.message?.content || "")
  if (!content) return NextResponse.json({ error: "The evaluator returned nothing." }, { status: 502 })
  return NextResponse.json(parseVerdict(content))
}
