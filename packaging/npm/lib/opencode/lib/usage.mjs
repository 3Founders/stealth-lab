/** The last turn's usage: assistant messages after the last user message, summed. Field names follow OpenCode's
 *  session-message schema (tokens: input, output, cache.read; cost in USD); anything missing is left out. */
export function turnUsage(messages, model) {
  const list = Array.isArray(messages) ? messages : []
  let i = list.length - 1
  while (i >= 0 && (list[i]?.role ?? list[i]?.info?.role) !== "user") i--
  const out = { scaffold: "opencode", ...(model ? { model } : {}) }
  let tin = 0, tout = 0, cached = 0, cost = 0, seen = false, costSeen = false
  for (const m of list.slice(i + 1)) {
    const t = m?.tokens ?? m?.info?.tokens
    const c = m?.cost ?? m?.info?.cost
    if (t && typeof t === "object") { seen = true; tin += Number(t.input) || 0; tout += (Number(t.output) || 0) + (Number(t.reasoning) || 0); cached += Number(t.cache?.read) || 0 }
    if (Number.isFinite(Number(c)) && c !== null && c !== undefined) { costSeen = true; cost += Number(c) }
  }
  if (seen) Object.assign(out, { tokens_in: tin, tokens_out: tout, tokens_cached: cached })
  if (costSeen) out.cost_usd = Math.round(cost * 1e6) / 1e6
  return out
}
