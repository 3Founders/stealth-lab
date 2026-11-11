// Extractive compression for imported assistant replies (docs/localdb.md). No model call: the full reply stays in the
// local store for the UI, and this produces the short form that may be sent as context later. The rules keep the parts
// that usually carry state (what was done, decided, found, changed) and drop code bodies, tool noise and repetition.
const NOISE = /^(<\/?[a-z-]+>|\s*\{|\s*\}|```|diff --git|@@|\+\+\+|---|[-+]\s*$)/i
const SIGNAL = /^(i |we |fixed|added|changed|created|updated|removed|renamed|found|the |this |note|next|now|then|because|so |it |there |here |implemented|wrote|ran|tested|verified|decided|will |should |need|todo|remaining|still )/i

const clean = (s: string) => s.replace(/\s+/g, " ").trim()

/** A bounded, readable summary of one assistant reply. */
export function summarize(text: string, maxChars = 900): string {
  const lines = String(text || "").split("\n")
  const picked: string[] = []
  let inCode = false
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue
    if (line.startsWith("```")) { inCode = !inCode; continue }
    if (inCode) continue
    if (NOISE.test(line)) continue
    if (line.length < 24) continue
    if (/^([-*+]|\d+\.)\s+/.test(line) || SIGNAL.test(line)) {
      const c = clean(line).replace(/^([-*+]|\d+\.)\s+/, "")
      if (c && !picked.some((p) => p === c)) picked.push(c)
    }
    if (picked.join(" ").length > maxChars) break
  }
  if (!picked.length) {
    // No obvious signal lines: keep the first sentences, still bounded.
    const first = clean(String(text || "")).slice(0, maxChars)
    return first.length < String(text || "").length ? first.replace(/\s+\S*$/, "") + "…" : first
  }
  let out = ""
  for (const p of picked) {
    const next = out ? `${out} ${p}` : p
    if (next.length > maxChars) break
    out = next
  }
  return out.length < clean(text).length ? out.replace(/\s+\S*$/, "") + "…" : out
}
