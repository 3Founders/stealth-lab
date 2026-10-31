// Step 0b: does ctx.session.switchModel work when called inside the session.prompt hook?
// Logs which model the next request really uses (model.request hook) to switch-test.log.
import { appendFileSync } from "node:fs"
import { Plugin } from "@opencode/plugin"

const LOG = new URL("../../switch-test.log", import.meta.url)
const log = (s) => appendFileSync(LOG, `${new Date().toISOString()} ${s}\n`)

export default Plugin.define({
  id: "kell-switch-test",
  setup: async (ctx) => {
    await ctx.session.hook("prompt", async (e) => {
      log(`prompt hook: session=${e.sessionID}; switching to gc/minimax-m2.7`)
      try {
        await ctx.session.switchModel({ sessionID: e.sessionID, model: { id: "minimax-m2.7", providerID: "gc" } })
        log("switchModel returned ok")
      } catch (err) {
        log(`switchModel threw: ${String(err?.message ?? err)}`)
      }
    })
    await ctx.session.hook("model.request", (e) => log(`model.request kind=${e.kind} model=${e.model.providerID}/${e.model.id}`))
  },
})
