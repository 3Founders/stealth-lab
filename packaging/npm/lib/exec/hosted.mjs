// One hosted tool call over Streamable HTTP (initialize -> initialized -> tools/call -> DELETE session),
// the same wire pattern lib/hook.mjs uses for find_ways. The destination is resolved ONLY through
// lib/config.mjs resolveSettings() (--url -> STEALTHLAB_MCP_URL -> ~/.stealthlab/config.json ->
// package.json defaultMcpUrl) and normalizeUrl() refuses plain http:// for any non-loopback host, so
// there is no hardcoded host here and no non-HTTPS egress.
//
// Only two tools are ever called from the executor layer: recommend_models and report_model_run. Their
// arguments are built by select.mjs / evidence.mjs from short, redacted fields -- never a transcript,
// never a task string, never check output.
import { readPackage, resolveSettings } from "../config.mjs";

const ACCEPT = "application/json, text/event-stream";

// Errors carry .transient: true when the right response is "queue and retry later".
export class HostedError extends Error {
  constructor(message, { transient, status } = {}) {
    super(message);
    this.transient = !!transient;
    this.status = status;
  }
}

// The hosted routing tables may not exist yet (pending migrations): a Postgres "undefined table"
// surfaces as text like `relation "model_observations" does not exist` or `UndefinedTableError`.
export const UNDEFINED_TABLE_RE = /undefined[_ ]?table|relation\s+"?[\w.]+"?\s+does not exist/i;

function parseBody(text) {
  const t = String(text || "").trim();
  if (!t) return {};
  if (t.startsWith("{") || t.startsWith("[")) return JSON.parse(t);
  const data = t.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).filter(Boolean);
  for (let i = data.length - 1; i >= 0; i--) {
    try {
      const obj = JSON.parse(data[i]);
      if (obj.result || obj.error) return obj;
    } catch { /* keep looking */ }
  }
  return {};
}

export function hostedSettings(env = process.env) {
  try {
    return resolveSettings({}, env);
  } catch (err) {
    return { url: "", token: "", error: err.message };
  }
}

// Returns the tool's text result. Throws HostedError (transient or permanent).
export async function callHostedTool(name, args, { env = process.env, fetchImpl = globalThis.fetch, timeoutMs = 10000 } = {}) {
  const settings = hostedSettings(env);
  if (settings.error) throw new HostedError(`hosted URL rejected: ${settings.error}`, { transient: true });
  if (!settings.url) throw new HostedError("no hosted MCP URL configured", { transient: true });
  const signal = AbortSignal.timeout(timeoutMs);
  let version = "0";
  try { version = readPackage().version; } catch { /* keep "0" */ }
  const headers = { accept: ACCEPT, "content-type": "application/json", "user-agent": `stealthlab-exec/${version}` };
  if (settings.token) headers.authorization = `Bearer ${settings.token}`;
  const post = async (body, sid, proto) => {
    try {
      return await fetchImpl(settings.url, {
        method: "POST", signal, body: JSON.stringify(body),
        headers: { ...headers, ...(sid ? { "mcp-session-id": sid } : {}), ...(proto ? { "mcp-protocol-version": proto } : {}) },
      });
    } catch (err) {
      throw new HostedError(`hosted MCP unreachable: ${err.cause?.message || err.message}`, { transient: true });
    }
  };
  const check = async (res, what) => {
    if (res.ok) return;
    const text = await res.text().catch(() => "");
    // 5xx, auth, rate limit, not found (endpoint moving) are all "try again later", never a failed run.
    throw new HostedError(`${what} HTTP ${res.status}${text ? `: ${text.slice(0, 200)}` : ""}`, { transient: true, status: res.status });
  };
  const init = await post({
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "stealthlab-exec", version } },
  });
  await check(init, "initialize");
  const sid = init.headers.get("mcp-session-id") || undefined;
  let proto;
  try { proto = parseBody(await init.text()).result?.protocolVersion; } catch { /* optional */ }
  const note = await post({ jsonrpc: "2.0", method: "notifications/initialized" }, sid, proto);
  await note.text().catch(() => "");
  const res = await post({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name, arguments: args } }, sid, proto);
  await check(res, name);
  let body;
  try {
    body = parseBody(await res.text());
  } catch (err) {
    throw new HostedError(`${name}: unparseable response (${err.message})`, { transient: true });
  } finally {
    if (sid) fetchImpl(settings.url, { method: "DELETE", headers: { ...headers, "mcp-session-id": sid } }).catch(() => {});
  }
  if (body.error) throw new HostedError(`${name}: ${body.error.message || "JSON-RPC error"}`, { transient: true });
  const text = (body.result?.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
  if (UNDEFINED_TABLE_RE.test(text)) throw new HostedError(`${name}: server tables not ready (${text.slice(0, 200)})`, { transient: true });
  if (body.result?.isError) throw new HostedError(`${name}: ${text.slice(0, 300) || "tool error"}`, { transient: true });
  if (/^REFUSED\b/.test(text.trim())) throw new HostedError(`${name}: ${text.trim().slice(0, 300)}`, { transient: false });
  return text;
}
