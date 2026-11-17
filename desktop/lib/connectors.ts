// Connectors (docs/desktop_requirements_2026-11.md, P1 #8, 2026-11-17): the work apps people use most, added to the engine in one click.
// Each is the vendor's own remote MCP server (addresses from the MCP connector registry, 2026-11-17); the engine signs in with the
// vendor's OAuth page in the person's browser and keeps the token in its own credential store. What is added is kept in the app's store
// (meta "connectors") and put into the engine's config at every start (lib/pooled.ts), so it survives a restart.
//
// Some vendors only accept sign-in apps they registered in advance (Google's MCP servers, for one). For those the engine reports the
// failure, and the panel shows it as it is instead of pretending the connector works.
import { clientFor, engineStatus } from "./engine"
import { getMeta, setMeta } from "./localdb"

export type Connector = { id: string; name: string; url: string; what: string }
export const CATALOG: Connector[] = [
  { id: "notion", name: "Notion", url: "https://mcp.notion.com/mcp", what: "Search, read and write pages and databases" },
  { id: "slack", name: "Slack", url: "https://mcp.slack.com/mcp", what: "Search and read channels, send messages" },
  { id: "atlassian", name: "Jira & Confluence", url: "https://mcp.atlassian.com/v2/mcp", what: "Issues, pages and Bitbucket with your Atlassian permissions" },
  { id: "linear", name: "Linear", url: "https://mcp.linear.app/mcp", what: "Issues, projects and cycles" },
  { id: "asana", name: "Asana", url: "https://mcp.asana.com/v2/mcp", what: "Tasks, projects and goals" },
  { id: "canva", name: "Canva", url: "https://mcp.canva.com/mcp", what: "Find, create and export designs" },
  { id: "figma", name: "Figma", url: "https://mcp.figma.com/mcp", what: "Design context and screenshots" },
  { id: "box", name: "Box", url: "https://mcp.box.com/", what: "Search and read your Box files" },
  { id: "gdrive", name: "Google Drive", url: "https://drivemcp.googleapis.com/mcp/v1", what: "Search, read and upload files" },
  { id: "gmail", name: "Gmail", url: "https://gmailmcp.googleapis.com/mcp/v1", what: "Search mail and draft replies" },
  { id: "gcal", name: "Google Calendar", url: "https://calendarmcp.googleapis.com/mcp/v1", what: "Events, availability and invites" },
]

type Added = { id: string; name: string; url: string }
export function added(): Added[] {
  try { const v = JSON.parse(getMeta("connectors") || "[]"); return Array.isArray(v) ? v.filter((x) => x && x.id && x.url) : [] } catch { return [] }
}
const save = (list: Added[]) => setMeta("connectors", JSON.stringify(list))
const serverName = (id: string) => `c-${id.replace(/[^a-z0-9-]/gi, "").toLowerCase()}`

/** The engine config's MCP entries for every added connector (lib/pooled.ts merges them in). */
export function connectorServers(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const a of added()) out[serverName(a.id)] = { type: "remote", url: a.url }
  return out
}

async function client() {
  const e = engineStatus()
  if (!e) throw new Error("the engine is not running; open Chats or a project first")
  return { c: (await clientFor(e)) as any, dir: e.directory }
}

export async function addConnector(id: string, custom?: { name: string; url: string }) {
  const known = CATALOG.find((x) => x.id === id)
  const entry: Added | null = known ? { id: known.id, name: known.name, url: known.url }
    : custom && /^https:\/\//i.test(custom.url) ? { id: `custom-${custom.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 30) || "server"}`, name: custom.name.slice(0, 60) || "Custom", url: custom.url.trim() }
      : null
  if (!entry) throw new Error("pick a connector, or give a name and an https:// address")
  save([...added().filter((x) => x.id !== entry.id), entry])
  const { c, dir } = await client()
  const mcp = c.experimental?.mcp || c.mcp
  try { await mcp.add({ server: serverName(entry.id), location: { directory: dir }, config: { type: "remote", url: entry.url } }) }
  catch { /* already there from the config */ }
  try { await mcp.connect({ server: serverName(entry.id), location: { directory: dir } }) } catch { /* sign-in first */ }
  return entry
}

export async function removeConnector(id: string) {
  save(added().filter((x) => x.id !== id))
  try { const { c, dir } = await client(); await (c.experimental?.mcp || c.mcp).remove({ server: serverName(id), location: { directory: dir } }) } catch { /* gone at the next start */ }
}

export type ConnectorStatus = { id: string; name: string; url: string; status: string; error?: string; integrationID?: string }
export async function statuses(): Promise<ConnectorStatus[]> {
  const list = added()
  if (!list.length) return []
  let servers: any[] = []
  try { const { c, dir } = await client(); servers = (await c.mcp.list({ location: { directory: dir } }))?.data || [] } catch { /* engine down */ }
  return list.map((a) => {
    const s = servers.find((x) => x.name === serverName(a.id))
    return { ...a, status: s?.status?.status || (servers.length ? "not loaded" : "engine not running"), error: s?.status?.error, integrationID: s?.integrationID }
  })
}

/** Starts the vendor's sign-in: -> the URL to open in the browser and the attempt to poll. */
export async function signIn(id: string): Promise<{ url: string; attemptID: string; integrationID: string; instructions?: string }> {
  const st = (await statuses()).find((x) => x.id === id)
  if (!st?.integrationID) throw new Error(st?.error || "this connector has no sign-in (it may not need one, or the engine has not loaded it yet)")
  const { c, dir } = await client()
  const info = (await c.integration.get({ integrationID: st.integrationID, location: { directory: dir } }))?.data
  const method = (info?.methods || []).find((m: any) => m.type === "oauth")
  if (!method) throw new Error("this connector offers no browser sign-in")
  const attempt = (await c.integration.oauth.connect({ integrationID: st.integrationID, methodID: method.id, location: { directory: dir } }))?.data
  if (!attempt?.url) throw new Error("the sign-in did not start")
  return { url: attempt.url, attemptID: attempt.attemptID, integrationID: st.integrationID, instructions: attempt.instructions }
}

export async function signInStatus(integrationID: string, attemptID: string): Promise<{ status: string; message?: string }> {
  const { c, dir } = await client()
  const s = (await c.integration.oauth.status({ integrationID, attemptID, location: { directory: dir } }))?.data
  if (s?.status === "complete") {
    // reconnect every connector on this integration so its tools load now
    for (const a of await statuses()) if (a.integrationID === integrationID) {
      try { await (c.experimental?.mcp || c.mcp).connect({ server: serverName(a.id), location: { directory: dir } }) } catch { /* shows in status */ }
    }
  }
  return { status: s?.status || "unknown", message: s?.message }
}
