// kell's server calls the desktop app still makes for itself: the /goal verdict, the Goal -> Way -> Tool route, and the
// rules the engine is given for each prompt. The tool library (its cards, sign-ins and approvals) was removed from the
// app on 2026-11-16; the MCP server and the database still hold it, the desktop just no longer shows or wires it.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { freshSignIn } from "./signin"

const config = () => {
  const home = process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
  let c: any = {}
  try { c = JSON.parse(fs.readFileSync(path.join(home, "config.json"), "utf8")) } catch { /* not signed in */ }
  return { url: process.env.STEALTHLAB_MCP_URL || process.env.STEALTHLAB_URL || c.url, token: c.token || process.env.STEALTHLAB_TOKEN }
}

const sibling = (mcpUrl: string, name: string) => {
  try {
    const u = new URL(mcpUrl); u.pathname = u.pathname.replace(/\/mcp\/?$/, "").replace(/\/$/, "") + name; u.search = ""; u.hash = ""
    return u.toString()
  } catch { return null }
}

async function post(name: string, body: unknown, ms: number): Promise<any> {
  await freshSignIn()
  const { url, token } = config()
  const target = url && sibling(url, name)
  if (!target) throw Object.assign(new Error("kell is not signed in on this machine (no server in ~/.stealthlab/config.json)"), { status: 401 })
  const r = await fetch(target, {
    method: "POST", signal: AbortSignal.timeout(ms), body: JSON.stringify(body),
    headers: { accept: "application/json", "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
  })
  if (!r.ok) throw Object.assign(new Error(`kell's ${name.slice(1)} answered ${r.status}`), { status: 502 })
  return r.json()
}

/** The /goal verdict from kell's semantic judge (`POST /goal/judge`): JEV first, then Gemini/Gemma. A typed choice,
 *  not a chat model -- the reason is the judge's verdict restated, since JEV does not write free text. */
export type GoalVerdict = {
  verdict: "met" | "not_yet" | "impossible"; confidence?: number; reason?: string; provider?: string; model?: string
}
export async function judgeGoal(condition: string, transcript: string): Promise<GoalVerdict> {
  return post("/goal/judge", { condition: condition.slice(0, 4000), transcript: transcript.slice(-30_000) }, 30_000)
}

/** Goal -> Way -> Tool for a task, from the MCP server the app is already signed in to. The same answer as the website's
 *  GET /v1/goals/route (app/tool_routing/route_api.py): the Goal, its ways with their approval tier, and the tools the
 *  request ranks inside it. Read-only -- nothing is called, no plan is written. */
export async function routeGoals(need: string, k = 5, timeoutMs = 60_000): Promise<any> {
  await freshSignIn()
  const { url, token } = config()
  const base = url && sibling(url, "/v1/goals/route")
  if (!base) throw Object.assign(new Error("kell is not signed in on this machine (no server in ~/.stealthlab/config.json)"), { status: 401 })
  const target = new URL(base)
  target.search = new URLSearchParams({
    q: need.slice(0, 4000),
    k: String(Math.min(Math.max(Number(k) || 5, 1), 20)),
  }).toString()
  const r = await fetch(target.toString(), {
    method: "GET", signal: AbortSignal.timeout(timeoutMs),
    headers: { accept: "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
  })
  if (!r.ok) throw Object.assign(new Error(`kell's router answered ${r.status}`), { status: 502 })
  return r.json()
}

// The room agent's TOOLS_ON and WORK_RULES (backend/app/services/room_agent.py), for a coding agent: local work stays on the engine's
// own tools. The tool library is no longer offered in the app, so the rules no longer teach its cards or sign-ins. The delegate
// line: the session's model already is kell's cheapest, and a delegated answer was paid for twice and then thrown away (2026-11-08).
export const TOOL_RULES = [
  "You are the coding agent in kell's desktop app (not Claude Code and not Claude Desktop). The person opened one project folder; your read, "
  + "edit, glob, grep and shell tools work in it. kell's server is already connected; nothing needs installing or adding as a connector.",
  "kell looks each new task up before your turn and adds what it knows above (find_ways already ran for it: do not call it again for the "
  + "same task). When this turn's delegation names a Way, run it with kell_run_way as the delegation says, one step at a "
  + "time. Never repeat a step's work with shell, scripts or direct HTTP calls. Otherwise do the work in this folder yourself; do not hand "
  + "it to kell_delegate or kell_call_model. If you do hand a sub-task to a subagent or kell_delegate, use its result: never redo the same "
  + "work in parallel.",
  "If you need something only the person can tell you, ask in your reply and stop there. glob and grep skip files git ignores (.env files, "
  + "build output): before saying a file does not exist, check with the shell.",
].join("\n\n")
