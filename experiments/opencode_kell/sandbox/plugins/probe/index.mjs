// Step 0 probe: what do the tool/session hooks really receive? Logs shapes (never secrets) to probe.log.
import { appendFileSync } from "node:fs"
import { Plugin } from "@opencode/plugin"
const LOG = new URL("../../probe.log", import.meta.url)
const log = (s) => appendFileSync(LOG, s.slice(0, 1500) + "\n")
export default Plugin.define({
  id: "kell-probe",
  setup: async (ctx) => {
    await ctx.session.hook("prompt", (e) => log(`PROMPT keys=${Object.keys(e)} prompt=${JSON.stringify(e.prompt).slice(0, 200)} delivery=${JSON.stringify(e.delivery)}`))
    await ctx.tool.hook("execute.before", (e) => log(`BEFORE tool=${e.tool} id=${e.id} input=${JSON.stringify(e.input).slice(0, 300)}`))
    await ctx.tool.hook("execute.after", (e) => log(`AFTER tool=${e.tool} status=${e.status} result=${JSON.stringify(e.status === "completed" ? e.result : e.error).slice(0, 500)}`))
    await ctx.session.hook("experimental.idle", (e) => log(`IDLE ${JSON.stringify(e)}`))
  },
})
