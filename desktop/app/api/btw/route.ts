// POST { question, transcript }: one side question about the chat already on screen, answered in one response with no
// tools. This is Claude Code's /btw (code.claude.com/docs/en/commands): the question sees the conversation, cannot read
// files or run anything, and is never added to the session. The engine's one-shot generate is the right primitive here:
// it is a plain model call, so it creates no session, writes no message and leaves the running turn alone.
import { engineStatus, clientFor } from "@/lib/engine"

export const dynamic = "force-dynamic"

// The newest turns are what a side question is usually about, so keep the tail of the conversation.
const MAX_TRANSCRIPT = 24_000
const MAX_QUESTION = 4_000

/** What the model is told about this call. Same constraints Claude Code gives /btw, in kell's voice. */
const INSTRUCTIONS = `This is a side question from the user about the conversation below. Answer it directly, in one response.

- You are a separate, lightweight agent spawned to answer this one question. The main agent is not interrupted.
- You can see the conversation below and nothing else. There are no files, no shell and no search.
- You have NO tools. Never say "let me check", "let me read" or promise to take any action.
- This is a one-off: there will be no follow-up turns. Answer from what is already here.
- Be brief and concrete. If the conversation does not contain the answer, say so plainly.`

export async function POST(req: Request) {
  const e = engineStatus()
  if (!e) return Response.json({ error: "The engine is down; open a project first." }, { status: 502 })
  let body: any
  try { body = await req.json() } catch { return Response.json({ error: "Bad request." }, { status: 400 }) }
  const question = String(body?.question || "").trim().slice(0, MAX_QUESTION)
  if (!question) return Response.json({ error: "Ask a question: /btw <question>" }, { status: 400 })
  const transcript = String(body?.transcript || "").trim().slice(-MAX_TRANSCRIPT)
  const prompt = `${INSTRUCTIONS}\n\nConversation so far:\n${transcript || "(nothing has been said yet)"}\n\nSide question: ${question}`
  try {
    const client = await clientFor(e)
    const out = await client.experimental.generate.text({ prompt })
    const answer = String((out as any)?.text || "").trim()
    if (!answer) return Response.json({ error: "The model returned no answer." }, { status: 502 })
    return Response.json({ answer })
  } catch (err: any) {
    // The same hygiene the prompt route uses: never echo a bearer, and keep the message to one line.
    const msg = String(err?.message || err).replace(/Bearer\s+\S+/gi, "Bearer …").slice(0, 300)
    return Response.json({ error: msg || "The side question failed." }, { status: 502 })
  }
}
